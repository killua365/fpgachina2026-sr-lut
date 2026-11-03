/* CPU-only candidate: contiguous loads/table shuffle, full X-byte validation. */
#include <stddef.h>
#include <stdint.h>
#if defined(__ARM_NEON)
#include <arm_neon.h>
#endif
#ifndef HDMI_PREFETCH
#define HDMI_PREFETCH 256
#endif
#define PREFETCH_READ(p) __builtin_prefetch((p) + HDMI_PREFETCH, 0, 0)

/* Same-frame x4 wipe: read/check every SR X byte, write each output once.
 * A cut may lie inside a 4-pixel nearest block. All stores remain in bounds. */
int hdmi_wipe4_checked(const uint8_t *sr, size_t sr_stride,
                      const uint8_t *src, size_t src_stride,
                      uint8_t *dst, size_t dst_stride,
                      size_t sw, size_t sh, size_t cut) {
    if (!sw || !sh || sw > 1024 || sh > 1024 || cut > sw*4 ||
        sr_stride < sw*16 || dst_stride < sw*16 || src_stride < sw*3) return -1;
    unsigned bad = 0;
#if defined(__ARM_NEON)
    uint32x4_t bad4 = vdupq_n_u32(0), bad1=bad4, bad2=bad4, bad3=bad4;
#endif
    for (size_t y=0; y<sh*4; ++y) {
        const uint8_t *s=sr+y*sr_stride, *base=src+(y/4)*src_stride;
        uint8_t *d=dst+y*dst_stride;
        size_t x=0;
#if defined(__ARM_NEON)
        for (; x+16<=cut; x+=16) {
            PREFETCH_READ(s+4*x);
            bad4=vorrq_u32(bad4,vld1q_u32((const uint32_t *)(s+4*x)));
            bad1=vorrq_u32(bad1,vld1q_u32((const uint32_t *)(s+4*x+16)));
            bad2=vorrq_u32(bad2,vld1q_u32((const uint32_t *)(s+4*x+32)));
            bad3=vorrq_u32(bad3,vld1q_u32((const uint32_t *)(s+4*x+48)));
            for (unsigned n=0; n<4; ++n) {
                const uint8_t *p=base+3*(x/4+n);
                uint32_t rgb=(uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16);
                vst1q_u32((uint32_t *)(d+4*x+16*n),vdupq_n_u32(rgb));
            }
        }
        for (; x+4<=cut; x+=4) {
            PREFETCH_READ(s+4*x);
            bad4=vorrq_u32(bad4,vld1q_u32((const uint32_t *)(s+4*x)));
            const uint8_t *p=base+3*(x/4);
            uint32_t rgb=(uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16);
            vst1q_u32((uint32_t *)(d+4*x),vdupq_n_u32(rgb));
        }
#endif
        for (; x<cut; ++x) {
            bad|=s[4*x+3];
            const uint8_t *p=base+3*(x/4);
            d[4*x]=p[0];d[4*x+1]=p[1];d[4*x+2]=p[2];d[4*x+3]=0;
        }
#if defined(__ARM_NEON)
        for (; x+16<=sw*4; x+=16) {
            PREFETCH_READ(s+4*x);
            uint32x4_t a=vld1q_u32((const uint32_t *)(s+4*x));
            uint32x4_t b=vld1q_u32((const uint32_t *)(s+4*x+16));
            uint32x4_t c=vld1q_u32((const uint32_t *)(s+4*x+32));
            uint32x4_t e=vld1q_u32((const uint32_t *)(s+4*x+48));
            bad4=vorrq_u32(bad4,a);bad1=vorrq_u32(bad1,b);
            bad2=vorrq_u32(bad2,c);bad3=vorrq_u32(bad3,e);
            const uint32x4_t mask=vdupq_n_u32(0x00ffffff);
            vst1q_u32((uint32_t *)(d+4*x),vandq_u32(a,mask));
            vst1q_u32((uint32_t *)(d+4*x+16),vandq_u32(b,mask));
            vst1q_u32((uint32_t *)(d+4*x+32),vandq_u32(c,mask));
            vst1q_u32((uint32_t *)(d+4*x+48),vandq_u32(e,mask));
        }
        for (; x+4<=sw*4; x+=4) {
            PREFETCH_READ(s+4*x);
            uint32x4_t v=vld1q_u32((const uint32_t *)(s+4*x));
            bad4=vorrq_u32(bad4,v);
            vst1q_u32((uint32_t *)(d+4*x),vandq_u32(v,vdupq_n_u32(0x00ffffff)));
        }
#endif
        for (; x<sw*4; ++x) {
            bad|=s[4*x+3];
            d[4*x]=s[4*x];d[4*x+1]=s[4*x+1];d[4*x+2]=s[4*x+2];d[4*x+3]=0;
        }
    }
#if defined(__ARM_NEON)
    bad4=vorrq_u32(vorrq_u32(bad4,bad1),vorrq_u32(bad2,bad3));
    uint32_t values[4];vst1q_u32(values,vshrq_n_u32(bad4,24));
    bad|=values[0]|values[1]|values[2]|values[3];
#endif
    return bad ? 1 : 0;
}

