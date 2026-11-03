#include <stddef.h>
#include <stdint.h>
#include <string.h>

#if defined(__ARM_NEON) || defined(__ARM_NEON__)
#include <arm_neon.h>
#define SR_LUT_HAS_NEON 1
#else
#define SR_LUT_HAS_NEON 0
#endif

#if defined(_WIN32)
#define SR_LUT_EXPORT __declspec(dllexport)
#else
#define SR_LUT_EXPORT __attribute__((visibility("default")))
#endif

static inline uint8_t clamp_u8(int value) {
    if (value < 0) {
        return 0;
    }
    if (value > 255) {
        return 255;
    }
    return (uint8_t)value;
}

static inline uint8_t rgb_to_y(uint8_t r, uint8_t g, uint8_t b) {
    return clamp_u8(((66 * r + 129 * g + 25 * b + 128) >> 8) + 16);
}

static inline uint8_t rgb_to_u(uint8_t r, uint8_t g, uint8_t b) {
    return clamp_u8(((-38 * r - 74 * g + 112 * b + 128) >> 8) + 128);
}

static inline uint8_t rgb_to_v(uint8_t r, uint8_t g, uint8_t b) {
    return clamp_u8(((112 * r - 94 * g - 18 * b + 128) >> 8) + 128);
}

static void pack_bgrx_scalar(
    const uint8_t *source,
    int width,
    int height,
    int source_stride,
    uint8_t *destination,
    int start_x
) {
    for (int row = 0; row < height; ++row) {
        const uint8_t *input = source + (size_t)row * (size_t)source_stride;
        uint8_t *output = destination + (size_t)row * (size_t)width * 4U;
        for (int column = start_x; column < width; ++column) {
            output[4 * column] = input[3 * column + 2];
            output[4 * column + 1] = input[3 * column + 1];
            output[4 * column + 2] = input[3 * column];
            output[4 * column + 3] = 0;
        }
    }
}

static void convert_scalar(
    const uint8_t *source,
    int width,
    int height,
    int source_stride,
    uint8_t *y_plane,
    uint8_t *u_plane,
    uint8_t *v_plane,
    int start_x
) {
    for (int row = 0; row < height; row += 2) {
        const uint8_t *top = source + (size_t)row * (size_t)source_stride;
        const uint8_t *bottom = top + source_stride;
        uint8_t *y_top = y_plane + (size_t)row * (size_t)width;
        uint8_t *y_bottom = y_top + width;
        uint8_t *u_row = u_plane + (size_t)(row / 2) * (size_t)(width / 2);
        uint8_t *v_row = v_plane + (size_t)(row / 2) * (size_t)(width / 2);

        for (int column = start_x; column < width; column += 2) {
            const uint8_t *p00 = top + 4 * column;
            const uint8_t *p01 = p00 + 4;
            const uint8_t *p10 = bottom + 4 * column;
            const uint8_t *p11 = p10 + 4;
            y_top[column] = rgb_to_y(p00[0], p00[1], p00[2]);
            y_top[column + 1] = rgb_to_y(p01[0], p01[1], p01[2]);
            y_bottom[column] = rgb_to_y(p10[0], p10[1], p10[2]);
            y_bottom[column + 1] = rgb_to_y(p11[0], p11[1], p11[2]);

            int chroma_column = column / 2;
            u_row[chroma_column] = rgb_to_u(p00[0], p00[1], p00[2]);
            v_row[chroma_column] = rgb_to_v(p00[0], p00[1], p00[2]);
        }
    }
}

#if SR_LUT_HAS_NEON
static void pack_bgrx_neon(
    const uint8_t *source,
    int width,
    int height,
    int source_stride,
    uint8_t *destination
) {
    int vector_width = width & ~7;
    for (int row = 0; row < height; ++row) {
        const uint8_t *input = source + (size_t)row * (size_t)source_stride;
        uint8_t *output = destination + (size_t)row * (size_t)width * 4U;
        for (int column = 0; column < vector_width; column += 8) {
            uint8x8x3_t bgr = vld3_u8(input + 3 * column);
            uint8x8x4_t rgbx;
            rgbx.val[0] = bgr.val[2];
            rgbx.val[1] = bgr.val[1];
            rgbx.val[2] = bgr.val[0];
            rgbx.val[3] = vdup_n_u8(0);
            vst4_u8(output + 4 * column, rgbx);
        }
    }
    if (vector_width != width) {
        pack_bgrx_scalar(
            source,
            width,
            height,
            source_stride,
            destination,
            vector_width
        );
    }
}

static inline uint8x8_t neon_y(
    uint8x8_t red,
    uint8x8_t green,
    uint8x8_t blue
) {
    uint16x8_t value = vmull_u8(red, vdup_n_u8(66));
    value = vmlal_u8(value, green, vdup_n_u8(129));
    value = vmlal_u8(value, blue, vdup_n_u8(25));
    value = vaddq_u16(value, vdupq_n_u16(128));
    value = vshrq_n_u16(value, 8);
    return vqadd_u8(vqmovn_u16(value), vdup_n_u8(16));
}

