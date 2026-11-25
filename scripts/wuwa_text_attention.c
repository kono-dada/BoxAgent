// Gated DeltaNet prefill uses blocked triangular solves and FP32 recurrence state.
#include <arm_neon.h>
#include <Accelerate/Accelerate.h>
#include <dispatch/dispatch.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { HEADS = 16, WIDTH = 128, PROJECTIONS = 3 * HEADS * WIDTH };

static inline float32x4_t bf16x4(const uint16_t *values) {
    return vreinterpretq_f32_u32(vshll_n_u16(vld1_u16(values), 16));
}

static inline float bf16(const uint16_t value) {
    const uint32_t bits = (uint32_t)value << 16;
    float result;
    memcpy(&result, &bits, sizeof(result));
    return result;
}

enum { MAX_TOKENS = 1088 };

enum { CHUNK = 96 };

static void delta_cpu_chunked(const uint16_t *q, const uint16_t *k, const uint16_t *v,
                       const float *gate, const uint16_t *beta, const float *before,
                       const bool *mask, const size_t length, float *y, float *after) {
    memcpy(after, before, sizeof(float) * HEADS * WIDTH * WIDTH);
    dispatch_apply(HEADS, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^(size_t head) {
        float *state = after + head * WIDTH * WIDTH;
        float keys[CHUNK * WIDTH], queries[CHUNK * WIDTH], scaled_q[CHUNK * WIDTH];
        float updates[CHUNK * 2 * WIDTH], dots[CHUNK * CHUNK], causal[CHUNK * CHUNK];
        float reply[CHUNK * WIDTH], decays[CHUNK], mixes[CHUNK];
        for (size_t start = 0; start < length; start += CHUNK) {
            const int number = (int)(length - start < CHUNK ? length - start : CHUNK);
            float accumulated = 1.f;
            for (int i = 0; i < number; ++i) {
                const size_t t = start + i;
                const size_t offset = t * HEADS + head;
                const bool live = !mask || mask[t];
                const float g = live ? gate[offset] : 1.f;
                const float b = live ? bf16(beta[offset]) : 0.f;
                accumulated *= g;
                decays[i] = g;
                mixes[i] = b;
                const size_t source = offset * WIDTH;
                const int target = i * WIDTH;
                for (int j = 0; j < WIDTH; j += 4) {
                    const float32x4_t key = bf16x4(k + source + j);
                    const float32x4_t query = live ? bf16x4(q + source + j) : vdupq_n_f32(0.f);
                    const float32x4_t value = bf16x4(v + source + j);
                    vst1q_f32(keys + target + j, key);
                    vst1q_f32(queries + target + j, query);
                    vst1q_f32(scaled_q + target + j, vmulq_n_f32(query, accumulated));
                    vst1q_f32(updates + 2*target + j, vmulq_n_f32(value, b));
                    vst1q_f32(updates + 2*target + WIDTH + j, vmulq_n_f32(key, b*accumulated));
                }
            }
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, number, number, WIDTH, 1.f,
                        keys, WIDTH, keys, WIDTH, 0.f, dots, number);
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, number, number, WIDTH, 1.f,
                        queries, WIDTH, keys, WIDTH, 0.f, causal, number);
            for (int i = 0; i < number; ++i) {
                float product = 1.f;
                for (int j = i-1; j >= 0; --j) {
                    product *= decays[j+1];
                    dots[i*number+j] *= product * mixes[i];
                    causal[i*number+j] *= product;
                }
                memset(causal+i*number+i+1, 0, sizeof(float)*(number-i-1));
            }
            cblas_strsm(CblasRowMajor, CblasLeft, CblasLower, CblasNoTrans, CblasUnit,
                        number, 2*WIDTH, 1.f, dots, number, updates, 2*WIDTH);
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, number, WIDTH, WIDTH, -1.f,
                        updates+WIDTH, 2*WIDTH, state, WIDTH, 1.f, updates, 2*WIDTH);
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasTrans, number, WIDTH, WIDTH, 1.f,
                        scaled_q, WIDTH, state, WIDTH, 0.f, reply, WIDTH);
            cblas_sgemm(CblasRowMajor, CblasNoTrans, CblasNoTrans, number, WIDTH, number, 1.f,
                        causal, number, updates, 2*WIDTH, 1.f, reply, WIDTH);
            float product = 1.f;
            for (int i = number-1; i >= 0; --i) {
                const size_t offset = ((start + i) * HEADS + head) * WIDTH;
                memcpy(y + offset, reply + i * WIDTH, WIDTH * sizeof(float));
                for (int j = 0; j < WIDTH; ++j) keys[i*WIDTH+j] *= product;
                product *= decays[i];
            }
            cblas_sgemm(CblasRowMajor, CblasTrans, CblasNoTrans, WIDTH, WIDTH, number, 1.f,
                        updates, 2*WIDTH, keys, WIDTH, accumulated, state, WIDTH);
        }
    });
}