/* BT.601 limited-range YUYV, with the same 20-bit integer coefficients and
 * rounding as OpenCV 4.5.4 COLOR_YUV2BGR_YUY2. Clamp before area resampling. */
static uint8_t clip_byte(int value) {
    return value < 0 ? 0 : value > 255 ? 255 : (uint8_t)value;
}

int hdmi_yuyv_bgr(const uint8_t *src, size_t src_stride, uint8_t *dst,
                   size_t dst_stride, size_t width, size_t height) {
    if (!width || width % 2 || width > 4096 || !height || height > 4096
            || src_stride < width*2 || dst_stride < width*3) return -1;
    for(size_t y=0;y<height;++y) {
        const uint8_t *s=src+y*src_stride;
        uint8_t *d=dst+y*dst_stride;
        size_t x=0;
#if defined(__ARM_NEON)
        static const uint8_t ui[8]={0,0,2,2,4,4,6,6}, vi[8]={1,1,3,3,5,5,7,7};
        for(;x+8<=width;x+=8) {
            uint8x8x2_t in=vld2_u8(s+2*x);
            uint16x8_t yy=vmovl_u8(vqsub_u8(in.val[0],vdup_n_u8(16)));
            int16x8_t u=vsubq_s16(vreinterpretq_s16_u16(vmovl_u8(vtbl1_u8(in.val[1],vld1_u8(ui)))),vdupq_n_s16(128));
            int16x8_t v=vsubq_s16(vreinterpretq_s16_u16(vmovl_u8(vtbl1_u8(in.val[1],vld1_u8(vi)))),vdupq_n_s16(128));
            int32x4_t y0=vmlaq_n_s32(vdupq_n_s32(1<<19),vreinterpretq_s32_u32(vmovl_u16(vget_low_u16(yy))),1220542);
            int32x4_t y1=vmlaq_n_s32(vdupq_n_s32(1<<19),vreinterpretq_s32_u32(vmovl_u16(vget_high_u16(yy))),1220542);
            int32x4_t u0=vmovl_s16(vget_low_s16(u)),u1=vmovl_s16(vget_high_s16(u));
            int32x4_t v0=vmovl_s16(vget_low_s16(v)),v1=vmovl_s16(vget_high_s16(v));
            uint8x8x3_t out;
            int32x4_t b0=vmlaq_n_s32(y0,u0,2116026),b1=vmlaq_n_s32(y1,u1,2116026);
            out.val[0]=vqmovn_u16(vcombine_u16(vqmovun_s32(vshrq_n_s32(b0,20)),vqmovun_s32(vshrq_n_s32(b1,20))));
            int32x4_t g0=vmlaq_n_s32(vmlaq_n_s32(y0,u0,-409993),v0,-852492);
            int32x4_t g1=vmlaq_n_s32(vmlaq_n_s32(y1,u1,-409993),v1,-852492);
            out.val[1]=vqmovn_u16(vcombine_u16(vqmovun_s32(vshrq_n_s32(g0,20)),vqmovun_s32(vshrq_n_s32(g1,20))));
            int32x4_t r0=vmlaq_n_s32(y0,v0,1673527),r1=vmlaq_n_s32(y1,v1,1673527);
            out.val[2]=vqmovn_u16(vcombine_u16(vqmovun_s32(vshrq_n_s32(r0,20)),vqmovun_s32(vshrq_n_s32(r1,20))));
            vst3_u8(d+3*x,out);
        }
#endif
        for(;x<width;x+=2) {
            int u=(int)s[2*x+1]-128, v=(int)s[2*x+3]-128;
            for(unsigned p=0;p<2;++p) {
                int lum=s[2*x+2*p]>16?s[2*x+2*p]-16:0;
                lum=lum*1220542+(1<<19);
                d[3*(x+p)]=clip_byte((lum+2116026*u)>>20);
                d[3*(x+p)+1]=clip_byte((lum-409993*u-852492*v)>>20);
                d[3*(x+p)+2]=clip_byte((lum+1673527*v)>>20);
            }
        }
    }
    return 0;
}