static inline uint8x8_t neon_chroma(
    uint16x4_t red,
    uint16x4_t green,
    uint16x4_t blue,
    int red_coefficient,
    int green_coefficient,
    int blue_coefficient
) {
    int16x4_t signed_red = vreinterpret_s16_u16(red);
    int16x4_t signed_green = vreinterpret_s16_u16(green);
    int16x4_t signed_blue = vreinterpret_s16_u16(blue);
    int32x4_t value = vmull_n_s16(signed_red, (int16_t)red_coefficient);
    value = vmlal_n_s16(value, signed_green, (int16_t)green_coefficient);
    value = vmlal_n_s16(value, signed_blue, (int16_t)blue_coefficient);
    value = vaddq_s32(value, vdupq_n_s32(128));
    value = vshrq_n_s32(value, 8);
    value = vaddq_s32(value, vdupq_n_s32(128));
    return vqmovun_s16(vcombine_s16(vqmovn_s32(value), vdup_n_s16(0)));
}

static void store_low_four(uint8_t *destination, uint8x8_t value) {
    uint32_t packed = vget_lane_u32(vreinterpret_u32_u8(value), 0);
    memcpy(destination, &packed, sizeof(packed));
}

static uint16x4_t widen_even_low_four(uint8x8_t value) {
    uint8x8x2_t separated = vuzp_u8(value, value);
    return vget_low_u16(vmovl_u8(separated.val[0]));
}

static int convert_neon(
    const uint8_t *source,
    int width,
    int height,
    int source_stride,
    uint8_t *y_plane,
    uint8_t *u_plane,
    uint8_t *v_plane
) {
    int vector_width = width & ~7;
    for (int row = 0; row < height; row += 2) {
        const uint8_t *top = source + (size_t)row * (size_t)source_stride;
        const uint8_t *bottom = top + source_stride;
        uint8_t *y_top = y_plane + (size_t)row * (size_t)width;
        uint8_t *y_bottom = y_top + width;
        uint8_t *u_row = u_plane + (size_t)(row / 2) * (size_t)(width / 2);
        uint8_t *v_row = v_plane + (size_t)(row / 2) * (size_t)(width / 2);

        for (int column = 0; column < vector_width; column += 8) {
            uint8x8x4_t top_pixels = vld4_u8(top + 4 * column);
            uint8x8x4_t bottom_pixels = vld4_u8(bottom + 4 * column);
            vst1_u8(
                y_top + column,
                neon_y(top_pixels.val[0], top_pixels.val[1], top_pixels.val[2])
            );
            vst1_u8(
                y_bottom + column,
                neon_y(
                    bottom_pixels.val[0],
                    bottom_pixels.val[1],
                    bottom_pixels.val[2]
                )
            );

            uint16x4_t chroma_r = widen_even_low_four(top_pixels.val[0]);
            uint16x4_t chroma_g = widen_even_low_four(top_pixels.val[1]);
            uint16x4_t chroma_b = widen_even_low_four(top_pixels.val[2]);
            store_low_four(
                u_row + column / 2,
                neon_chroma(chroma_r, chroma_g, chroma_b, -38, -74, 112)
            );
            store_low_four(
                v_row + column / 2,
                neon_chroma(chroma_r, chroma_g, chroma_b, 112, -94, -18)
            );
        }
    }
    if (vector_width != width) {
        convert_scalar(
            source,
            width,
            height,
            source_stride,
            y_plane,
            u_plane,
            v_plane,
            vector_width
        );
    }
    return 0;
}
#endif

SR_LUT_EXPORT int sr_lut_bgr_to_rgbx(
    const uint8_t *source,
    int width,
    int height,
    int source_stride,
    uint8_t *destination,
    size_t destination_size
) {
    if (
        source == NULL || destination == NULL || width <= 0 || height <= 0 ||
        source_stride < width * 3
    ) {
        return 1;
    }
    if (destination_size != (size_t)width * (size_t)height * 4U) {
        return 2;
    }
#if SR_LUT_HAS_NEON
    pack_bgrx_neon(source, width, height, source_stride, destination);
#else
    pack_bgrx_scalar(source, width, height, source_stride, destination, 0);
#endif
    return 0;
}

SR_LUT_EXPORT const char *sr_lut_input_pack_backend(void) {
#if SR_LUT_HAS_NEON
    return "native-neon-bgr-rgbx";
#else
    return "native-scalar-bgr-rgbx";
#endif
}

SR_LUT_EXPORT int sr_lut_rgba_to_i420(
    const uint8_t *source,
    int width,
    int height,
    int source_stride,
    uint8_t *destination,
    size_t destination_size
) {
    if (
        source == NULL || destination == NULL || width <= 0 || height <= 0 ||
        (width & 1) != 0 || (height & 1) != 0 || source_stride < width * 4
    ) {
        return 1;
    }
    size_t y_size = (size_t)width * (size_t)height;
    size_t expected_size = y_size + y_size / 2;
    if (destination_size != expected_size) {
        return 2;
    }
    uint8_t *y_plane = destination;
    uint8_t *u_plane = destination + y_size;
    uint8_t *v_plane = u_plane + y_size / 4;
#if SR_LUT_HAS_NEON
    return convert_neon(
        source,
        width,
        height,
        source_stride,
        y_plane,
        u_plane,
        v_plane
    );
#else
    convert_scalar(
        source,
        width,
        height,
        source_stride,
        y_plane,
        u_plane,
        v_plane,
        0
    );
    return 0;
#endif
}

SR_LUT_EXPORT const char *sr_lut_yuv420_backend(void) {
#if SR_LUT_HAS_NEON
    return "native-neon-bt601-i420";
#else
    return "native-scalar-bt601-i420";
#endif
}
