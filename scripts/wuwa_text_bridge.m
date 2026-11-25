#import <CoreML/CoreML.h>
#import <Foundation/Foundation.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    const char *name;
    void *data;
    size_t elements;
} TensorBuffer;

static NSArray<NSNumber *> *contiguousStrides(NSArray<NSNumber *> *shape) {
    NSMutableArray<NSNumber *> *strides = [NSMutableArray arrayWithCapacity:shape.count];
    size_t stride = 1;
    for (NSUInteger i = shape.count; i > 0; --i) {
        [strides insertObject:@(stride) atIndex:0];
        stride *= shape[i - 1].unsignedIntegerValue;
    }
    return strides;
}

static MLMultiArray *wrapBuffer(MLFeatureDescription *feature, TensorBuffer buffer) {
    MLMultiArrayConstraint *constraint = feature.multiArrayConstraint;
    if (!buffer.data || constraint.dataType != MLMultiArrayDataTypeFloat16 ||
        constraint.shape.count == 0) {
        return nil;
    }
    size_t count = 1;
    for (NSNumber *dimension in constraint.shape) {
        count *= dimension.unsignedIntegerValue;
    }
    if (count != buffer.elements) {
        return nil;
    }
    return [[MLMultiArray alloc] initWithDataPointer:buffer.data shape:constraint.shape
        dataType:constraint.dataType strides:contiguousStrides(constraint.shape)
        deallocator:nil error:nil];
}

static BOOL copyResult(MLMultiArray *source, MLMultiArray *target) {
    if (!source || source.dataType != target.dataType || ![source.shape isEqual:target.shape]) {
        return NO;
    }
    if (source.dataPointer == target.dataPointer) {
        return YES;
    }
    if ([source.strides isEqual:target.strides]) {
        memcpy(target.dataPointer, source.dataPointer, target.count * sizeof(uint16_t));
        return YES;
    }
    const NSUInteger dimensions = source.shape.count;
    const size_t rowWidth = source.shape.lastObject.unsignedIntegerValue;
    if (source.strides.lastObject.unsignedIntegerValue != 1 || rowWidth == 0) {
        return NO;
    }
    const uint16_t *data = source.dataPointer;
    uint16_t *dest = target.dataPointer;
    size_t rows = target.count / rowWidth;
    for (size_t row = 0; row < rows; ++row) {
        size_t index = row, offset = 0;
        for (NSUInteger d = dimensions - 1; d > 0; --d) {
            const size_t width = source.shape[d - 1].unsignedIntegerValue;
            offset += (index % width) * source.strides[d - 1].unsignedIntegerValue;
            index /= width;
        }
        memcpy(dest + row * rowWidth, data + offset, rowWidth * sizeof(*data));
    }
    return YES;
}

void *boxagent_wuwa_text_open(const char *path) {
    @autoreleasepool {
        NSString *filename = [NSString stringWithUTF8String:path];
        if (!filename) {
            return NULL;
        }
        MLModelConfiguration *configuration = [[MLModelConfiguration alloc] init];
        configuration.computeUnits = MLComputeUnitsCPUAndNeuralEngine;
        NSError *error = nil;
        MLModel *model = [MLModel modelWithContentsOfURL:[NSURL fileURLWithPath:filename]
            configuration:configuration error:&error];
        if (!model) {
            fprintf(stderr, "Wuwa text Core ML: %s\n", error.localizedDescription.UTF8String);
            return NULL;
        }
        return (__bridge_retained void *)model;
    }
}

int boxagent_wuwa_text_predict(void *handle, const TensorBuffer *inputs, size_t inputCount,
                              const TensorBuffer *outputs, size_t outputCount) {
    @autoreleasepool {
        MLModel *model = (__bridge MLModel *)handle;
        MLModelDescription *description = model.modelDescription;
        if (description.inputDescriptionsByName.count != inputCount ||
            description.outputDescriptionsByName.count != outputCount) {
            return 1;
        }
        NSMutableDictionary<NSString *, MLMultiArray *> *values = [NSMutableDictionary dictionary];
        NSMutableDictionary<NSString *, MLMultiArray *> *backings = [NSMutableDictionary dictionary];
        for (size_t i = 0; i < inputCount; ++i) {
            NSString *name = [NSString stringWithUTF8String:inputs[i].name];
            MLMultiArray *value = wrapBuffer(description.inputDescriptionsByName[name], inputs[i]);
            if (!value) return 1;
            values[name] = value;
        }
        for (size_t i = 0; i < outputCount; ++i) {
            NSString *name = [NSString stringWithUTF8String:outputs[i].name];
            MLMultiArray *value = wrapBuffer(description.outputDescriptionsByName[name], outputs[i]);
            if (!value) return 1;
            backings[name] = value;
        }
        NSError *error = nil;
        MLDictionaryFeatureProvider *requests = [[MLDictionaryFeatureProvider alloc]
            initWithDictionary:values error:&error];
        if (!requests) return 1;
        MLPredictionOptions *options = [[MLPredictionOptions alloc] init];
        options.outputBackings = backings;
        id<MLFeatureProvider> result = [model predictionFromFeatures:requests options:options error:&error];
        if (!result) {
            fprintf(stderr, "Wuwa text Core ML: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }
        for (NSString *name in backings) {
            if (!copyResult([result featureValueForName:name].multiArrayValue, backings[name])) {
                return 1;
            }
        }
        return 0;
    }
}

void boxagent_wuwa_text_close(void *handle) {
    CFRelease(handle);
}