/* Exact area integration for 640x480 -> 360x270. Each axis has rational
 * weights summing to 16; retain both axes at full precision until rounding.
 * This is the existing 4:3 camera letterbox, not a lower-resolution mode. */
void hdmi_camera_area(const uint8_t *src, size_t stride, uint8_t *dst, size_t dst_stride) {
#if defined(__ARM_NEON)
    static const uint8_t ix[3][16] = {
        {0,1,2,3,6,7,10,11,14,15,16,17,20,21,24,25},
        {2,3,4,5,8,9,12,13,16,17,18,19,22,23,26,27},
        {4,5,6,7,10,11,14,15,18,19,20,21,24,25,28,29}};
    static const uint16_t weights[3][8] = {
        {9,2,4,6,8,1,3,5}, {7,9,9,9,8,9,9,9}, {0,5,3,1,0,6,4,2}};
    uint16_t rows[3][640];
    for(unsigned y=0;y<270;++y) {
        unsigned sy=y*16/9, w0=9-(y*16%9), w1=16-w0;
        unsigned w2=w1>9?w1-9:0;
        if(w1>9) w1=9;
        const uint8_t *a=src+sy*stride, *b=a+stride, *c=w2?b+stride:b;
        for(unsigned x=0;x<640;x+=8) {
            uint8x8x3_t va=vld3_u8(a+3*x),vb=vld3_u8(b+3*x),vc=vld3_u8(c+3*x);
            for(unsigned k=0;k<3;++k) {
                uint16x8_t sum=vmull_u8(va.val[k],vdup_n_u8(w0));
                sum=vmlal_u8(sum,vb.val[k],vdup_n_u8(w1));
                if(w2) sum=vmlal_u8(sum,vc.val[k],vdup_n_u8(w2));
                vst1q_u16(rows[k]+x,sum);
            }
        }
        for(unsigned group=0;group<40;++group) {
            uint8x8x3_t out;
            for(unsigned k=0;k<3;++k) {
                const uint16_t *r=rows[k]+group*16;
                uint8x8x4_t table;
                table.val[0]=vld1_u8((const uint8_t*)r);
                table.val[1]=vld1_u8((const uint8_t*)r+8);
                table.val[2]=vld1_u8((const uint8_t*)r+16);
                table.val[3]=vld1_u8((const uint8_t*)r+24);
                uint16x8_t sum=vdupq_n_u16(0);
                for(unsigned p=0;p<3;++p) {
                    uint16x8_t value=vreinterpretq_u16_u8(vcombine_u8(
                        vtbl4_u8(table,vld1_u8(ix[p])),vtbl4_u8(table,vld1_u8(ix[p]+8))));
                    sum=vmlaq_u16(sum,value,vld1q_u16(weights[p]));
                }
                uint16x8_t tie=vandq_u16(vshrq_n_u16(sum,8),vdupq_n_u16(1));
                out.val[2-k]=vshrn_n_u16(vaddq_u16(vaddq_u16(sum,vdupq_n_u16(127)),tie),8);
                unsigned last=r[14]*7+r[15]*9;
                dst[y*dst_stride+group*27+24+2-k]=(last+127+((last>>8)&1))>>8;
            }
            vst3_u8(dst+y*dst_stride+group*27,out);
        }
    }
#else
    uint16_t row[640*3];
    for (unsigned y=0; y<270; ++y) {
        unsigned sy=y*16/9, w0=9-(y*16%9), w1=16-w0;
        unsigned w2=w1>9?w1-9:0;
        if (w1>9) w1=9;
        const uint8_t *a=src+sy*stride, *b=a+stride, *c=w2?b+stride:b;
        unsigned j=0;
#if defined(__ARM_NEON)
        uint8x8_t v0=vdup_n_u8(w0), v1=vdup_n_u8(w1), v2=vdup_n_u8(w2);
        for (;j+8<=640*3;j+=8) {
            uint16x8_t sum=vmull_u8(vld1_u8(a+j),v0);
            sum=vmlal_u8(sum,vld1_u8(b+j),v1);
            if(w2) sum=vmlal_u8(sum,vld1_u8(c+j),v2);
            vst1q_u16(row+j,sum);
        }
#endif
        for (;j<640*3;++j) row[j]=a[j]*w0+b[j]*w1+c[j]*w2;
        uint8_t *out=dst+y*dst_stride;
        for(unsigned x=0;x<360;++x) {
            unsigned sx=x*16/9, h0=9-(x*16%9), h1=16-h0;
            unsigned h2=h1>9?h1-9:0;
            if(h1>9) h1=9;
            for(unsigned channel=0;channel<3;++channel) {
                unsigned p=3*sx+channel;
                unsigned sum=row[p]*h0+row[p+3]*h1;
                if(h2) sum+=row[p+6]*h2;
                out[3*x+2-channel]=(sum+127+((sum>>8)&1))>>8;
            }
        }
    }
#endif
}