static inline uint16x4_t to_bf16(float32x4_t value) {
    const uint32x4_t bits = vreinterpretq_u32_f32(value);
    const uint32x4_t tie = vandq_u32(vshrq_n_u32(bits, 16), vdupq_n_u32(1));
    const uint32x4_t bias = vaddq_u32(vdupq_n_u32(0x7fff), tie);
    return vshrn_n_u32(vaddq_u32(bits, bias), 16);
}

static inline float round_bf16(float value) {
    return bf16(vget_lane_u16(to_bf16(vdupq_n_f32(value)), 0));
}

static inline float32x4_t fp16x4(const __fp16 *values) {
    return vcvt_f32_f16(vld1_f16(values));
}

static void depthwise_row(const __fp16 *input, const float *weights, size_t t, __fp16 *output) {
    float activated[PROJECTIONS], exp_input[PROJECTIONS];
    for (size_t i = 0; i < PROJECTIONS; i += 4) {
        float32x4_t accum = vdupq_n_f32(0);
        for (size_t lag = 0; lag < 4 && lag <= t; ++lag) {
            const float32x4_t value = fp16x4(input + (t-lag)*PROJECTIONS+i);
            const float32x4_t rounded = vreinterpretq_f32_u32(vshll_n_u16(to_bf16(value),16));
            const float32x4_t coeff = vld1q_f32(weights+(3-lag)*PROJECTIONS+i);
            accum = vfmaq_f32(accum, rounded, coeff);
        }
        const float32x4_t rounded = vreinterpretq_f32_u32(vshll_n_u16(to_bf16(accum),16));
        vst1q_f32(activated+i, rounded);
        vst1q_f32(exp_input+i, vnegq_f32(rounded));
    }
    const int n = PROJECTIONS;
    vvexpf(exp_input, exp_input, &n);
    for (size_t i = 0; i < PROJECTIONS; i += 4) {
        const float32x4_t result = vdivq_f32(vld1q_f32(activated+i),
            vaddq_f32(vdupq_n_f32(1), vld1q_f32(exp_input+i)));
        const float32x4_t rounded = vreinterpretq_f32_u32(vshll_n_u16(to_bf16(result),16));
        vst1_f16(output+i, vcvt_f16_f32(rounded));
    }
}

void boxagent_wuwa_text_depthwise(const __fp16 *input, const float *weights, const size_t length,
                                __fp16 *output) {
    enum { JOB = 16 };
    dispatch_apply((length+JOB-1)/JOB, dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0),
                   ^(size_t job) {
        for (size_t t = job*JOB; t < (job+1)*JOB && t < length; ++t) {
            depthwise_row(input, weights, t, output+t*PROJECTIONS);
        }
    });
}

static inline void normalize_qk(const __fp16 *values, uint16_t *normalized, float scale) {
    float32x4_t values_f[WIDTH/4];
    float32x4_t squares[4] = {vdupq_n_f32(0), vdupq_n_f32(0), vdupq_n_f32(0), vdupq_n_f32(0)};
    for (int i = 0; i < WIDTH/4; ++i) {
        values_f[i] = vreinterpretq_f32_u32(vshll_n_u16(to_bf16(fp16x4(values + 4*i)), 16));
        squares[i%4] = vfmaq_f32(squares[i%4], values_f[i], values_f[i]);
    }
    const float sum = vaddvq_f32(vaddq_f32(vaddq_f32(squares[0], squares[1]),
                                           vaddq_f32(squares[2], squares[3])));
    const float factor = 1.f/sqrtf(sum/WIDTH + 1e-6f);
    for (int i = 0; i < WIDTH/4; ++i) {
        const float32x4_t normed = vmulq_n_f32(values_f[i], factor);
        const float32x4_t rounded = vreinterpretq_f32_u32(vshll_n_u16(to_bf16(normed), 16));
        vst1_u16(normalized + 4*i, to_bf16(vmulq_n_f32(rounded, scale)));
    }
}

