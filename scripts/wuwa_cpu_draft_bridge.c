// Thin bridge for the pinned CPU-only llama.cpp build used by the Wuwa draft backend.
#include "llama.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#define GGML_COMMON_DECL_C
#include "ggml-common.h"
#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/IOKitLib.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct draft {
    struct llama_model *model;
    struct llama_context *context;
    int32_t vocabulary;
};

int boxagent_draft_gpu_load(void) {
    io_service_t device = IOServiceGetMatchingService(
        kIOMainPortDefault, IOServiceMatching("IOAccelerator"));
    if (!device) return -1;
    CFTypeRef stats = IORegistryEntryCreateCFProperty(
        device, CFSTR("PerformanceStatistics"), kCFAllocatorDefault, 0);
    IOObjectRelease(device);
    if (!stats) return -1;
    int load = -1;
    if (CFGetTypeID(stats) == CFDictionaryGetTypeID()) {
        CFTypeRef value = CFDictionaryGetValue(stats, CFSTR("Device Utilization %"));
        if (value && CFGetTypeID(value) == CFNumberGetTypeID()) {
            CFNumberGetValue(value, kCFNumberIntType, &load);
        }
    }
    CFRelease(stats);
    return load >= 0 && load <= 100 ? load : -1;
}

static void bridge_log(enum ggml_log_level level, const char *message, void *unused) {
    (void)unused;
    if (level == GGML_LOG_LEVEL_ERROR) fputs(message, stderr);
}

void boxagent_draft_close(struct draft *draft) {
    if (!draft) return;
    if (draft->context) llama_free(draft->context);
    if (draft->model) llama_model_free(draft->model);
    free(draft);
}

static float *decode_token(struct draft *draft, llama_pos position, llama_token token) {
    llama_seq_id sequence = 0;
    llama_seq_id *sequences = &sequence;
    int32_t n_sequences = 1;
    int8_t request_logits = 1;
    struct llama_batch batch = {
        .n_tokens = 1, .token = &token, .pos = &position, .n_seq_id = &n_sequences,
        .seq_id = &sequences, .logits = &request_logits,
    };
    if (llama_decode(draft->context, batch) != 0) return NULL;
    return llama_get_logits_ith(draft->context, 0);
}

static bool apply_penalty(struct draft *draft, float *logits, float penalty,
                          const llama_token *recent, size_t count) {
    // The caller passes each recent token ID once. Only the sampled row is modified.
    for (size_t i = 0; i < count; ++i) {
        const llama_token id = recent[i];
        if (id < 0 || id >= draft->vocabulary) return false;
        const float score = logits[id];
        logits[id] = score < 0 ? score * penalty : score / penalty;
    }
    return true;
}

int boxagent_draft_token_penalized(struct draft *draft, llama_pos position, llama_token token,
                                  float penalty, const llama_token *recent, size_t count) {
    float *logits = decode_token(draft, position, token);
    if (!logits) return -1;
    if (!apply_penalty(draft, logits, penalty, recent, count)) return -1;
    int32_t best = 0;
    for (int32_t i = 1; i < draft->vocabulary; ++i) {
        if (logits[i] > logits[best]) best = i;
    }
    return best;
}

int boxagent_draft_token(struct draft *draft, llama_pos position, llama_token token) {
    return boxagent_draft_token_penalized(draft, position, token, 1.0f, NULL, 0);
}