/* Cached UI contains only complete RGBX spans that the drawing code touched. */
void hdmi_overlay_spans(const uint8_t *src, uint8_t *dst,
                        const uint32_t *spans, size_t count) {
    for(size_t n=0;n<count;++n) {
        size_t start=spans[2*n], end=start+spans[2*n+1], p=start;
#if defined(__ARM_NEON)
        for(;p+16<=end;p+=16) vst1q_u8(dst+p,vld1q_u8(src+p));
#endif
        for(;p<end;++p) dst[p]=src[p];
    }
}

int hdmi_rgbx_to_rgb(const uint8_t *src, uint8_t *dst, size_t pixels) {
    size_t i = 0;
    uint8_t invalid = 0;
#if defined(__ARM_NEON)
    static const uint8_t indexes[4][8] = {
        {0, 1, 2, 4, 5, 6, 8, 9},
        {10, 12, 13, 14, 16, 17, 18, 20},
        {21, 22, 24, 25, 26, 28, 29, 30},
        {3, 7, 11, 15, 19, 23, 27, 31}
    };
    const uint8x8_t ix0 = vld1_u8(indexes[0]);
    const uint8x8_t ix1 = vld1_u8(indexes[1]);
    const uint8x8_t ix2 = vld1_u8(indexes[2]);
    const uint8x8_t ix3 = vld1_u8(indexes[3]);
    uint8x8_t alpha = vdup_n_u8(0);
    for (; i + 8 <= pixels; i += 8) {
        PREFETCH_READ(src+4*i);
        uint8x8x4_t in;
        in.val[0] = vld1_u8(src+4*i);
        in.val[1] = vld1_u8(src+4*i+8);
        in.val[2] = vld1_u8(src+4*i+16);
        in.val[3] = vld1_u8(src+4*i+24);
        vst1_u8(dst+3*i, vtbl4_u8(in, ix0));
        vst1_u8(dst+3*i+8, vtbl4_u8(in, ix1));
        vst1_u8(dst+3*i+16, vtbl4_u8(in, ix2));
        alpha = vorr_u8(alpha, vtbl4_u8(in, ix3));
    }
    uint8_t check[8];
    vst1_u8(check, alpha);
    for (size_t j = 0; j < 8; ++j) invalid |= check[j];
#endif
    for (; i < pixels; ++i) {
        dst[3*i] = src[4*i];
        dst[3*i+1] = src[4*i+1];
        dst[3*i+2] = src[4*i+2];
        invalid |= src[4*i+3];
    }
    return invalid != 0;
}