int boxagent_wuwa_text_linear(const __fp16 *filtered, const __fp16 *extra, const float *decay,
                             const float *dt, const float *norm, const size_t length,
                             float *after, __fp16 *output) {
    enum { GATES = HEADS * WIDTH + 2 * HEADS, JOB = 32 };
    if (length == 0 || length > MAX_TOKENS) return 1;
    const size_t vector = length * HEADS * WIDTH;
    const size_t gates = length * HEADS;
    uint16_t *const packed = malloc((3*vector + gates) * sizeof(uint16_t));
    float *const scratch = malloc((gates + vector + HEADS * WIDTH * WIDTH) * sizeof(float));
    if (!packed || !scratch) {
        free(packed);
        free(scratch);
        return 1;
    }
    uint16_t *q = packed, *k = q + vector, *v = k + vector, *beta = v + vector;
    float *g = scratch, *result = g + gates, *initial = result + vector;
    memset(initial, 0, HEADS * WIDTH * WIDTH * sizeof(float));
    const size_t jobs = (length + JOB - 1)/JOB;
    dispatch_queue_t queue = dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0);
    dispatch_apply(jobs, queue, ^(size_t job) {
        for (size_t t = job*JOB; t < (job+1)*JOB && t < length; ++t) {
            const __fp16 *current = filtered + t*PROJECTIONS;
            const __fp16 *parameters = extra + t*GATES + HEADS*WIDTH;
            for (size_t head = 0; head < HEADS; ++head) {
                const size_t base = (t*HEADS+head)*WIDTH;
                const size_t slot = t*HEADS+head;
                const float a = round_bf16((float)parameters[HEADS+head]);
                const float logits = round_bf16(a + dt[head]);
                const float rate = logits > 20.f ? logits : log1pf(expf(logits));
                g[slot] = expf(-decay[head] * rate);
                const float probability = 1.f / (1.f + expf(-round_bf16((float)parameters[head])));
                beta[slot] = vget_lane_u16(to_bf16(vdupq_n_f32(probability)), 0);
                normalize_qk(current + head*WIDTH, q + base, 1.f/WIDTH);
                normalize_qk(current + (HEADS+head)*WIDTH, k + base, 1.f/sqrtf(WIDTH));
                for (int j = 0; j < WIDTH; j += 4)
                    vst1_u16(v + base + j, to_bf16(fp16x4(current + (2*HEADS+head)*WIDTH+j)));
            }
        }
    });
    delta_cpu_chunked(q, k, v, g, beta, initial, NULL, length, result, after);
    dispatch_apply(jobs, queue, ^(size_t job) {
        for (size_t t = job*JOB; t < (job+1)*JOB && t < length; ++t) {
            for (size_t head = 0; head < HEADS; ++head) {
                const size_t base = (t*HEADS+head)*WIDTH;
                const __fp16 *z = extra + t*GATES + head*WIDTH;
                float32x4_t values[WIDTH/4];
                float exponents[WIDTH];
                float32x4_t squares[4] = {vdupq_n_f32(0), vdupq_n_f32(0), vdupq_n_f32(0), vdupq_n_f32(0)};
                for (int i = 0; i < WIDTH/4; ++i) {
                    const uint16x4_t bf = to_bf16(vld1q_f32(result+base+4*i));
                    values[i] = vreinterpretq_f32_u32(vshll_n_u16(bf, 16));
                    squares[i%4] = vfmaq_f32(squares[i%4], values[i], values[i]);
                    const uint16x4_t raw = to_bf16(fp16x4(z + 4*i));
                    vst1q_f32(exponents+4*i, vnegq_f32(vreinterpretq_f32_u32(vshll_n_u16(raw, 16))));
                }
                const float sum = vaddvq_f32(vaddq_f32(vaddq_f32(squares[0], squares[1]),
                                                       vaddq_f32(squares[2], squares[3])));
                const float scale = 1.f/sqrtf(sum/WIDTH + 1e-6f);
                const int count = WIDTH;
                vvexpf(exponents, exponents, &count);
                for (int i = 0; i < WIDTH/4; ++i) {
                    const float32x4_t gain = vmulq_n_f32(vld1q_f32(norm+4*i), scale);
                    const uint16x4_t normalized = to_bf16(vmulq_f32(values[i], gain));
                    const uint16x4_t raw = to_bf16(fp16x4(z+4*i));
                    const float32x4_t zval = vreinterpretq_f32_u32(vshll_n_u16(raw, 16));
                    const float32x4_t silu = vdivq_f32(zval, vaddq_f32(vdupq_n_f32(1),
                                                                      vld1q_f32(exponents+4*i)));
                    const float32x4_t x = vreinterpretq_f32_u32(vshll_n_u16(normalized, 16));
                    const uint16x4_t rounded = to_bf16(vmulq_f32(x, silu));
                    vst1_f16(output+base+4*i,
                             vcvt_f16_f32(vreinterpretq_f32_u32(vshll_n_u16(rounded, 16))));
                }
            }
        }
    });
    free(packed);
    free(scratch);
    return 0;
}