// Collect the actual CPU projection input and its highest scoring vocabulary rows.
// The root of the min-heap bounds every omitted score, including ties.
int boxagent_draft_ranked_token(struct draft *draft, llama_pos position, llama_token token,
                                   float penalty, const llama_token *recent, size_t count,
                                   float *activation, int32_t *ids, int capacity, float *cutoff) {
    if (capacity != 128) return -1;
    float *logits = decode_token(draft, position, token);
    const float *hidden = llama_get_embeddings_ith(draft->context, 0);
    if (!hidden || !logits || !apply_penalty(draft, logits, penalty, recent, count)) return -1;
    block_q8_1 blocks[1024 / QK8_1];
    ggml_get_type_traits_cpu(GGML_TYPE_Q8_1)->from_float(hidden, blocks, 1024);
    // Q4_1 uses a separately rounded sum for its affine bias. The two Q8_1
    // blocks here share one original 64-channel MLX scale and bias.
    memset(activation + 1024, 0, 16 * sizeof(float));
    for (int group = 0; group < 1024 / QK8_1; ++group) {
        const float scale = ggml_fp16_to_fp32(blocks[group].d);
        int sum = 0;
        for (int j = 0; j < QK8_1; ++j) {
            sum += blocks[group].qs[j];
            activation[group * QK8_1 + j] = scale * blocks[group].qs[j];
        }
        activation[1024 + group / 2] += ggml_fp16_to_fp32(blocks[group].s) - scale * sum;
    }
    float scores[128];
    for (int32_t id = 0; id < draft->vocabulary; ++id) {
        const float score = logits[id];
        if (!isfinite(score)) return -1;
        int slot;
        if (id < capacity) {
            slot = id;
            while (slot > 0) {
                int parent = (slot - 1) / 2;
                if (scores[parent] <= score) break;
                scores[slot] = scores[parent];
                ids[slot] = ids[parent];
                slot = parent;
            }
        } else {
            if (score <= scores[0]) continue;
            slot = 0;
            while (2 * slot + 1 < capacity) {
                int child = 2 * slot + 1;
                if (child + 1 < capacity && scores[child + 1] < scores[child]) ++child;
                if (scores[child] >= score) break;
                scores[slot] = scores[child];
                ids[slot] = ids[child];
                slot = child;
            }
        }
        scores[slot] = score;
        ids[slot] = id;
    }
    *cutoff = scores[0];
    int best = 0;
    for (int i = 1; i < capacity; ++i) {
        if (scores[i] > scores[best] || (scores[i] == scores[best] && ids[i] < ids[best]))
            best = i;
    }
    return ids[best];
}

static struct draft *open_model(const char *model_path, bool candidates) {
    if (strncmp(ggml_commit(), "164ab018f", 9) != 0) {
        return NULL;
    }
    llama_log_set(bridge_log, NULL);
    llama_backend_init();
    ggml_backend_dev_t devices[] = { NULL };
    struct llama_model_params model = llama_model_default_params();
    model.n_gpu_layers = 0;
    model.devices = devices;
    struct draft *draft = calloc(1, sizeof(*draft));
    if (!draft) return NULL;
    draft->model = llama_model_load_from_file(model_path, model);
    if (!draft->model) goto failure;
    if (llama_model_n_layer(draft->model) != 24 || llama_model_n_embd(draft->model) != 1024) {
        goto failure;
    }
    draft->vocabulary = llama_vocab_n_tokens(llama_model_get_vocab(draft->model));
    if (draft->vocabulary != 248320) goto failure;

    struct llama_context_params context = llama_context_default_params();
    context.n_ctx = 1536;
    context.n_batch = 32;
    context.n_ubatch = 32;
    context.n_threads = 6;
    context.n_threads_batch = 6;
    context.offload_kqv = false;
    context.op_offload = false;
    context.no_perf = true;
    context.embeddings = candidates;
    if (candidates) context.pooling_type = LLAMA_POOLING_TYPE_NONE;
    context.flash_attn_type = LLAMA_FLASH_ATTN_TYPE_ENABLED;
    context.type_k = GGML_TYPE_F16;
    context.type_v = GGML_TYPE_F16;
    draft->context = llama_init_from_model(draft->model, context);
    if (!draft->context || boxagent_draft_token(draft, 0, 0) < 0) goto failure;
    return draft;

failure:
    boxagent_draft_close(draft);
    return NULL;
}

struct draft *boxagent_draft_open(const char *model_path) {
    return open_model(model_path, false);
}

struct draft *boxagent_draft_open_candidates(const char *model_path) {
    return open_model(model_path, true);
}

size_t boxagent_draft_restore(struct draft *draft, const void *state, size_t size) {
    llama_memory_clear(llama_get_memory(draft->context), false);
    return llama_state_seq_set_data(draft->context, state, size, 0);
}