/* Check every output X byte without allocating or converting the RGB pixels. */
int hdmi_validate_rgbx(const uint8_t *src, size_t pixels) {
    size_t i = 0;
    uint8_t invalid = 0;
#if defined(__ARM_NEON)
    uint32x4_t bits = vdupq_n_u32(0), b1 = bits, b2 = bits, b3 = bits;
    for (; i + 16 <= pixels; i += 16) {
        PREFETCH_READ(src+4*i);
        bits = vorrq_u32(bits, vreinterpretq_u32_u8(vld1q_u8(src + 4*i)));
        b1 = vorrq_u32(b1, vreinterpretq_u32_u8(vld1q_u8(src + 4*i+16)));
        b2 = vorrq_u32(b2, vreinterpretq_u32_u8(vld1q_u8(src + 4*i+32)));
        b3 = vorrq_u32(b3, vreinterpretq_u32_u8(vld1q_u8(src + 4*i+48)));
    }
    bits = vorrq_u32(vorrq_u32(bits,b1),vorrq_u32(b2,b3));
    uint32_t lanes[4];
    vst1q_u32(lanes, bits);
    invalid = (uint8_t)((lanes[0] | lanes[1] | lanes[2] | lanes[3]) >> 24);
#endif
    for (; i < pixels; ++i) invalid |= src[4*i+3];
    return invalid != 0;
}

/* Validate EVERY source word, including pixels omitted by half-size sampling.
 * The caller owns the destination exclusively and must not publish on error.
 * Noncacheable scanout needs no dirty full-frame CPU cache clean afterward. */
int hdmi_copy_rgbx_checked(const uint8_t *src, size_t src_stride, size_t sw, size_t sh,
                          uint8_t *dst, size_t dst_stride, size_t dw, size_t dh) {
    if (!sw || !sh || sw>4096 || sh>4096 || src_stride<sw*4 || dst_stride<dw*4
            || !((sw==dw && sh==dh) || (sw==dw*2 && sh==dh*2))) return -1;
    const unsigned half=sw!=dw;
    uint32_t invalid=0;
#if defined(__ARM_NEON)
    uint32x4_t bits=vdupq_n_u32(0),b1=bits,b2=bits,b3=bits;
#endif
    for(size_t y=0;y<sh;++y) {
        const uint8_t *s=src+y*src_stride;
        uint8_t *d=dst+(y>>half)*dst_stride;
        unsigned write_row=!half || !(y&1);
        size_t x=0;
#if defined(__ARM_NEON)
        for(;x+16<=sw;x+=16) {
            PREFETCH_READ(s+x*4);
            uint32x4_t a=vreinterpretq_u32_u8(vld1q_u8(s+x*4));
            uint32x4_t b=vreinterpretq_u32_u8(vld1q_u8(s+x*4+16));
            uint32x4_t c=vreinterpretq_u32_u8(vld1q_u8(s+x*4+32));
            uint32x4_t e=vreinterpretq_u32_u8(vld1q_u8(s+x*4+48));
            bits=vorrq_u32(bits,a);b1=vorrq_u32(b1,b);b2=vorrq_u32(b2,c);b3=vorrq_u32(b3,e);
            if(write_row) {
                if(half) {
                    vst1q_u8(d+x*2,vreinterpretq_u8_u32(vuzpq_u32(a,b).val[0]));
                    vst1q_u8(d+x*2+16,vreinterpretq_u8_u32(vuzpq_u32(c,e).val[0]));
                } else {
                    vst1q_u8(d+x*4,vreinterpretq_u8_u32(a));
                    vst1q_u8(d+x*4+16,vreinterpretq_u8_u32(b));
                    vst1q_u8(d+x*4+32,vreinterpretq_u8_u32(c));
                    vst1q_u8(d+x*4+48,vreinterpretq_u8_u32(e));
                }
            }
        }
#endif
        for(;x<sw;++x) {
            invalid|=s[x*4+3];
            if(write_row && (!half || !(x&1))) {
                for(unsigned k=0;k<4;++k) d[(x>>half)*4+k]=s[x*4+k];
            }
        }
    }
#if defined(__ARM_NEON)
    uint32_t lanes[4];
    vst1q_u32(lanes,vorrq_u32(vorrq_u32(bits,b1),vorrq_u32(b2,b3)));
    invalid|=(lanes[0]|lanes[1]|lanes[2]|lanes[3])>>24;
#endif
    return invalid!=0;
}

/* The RGB view has a four-byte pixel stride; preserve exact nearest pixels. */
int hdmi_paint_rgbx(const uint8_t *src, size_t src_stride, size_t sw, size_t sh,
                    uint8_t *dst, size_t dst_stride, size_t dw, size_t dh) {
    if (!sw || !sh || !dw || !dh || sw > 4096 || sh > 4096 || dw > 4096 || dh > 4096)
        return -1;
    for (size_t y = 0; y < dh; ++y) {
        const uint8_t *s = src + (y * sh / dh) * src_stride;
        uint8_t *d = dst + y * dst_stride;
        size_t x = 0;
#if defined(__ARM_NEON)
        const uint32x4_t rgb = vdupq_n_u32(0x00ffffff);
        if (sw == dw) {
            for (; x + 4 <= dw; x += 4) {
                PREFETCH_READ(s+4*x);
                vst1q_u8(d + 4*x, vreinterpretq_u8_u32(vandq_u32(
                    vreinterpretq_u32_u8(vld1q_u8(s + 4*x)), rgb)));
            }
        } else if (sw == 2*dw) {
            for (; x + 4 <= dw; x += 4) {
                PREFETCH_READ(s+8*x);
                uint32x4_t a = vreinterpretq_u32_u8(vld1q_u8(s + 8*x));
                uint32x4_t b = vreinterpretq_u32_u8(vld1q_u8(s + 8*x + 16));
                vst1q_u8(d + 4*x, vreinterpretq_u8_u32(vandq_u32(vuzpq_u32(a,b).val[0], rgb)));
            }
        }
#endif
        for (; x < dw; ++x) {
            const uint8_t *p = s + 4*(x*sw/dw);
            d[4*x]=p[0]; d[4*x+1]=p[1]; d[4*x+2]=p[2]; d[4*x+3]=0;
        }
    }
    return 0;
}

void hdmi_blit_rgb(const uint8_t *src, size_t src_stride, uint8_t *dst,
                   size_t dst_stride, size_t width, size_t height) {
    for (size_t y = 0; y < height; ++y) {
        const uint8_t *s = src + y*src_stride;
        uint8_t *d = dst + y*dst_stride;
        size_t x = 0;
#if defined(__ARM_NEON)
        for (; x + 16 <= width; x += 16) {
            PREFETCH_READ(s+3*x);
            uint8x16x3_t in = vld3q_u8(s + 3*x);
            uint8x16x4_t out = vld4q_u8(d + 4*x);
            out.val[0] = in.val[0]; out.val[1] = in.val[1]; out.val[2] = in.val[2];
            vst4q_u8(d + 4*x, out);
        }
#endif
        for (; x < width; ++x) {
            d[4*x] = s[3*x]; d[4*x+1] = s[3*x+1]; d[4*x+2] = s[3*x+2];
        }
    }
}

/* Nearest sampling directly into RGBX. Every written X byte is zero. */
int hdmi_paint_nearest(const uint8_t *src, size_t src_stride, size_t sw, size_t sh,
                       uint8_t *dst, size_t dst_stride, size_t dw, size_t dh) {
    if (!sw || !sh || !dw || !dh || sw > 4096 || sh > 4096 || dw > 4096 || dh > 4096)
        return -1;
    size_t columns[4096];
    for (size_t x = 0; x < dw; ++x) columns[x] = (x * sw / dw) * 3;
    for (size_t y = 0; y < dh; ++y) {
        const uint8_t *s = src + (y * sh / dh) * src_stride;
        uint8_t *d = dst + y * dst_stride;
        size_t x = 0;
#if defined(__ARM_NEON)
        if (sw == dw) {
            for (; x + 16 <= dw; x += 16) {
                PREFETCH_READ(s+3*x);
                uint8x16x3_t in = vld3q_u8(s + 3*x);
                uint8x16x4_t out;
                out.val[0] = in.val[0]; out.val[1] = in.val[1]; out.val[2] = in.val[2];
                out.val[3] = vdupq_n_u8(0);
                vst4q_u8(d + 4*x, out);
            }
        } else if (sw == dw * 2) {
            /* Split view: keep pixels 0,2,...,14 from sixteen RGB pixels.
             * Row selection remains the exact integer nearest rule above. */
            for (; x + 8 <= dw; x += 8) {
                PREFETCH_READ(s+6*x);
                uint8x16x3_t in = vld3q_u8(s + 6*x);
                uint8x8x4_t out;
                out.val[0] = vget_low_u8(vuzpq_u8(in.val[0], in.val[0]).val[0]);
                out.val[1] = vget_low_u8(vuzpq_u8(in.val[1], in.val[1]).val[0]);
                out.val[2] = vget_low_u8(vuzpq_u8(in.val[2], in.val[2]).val[0]);
                out.val[3] = vdup_n_u8(0);
                vst4_u8(d + 4*x, out);
            }
        } else if (sw * 2 == dw) {
            for (; x + 32 <= dw; x += 32) {
                PREFETCH_READ(s+3*(x/2));
                uint8x16x3_t in = vld3q_u8(s + 3*(x/2));
                uint8x16x2_t r = vzipq_u8(in.val[0], in.val[0]);
                uint8x16x2_t g = vzipq_u8(in.val[1], in.val[1]);
                uint8x16x2_t b = vzipq_u8(in.val[2], in.val[2]);
                uint8x16x4_t left, right;
                left.val[0]=r.val[0]; left.val[1]=g.val[0]; left.val[2]=b.val[0];
                right.val[0]=r.val[1]; right.val[1]=g.val[1]; right.val[2]=b.val[1];
                left.val[3]=right.val[3]=vdupq_n_u8(0);
                vst4q_u8(d+4*x, left); vst4q_u8(d+4*(x+16), right);
            }
        }
#endif
        for (; x < dw; ++x) {
            const uint8_t *p = s + columns[x];
            d[4*x]=p[0]; d[4*x+1]=p[1]; d[4*x+2]=p[2]; d[4*x+3]=0;
        }
    }
    return 0;
}
