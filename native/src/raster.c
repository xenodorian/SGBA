/* Software drawing primitives for the canvas. */
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include "raster.h"

#if defined(__SSE2__)
#include <emmintrin.h>
#define SG_SSE2 1
#elif defined(__aarch64__) && defined(__ARM_NEON)
#include <arm_neon.h>
#define SG_NEON 1
#endif

/* Grow-only scratch memory, one set per thread: blits, polygons and gradients run thousands of times a frame and must not call malloc. */
static __thread struct { void *p; size_t cap; } g_scratch[4];
void *r_scratch(int slot, size_t bytes) {
    if (g_scratch[slot].cap < bytes) {
        free(g_scratch[slot].p);
        g_scratch[slot].cap = bytes + bytes / 2 + 64;
        g_scratch[slot].p = malloc(g_scratch[slot].cap);
    }
    return g_scratch[slot].p;
}

IRect irect_clip(IRect a, IRect b) {
    int x0 = a.x > b.x ? a.x : b.x, y0 = a.y > b.y ? a.y : b.y;
    int x1 = a.x + a.w < b.x + b.w ? a.x + a.w : b.x + b.w, y1 = a.y + a.h < b.y + b.h ? a.y + a.h : b.y + b.h;
    IRect r = { x0, y0, x1 - x0, y1 - y0 };
    if (r.w < 0) r.w = 0;
    if (r.h < 0) r.h = 0;
    return r;
}
IRect irect_union(IRect a, IRect b) {
    if (irect_empty(a)) return b;
    if (irect_empty(b)) return a;
    int x0 = a.x < b.x ? a.x : b.x, y0 = a.y < b.y ? a.y : b.y;
    int x1 = a.x + a.w > b.x + b.w ? a.x + a.w : b.x + b.w, y1 = a.y + a.h > b.y + b.h ? a.y + a.h : b.y + b.h;
    IRect r = { x0, y0, x1 - x0, y1 - y0 };
    return r;
}
int irect_collide(IRect a, IRect b) { return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h && a.w > 0 && a.h > 0 && b.w > 0 && b.h > 0; }
int irect_contains(IRect o, IRect i) { return i.x >= o.x && i.y >= o.y && i.x + i.w <= o.x + o.w && i.y + i.h <= o.y + o.h; }

static size_t g_live;

Surf *surf_create(int w, int h) {
    Surf *s = calloc(1, sizeof *s);
    s->w = w; s->h = h;
    s->px = calloc((size_t)w * (size_t)h, 4);
    s->owns = 1;
    s->nbytes = (size_t)w * (size_t)h * 4;
    __atomic_add_fetch(&g_live, s->nbytes, __ATOMIC_RELAXED);
    return s;
}
void surf_free(Surf *s) {
    if (!s) return;
    if (s->owns) { free(s->px); __atomic_sub_fetch(&g_live, s->nbytes, __ATOMIC_RELAXED); }
    free(s);
}
size_t surf_live_bytes(void) { return __atomic_load_n(&g_live, __ATOMIC_RELAXED); }
void surf_fill_all(Surf *s, uint32_t argb) { size_t n = (size_t)s->w * s->h; for (size_t i = 0; i < n; i++) s->px[i] = argb; }

void r_fill_solid(Surf *d, IRect r, uint32_t c) {
    for (int y = r.y; y < r.y + r.h; y++) {
        uint32_t *row = d->px + (size_t)y * d->w + r.x;
        for (int x = 0; x < r.w; x++) row[x] = c;
    }
}

/* s over an opaque d with source alpha a (1..254): two colour lanes at a time, exact /255 rounding. */
static inline uint32_t blend_opaque(uint32_t s, uint32_t d, uint32_t a) {
    uint32_t inv = 255 - a;
    uint32_t rb = (d & 0x00FF00FFu) * inv + (s & 0x00FF00FFu) * a + 0x00800080u;
    rb = ((rb + ((rb >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    uint32_t g = ((d >> 8) & 0xFFu) * inv + ((s >> 8) & 0xFFu) * a + 0x80u;
    g = ((g + (g >> 8)) >> 8) & 0xFFu;
    return 0xFF000000u | rb | (g << 8);
}

/* One pixel: s over d (straight alpha). */
static inline uint32_t over(uint32_t s, uint32_t d, int a /* source alpha 0..255 */) {
    if (a <= 0) return d;
    int da = PA(d);
    if (da == 255) {
        if (a >= 255) return s | 0xFF000000u;
        return blend_opaque(s, d, (uint32_t)a);
    }
    if (da == 0) return RGBA(PR(s), PG(s), PB(s), a);
    int oa = a + da * (255 - a) / 255;
    if (oa <= 0) return 0;
    int r = (PR(s) * a * 255 + PR(d) * da * (255 - a) + oa * 127) / (oa * 255);
    int g = (PG(s) * a * 255 + PG(d) * da * (255 - a) + oa * 127) / (oa * 255);
    int b = (PB(s) * a * 255 + PB(d) * da * (255 - a) + oa * 127) / (oa * 255);
    r = r > 255 ? 255 : r; g = g > 255 ? 255 : g; b = b > 255 ? 255 : b;
    return RGBA(r, g, b, oa);
}

/* Composite one pixel according to the mode. s carries its own alpha (already multiplied by any global alpha). */
uint32_t r_comp_px(uint32_t s, uint32_t d, int comp) {
    switch (comp) {
    case COMP_OVER: return over(s, d, PA(s));
    case COMP_MULTIPLY: {                        /* colour multiply weighted by the source alpha (the page's grade overlays) */
        int a = PA(s);
        int mr = PR(d) * PR(s) / 255, mg = PG(d) * PG(s) / 255, mb = PB(d) * PB(s) / 255;
        int r = PR(d) + ((mr - PR(d)) * a) / 255, g = PG(d) + ((mg - PG(d)) * a) / 255, b = PB(d) + ((mb - PB(d)) * a) / 255;
        return RGBA(r, g, b, PA(d));
    }
    case COMP_LIGHTER: {
        int a = PA(s);
        int r = PR(d) + PR(s) * a / 255, g = PG(d) + PG(s) * a / 255, b = PB(d) + PB(s) * a / 255;
        r = r > 255 ? 255 : r; g = g > 255 ? 255 : g; b = b > 255 ? 255 : b;
        return RGBA(r, g, b, PA(d));
    }
    case COMP_SRC_IN: {                          /* keep only where the destination already has pixels, in the source colour */
        int a = PA(d) * PA(s) / 255;
        return RGBA(PR(s), PG(s), PB(s), a);
    }
    case COMP_SRC_ATOP: {                        /* wash existing pixels with the source colour, leave transparent ones alone */
        int a = PA(s);
        int r = (PR(d) * (255 - a) + PR(s) * a) / 255, g = (PG(d) * (255 - a) + PG(s) * a) / 255, b = (PB(d) * (255 - a) + PB(s) * a) / 255;
        return RGBA(r, g, b, PA(d));
    }
    }
    return d;
}

/* ---------------------------------------------------------------------------------------------------------------------
 * Span kernels: four pixels at a time with SSE2 (PC) or NEON (aarch64, the R36S). They are exact copies of the scalar
 * formulas above (the /255 is the same rounding), and fall back to those for the rare case of a destination that is not
 * opaque. tests/raster_test.c compares every kernel with the scalar reference.
 * ------------------------------------------------------------------------------------------------------------------ */
enum { SRC_ALPHA = 0, SRC_OPAQUE = 1, SRC_KEY = 2 };

static inline uint32_t src_alpha_px(uint32_t p, int mode, int ga) {
    int a = mode == SRC_OPAQUE ? 255 : (int)(p >> 24);
    if (mode == SRC_KEY && a) a = 255;
    if (ga < 255) a = a * ga / 255;
    return (uint32_t)a;
}

/* scalar tail / non-opaque destination: one pixel */
static inline void px_over(uint32_t *dp, uint32_t s, int mode, int ga) {
    int a = (int)src_alpha_px(s, mode, ga);
    if (!a) return;
    *dp = over((s & 0x00FFFFFFu) | ((uint32_t)a << 24), *dp, a);
}

void r_span_over_ref(uint32_t *dp, const uint32_t *sp, int n, int ga, int mode) { for (int i = 0; i < n; i++) px_over(dp + i, sp[i], mode, ga); }

void r_span_over(uint32_t *dp, const uint32_t *sp, int n, int ga, int mode) {
    int i = 0;
#if SG_SSE2
    const __m128i zero = _mm_setzero_si128(), c255_32 = _mm_set1_epi32(255), c255_16 = _mm_set1_epi16(255), c128 = _mm_set1_epi16(128);
    const __m128i amask = _mm_set1_epi32((int)0xFF000000u), one32 = _mm_set1_epi32(1);
    for (; i + 4 <= n; i += 4) {
        __m128i s = _mm_loadu_si128((const __m128i *)(sp + i));
        __m128i a32;
        if (mode == SRC_OPAQUE) a32 = c255_32;
        else {
            a32 = _mm_srli_epi32(s, 24);
            if (mode == SRC_KEY) a32 = _mm_andnot_si128(_mm_cmpeq_epi32(a32, zero), c255_32);
        }
        if (ga < 255) {
            __m128i x = _mm_mullo_epi16(a32, _mm_set1_epi32(ga));                      /* a <= 255 and ga <= 255: fits 16 bits */
            a32 = _mm_srli_epi32(_mm_add_epi32(_mm_add_epi32(x, one32), _mm_srli_epi32(x, 8)), 8);   /* floor(x / 255) */
        }
        if (_mm_movemask_epi8(_mm_cmpeq_epi32(a32, zero)) == 0xFFFF) continue;         /* four transparent pixels */
        if (_mm_movemask_epi8(_mm_cmpeq_epi32(a32, c255_32)) == 0xFFFF) {               /* four solid pixels */
            _mm_storeu_si128((__m128i *)(dp + i), _mm_or_si128(s, amask));
            continue;
        }
        __m128i d = _mm_loadu_si128((const __m128i *)(dp + i));
        if (_mm_movemask_epi8(_mm_cmpeq_epi32(_mm_srli_epi32(d, 24), c255_32)) != 0xFFFF) {   /* destination not opaque: scalar */
            for (int k = 0; k < 4; k++) px_over(dp + i + k, sp[i + k], mode, ga);
            continue;
        }
        __m128i a2 = _mm_or_si128(a32, _mm_slli_epi32(a32, 16));                        /* a a per pixel */
        __m128i alo = _mm_unpacklo_epi16(a2, a2), ahi = _mm_unpackhi_epi16(a2, a2);      /* a a a a per pixel */
        __m128i slo = _mm_unpacklo_epi8(s, zero), shi = _mm_unpackhi_epi8(s, zero);
        __m128i dlo = _mm_unpacklo_epi8(d, zero), dhi = _mm_unpackhi_epi8(d, zero);
        __m128i tlo = _mm_add_epi16(_mm_add_epi16(_mm_mullo_epi16(slo, alo), _mm_mullo_epi16(dlo, _mm_sub_epi16(c255_16, alo))), c128);
        __m128i thi = _mm_add_epi16(_mm_add_epi16(_mm_mullo_epi16(shi, ahi), _mm_mullo_epi16(dhi, _mm_sub_epi16(c255_16, ahi))), c128);
        tlo = _mm_srli_epi16(_mm_add_epi16(tlo, _mm_srli_epi16(tlo, 8)), 8);
        thi = _mm_srli_epi16(_mm_add_epi16(thi, _mm_srli_epi16(thi, 8)), 8);
        _mm_storeu_si128((__m128i *)(dp + i), _mm_or_si128(_mm_packus_epi16(tlo, thi), amask));
    }
#elif SG_NEON
    const uint32x4_t amask = vdupq_n_u32(0xFF000000u), c255 = vdupq_n_u32(255), zero = vdupq_n_u32(0);
    const uint16x8_t c255_16 = vdupq_n_u16(255), c128 = vdupq_n_u16(128);
    for (; i + 4 <= n; i += 4) {
        uint32x4_t s = vld1q_u32(sp + i);
        uint32x4_t a32;
        if (mode == SRC_OPAQUE) a32 = c255;
        else {
            a32 = vshrq_n_u32(s, 24);
            if (mode == SRC_KEY) a32 = vandq_u32(vmvnq_u32(vceqq_u32(a32, zero)), c255);
        }
        if (ga < 255) {
            uint32x4_t x = vmulq_n_u32(a32, (uint32_t)ga);
            a32 = vshrq_n_u32(vaddq_u32(vaddq_u32(x, vdupq_n_u32(1)), vshrq_n_u32(x, 8)), 8);
        }
        if (vmaxvq_u32(a32) == 0) continue;
        if (vminvq_u32(a32) == 255) { vst1q_u32(dp + i, vorrq_u32(s, amask)); continue; }
        uint32x4_t d = vld1q_u32(dp + i);
        if (vminvq_u32(vshrq_n_u32(d, 24)) != 255) { for (int k = 0; k < 4; k++) px_over(dp + i + k, sp[i + k], mode, ga); continue; }
        uint16x8_t a2 = vreinterpretq_u16_u32(vorrq_u32(a32, vshlq_n_u32(a32, 16)));
        uint16x8_t alo = vzip1q_u16(a2, a2), ahi = vzip2q_u16(a2, a2);
        uint8x16_t s8 = vreinterpretq_u8_u32(s), d8 = vreinterpretq_u8_u32(d);
        uint16x8_t slo = vmovl_u8(vget_low_u8(s8)), shi = vmovl_high_u8(s8), dlo = vmovl_u8(vget_low_u8(d8)), dhi = vmovl_high_u8(d8);
        uint16x8_t tlo = vaddq_u16(vaddq_u16(vmulq_u16(slo, alo), vmulq_u16(dlo, vsubq_u16(c255_16, alo))), c128);
        uint16x8_t thi = vaddq_u16(vaddq_u16(vmulq_u16(shi, ahi), vmulq_u16(dhi, vsubq_u16(c255_16, ahi))), c128);
        tlo = vshrq_n_u16(vaddq_u16(tlo, vshrq_n_u16(tlo, 8)), 8);
        thi = vshrq_n_u16(vaddq_u16(thi, vshrq_n_u16(thi, 8)), 8);
        uint8x16_t out = vcombine_u8(vqmovn_u16(tlo), vqmovn_u16(thi));
        vst1q_u32(dp + i, vorrq_u32(vreinterpretq_u32_u8(out), amask));
    }
#endif
    for (; i < n; i++) px_over(dp + i, sp[i], mode, ga);
}

/* constant colour c (with its alpha) over n pixels, mode "over" */
void r_span_fill_over_ref(uint32_t *dp, int n, uint32_t c) { int a = (int)(c >> 24); for (int i = 0; i < n; i++) if (a) dp[i] = over(c, dp[i], a); }

void r_span_fill_over(uint32_t *dp, int n, uint32_t c) {
    uint32_t a = c >> 24;
    if (!a) return;
    int i = 0;
    if (a == 255) { uint32_t solid = c | 0xFF000000u; for (; i < n; i++) dp[i] = solid; return; }
#if SG_SSE2
    const __m128i zero = _mm_setzero_si128(), c255_16 = _mm_set1_epi16(255), c128 = _mm_set1_epi16(128), amask = _mm_set1_epi32((int)0xFF000000u);
    const __m128i c255_32 = _mm_set1_epi32(255);
    __m128i c4 = _mm_set1_epi32((int)c), aa = _mm_set1_epi16((short)a), ia = _mm_sub_epi16(c255_16, aa);
    __m128i clo = _mm_unpacklo_epi8(c4, zero), chi = _mm_unpackhi_epi8(c4, zero);
    __m128i sa_lo = _mm_mullo_epi16(clo, aa), sa_hi = _mm_mullo_epi16(chi, aa);            /* colour * alpha, once */
    for (; i + 4 <= n; i += 4) {
        __m128i d = _mm_loadu_si128((const __m128i *)(dp + i));
        if (_mm_movemask_epi8(_mm_cmpeq_epi32(_mm_srli_epi32(d, 24), c255_32)) != 0xFFFF) { for (int k = 0; k < 4; k++) dp[i + k] = over(c, dp[i + k], (int)a); continue; }
        __m128i dlo = _mm_unpacklo_epi8(d, zero), dhi = _mm_unpackhi_epi8(d, zero);
        __m128i tlo = _mm_add_epi16(_mm_add_epi16(sa_lo, _mm_mullo_epi16(dlo, ia)), c128);
        __m128i thi = _mm_add_epi16(_mm_add_epi16(sa_hi, _mm_mullo_epi16(dhi, ia)), c128);
        tlo = _mm_srli_epi16(_mm_add_epi16(tlo, _mm_srli_epi16(tlo, 8)), 8);
        thi = _mm_srli_epi16(_mm_add_epi16(thi, _mm_srli_epi16(thi, 8)), 8);
        _mm_storeu_si128((__m128i *)(dp + i), _mm_or_si128(_mm_packus_epi16(tlo, thi), amask));
    }
#elif SG_NEON
    const uint32x4_t amask = vdupq_n_u32(0xFF000000u);
    const uint16x8_t c255_16 = vdupq_n_u16(255), c128 = vdupq_n_u16(128), aa = vdupq_n_u16((uint16_t)a), ia = vsubq_u16(c255_16, aa);
    uint8x16_t c16 = vreinterpretq_u8_u32(vdupq_n_u32(c));
    uint16x8_t sa_lo = vmulq_u16(vmovl_u8(vget_low_u8(c16)), aa), sa_hi = vmulq_u16(vmovl_high_u8(c16), aa);
    for (; i + 4 <= n; i += 4) {
        uint32x4_t d = vld1q_u32(dp + i);
        if (vminvq_u32(vshrq_n_u32(d, 24)) != 255) { for (int k = 0; k < 4; k++) dp[i + k] = over(c, dp[i + k], (int)a); continue; }
        uint8x16_t d8 = vreinterpretq_u8_u32(d);
        uint16x8_t dlo = vmovl_u8(vget_low_u8(d8)), dhi = vmovl_high_u8(d8);
        uint16x8_t tlo = vaddq_u16(vaddq_u16(sa_lo, vmulq_u16(dlo, ia)), c128);
        uint16x8_t thi = vaddq_u16(vaddq_u16(sa_hi, vmulq_u16(dhi, ia)), c128);
        tlo = vshrq_n_u16(vaddq_u16(tlo, vshrq_n_u16(tlo, 8)), 8);
        thi = vshrq_n_u16(vaddq_u16(thi, vshrq_n_u16(thi, 8)), 8);
        vst1q_u32(dp + i, vorrq_u32(vreinterpretq_u32_u8(vcombine_u8(vqmovn_u16(tlo), vqmovn_u16(thi))), amask));
    }
#endif
    for (; i < n; i++) dp[i] = over(c, dp[i], (int)a);
}

/* constant colour in the multiply / lighter / source-atop modes (alpha of the destination is kept) */
void r_span_fill_comp(uint32_t *dp, int n, uint32_t c, int comp) {
    int i = 0;
    int a = (int)(c >> 24);
#if SG_SSE2
    if (comp == COMP_MULTIPLY || comp == COMP_SRC_ATOP) {
        const __m128i zero = _mm_setzero_si128(), c255_16 = _mm_set1_epi16(255), c128 = _mm_set1_epi16(128);
        const __m128i keep_a = _mm_set1_epi32((int)0xFF000000u), keep_rgb = _mm_set1_epi32(0x00FFFFFF);
        __m128i c4 = _mm_set1_epi32((int)c), aa = _mm_set1_epi16((short)a), ia = _mm_sub_epi16(c255_16, aa);
        __m128i clo = _mm_unpacklo_epi8(c4, zero), chi = _mm_unpackhi_epi8(c4, zero);
        __m128i sa_lo = _mm_mullo_epi16(clo, aa), sa_hi = _mm_mullo_epi16(chi, aa);
        for (; i + 4 <= n; i += 4) {
            __m128i d = _mm_loadu_si128((const __m128i *)(dp + i));
            __m128i dlo = _mm_unpacklo_epi8(d, zero), dhi = _mm_unpackhi_epi8(d, zero);
            __m128i xlo, xhi;                                             /* what the colour becomes where it lands */
            if (comp == COMP_MULTIPLY) {
                xlo = _mm_add_epi16(_mm_mullo_epi16(dlo, clo), c128); xlo = _mm_srli_epi16(_mm_add_epi16(xlo, _mm_srli_epi16(xlo, 8)), 8);
                xhi = _mm_add_epi16(_mm_mullo_epi16(dhi, chi), c128); xhi = _mm_srli_epi16(_mm_add_epi16(xhi, _mm_srli_epi16(xhi, 8)), 8);
            } else { xlo = clo; xhi = chi; }
            __m128i tlo = _mm_add_epi16(_mm_add_epi16(_mm_mullo_epi16(xlo, aa), _mm_mullo_epi16(dlo, ia)), c128);
            __m128i thi = _mm_add_epi16(_mm_add_epi16(_mm_mullo_epi16(xhi, aa), _mm_mullo_epi16(dhi, ia)), c128);
            tlo = _mm_srli_epi16(_mm_add_epi16(tlo, _mm_srli_epi16(tlo, 8)), 8);
            thi = _mm_srli_epi16(_mm_add_epi16(thi, _mm_srli_epi16(thi, 8)), 8);
            __m128i out = _mm_packus_epi16(tlo, thi);
            _mm_storeu_si128((__m128i *)(dp + i), _mm_or_si128(_mm_and_si128(out, keep_rgb), _mm_and_si128(d, keep_a)));
        }
        (void)sa_lo; (void)sa_hi;
    } else if (comp == COMP_LIGHTER) {
        int r = PR(c) * a / 255, g = PG(c) * a / 255, b = PB(c) * a / 255;
        __m128i add = _mm_set1_epi32((int)RGBA(r, g, b, 0));
        for (; i + 4 <= n; i += 4) _mm_storeu_si128((__m128i *)(dp + i), _mm_adds_epu8(_mm_loadu_si128((const __m128i *)(dp + i)), add));
    }
#elif SG_NEON
    if (comp == COMP_MULTIPLY || comp == COMP_SRC_ATOP) {
        const uint16x8_t c255_16 = vdupq_n_u16(255), c128 = vdupq_n_u16(128), aa = vdupq_n_u16((uint16_t)a), ia = vsubq_u16(c255_16, aa);
        const uint32x4_t keep_a = vdupq_n_u32(0xFF000000u), keep_rgb = vdupq_n_u32(0x00FFFFFFu);
        uint8x16_t c16 = vreinterpretq_u8_u32(vdupq_n_u32(c));
        uint16x8_t clo = vmovl_u8(vget_low_u8(c16)), chi = vmovl_high_u8(c16);
        for (; i + 4 <= n; i += 4) {
            uint32x4_t d = vld1q_u32(dp + i);
            uint8x16_t d8 = vreinterpretq_u8_u32(d);
            uint16x8_t dlo = vmovl_u8(vget_low_u8(d8)), dhi = vmovl_high_u8(d8), xlo, xhi;
            if (comp == COMP_MULTIPLY) {
                xlo = vaddq_u16(vmulq_u16(dlo, clo), c128); xlo = vshrq_n_u16(vaddq_u16(xlo, vshrq_n_u16(xlo, 8)), 8);
                xhi = vaddq_u16(vmulq_u16(dhi, chi), c128); xhi = vshrq_n_u16(vaddq_u16(xhi, vshrq_n_u16(xhi, 8)), 8);
            } else { xlo = clo; xhi = chi; }
            uint16x8_t tlo = vaddq_u16(vaddq_u16(vmulq_u16(xlo, aa), vmulq_u16(dlo, ia)), c128);
            uint16x8_t thi = vaddq_u16(vaddq_u16(vmulq_u16(xhi, aa), vmulq_u16(dhi, ia)), c128);
            tlo = vshrq_n_u16(vaddq_u16(tlo, vshrq_n_u16(tlo, 8)), 8);
            thi = vshrq_n_u16(vaddq_u16(thi, vshrq_n_u16(thi, 8)), 8);
            uint32x4_t out = vreinterpretq_u32_u8(vcombine_u8(vqmovn_u16(tlo), vqmovn_u16(thi)));
            vst1q_u32(dp + i, vorrq_u32(vandq_u32(out, keep_rgb), vandq_u32(d, keep_a)));
        }
    } else if (comp == COMP_LIGHTER) {
        int r = PR(c) * a / 255, g = PG(c) * a / 255, b = PB(c) * a / 255;
        uint8x16_t add = vreinterpretq_u8_u32(vdupq_n_u32(RGBA(r, g, b, 0)));
        for (; i + 4 <= n; i += 4) vst1q_u32(dp + i, vreinterpretq_u32_u8(vqaddq_u8(vreinterpretq_u8_u32(vld1q_u32(dp + i)), add)));
    }
#endif
    for (; i < n; i++) dp[i] = r_comp_px(c, dp[i], comp);
}
void r_span_fill_comp_ref(uint32_t *dp, int n, uint32_t c, int comp) { for (int i = 0; i < n; i++) dp[i] = r_comp_px(c, dp[i], comp); }

/* per-pixel colours (with their own alpha) in the multiply mode: gradients and masks. Other modes stay scalar. */
void r_span_comp_ref(uint32_t *dp, const uint32_t *sp, int n, int comp) { for (int i = 0; i < n; i++) if ((sp[i] >> 24) || comp == COMP_SRC_IN) dp[i] = r_comp_px(sp[i], dp[i], comp); }

void r_span_comp(uint32_t *dp, const uint32_t *sp, int n, int comp) {
    int i = 0;
    if (comp == COMP_OVER) { r_span_over(dp, sp, n, 255, SRC_ALPHA); return; }
#if SG_SSE2
    if (comp == COMP_MULTIPLY) {
        const __m128i zero = _mm_setzero_si128(), c255_16 = _mm_set1_epi16(255), c128 = _mm_set1_epi16(128);
        const __m128i keep_a = _mm_set1_epi32((int)0xFF000000u), keep_rgb = _mm_set1_epi32(0x00FFFFFF);
        for (; i + 4 <= n; i += 4) {
            __m128i s = _mm_loadu_si128((const __m128i *)(sp + i)), d = _mm_loadu_si128((const __m128i *)(dp + i));
            __m128i a32 = _mm_srli_epi32(s, 24);
            if (_mm_movemask_epi8(_mm_cmpeq_epi32(a32, zero)) == 0xFFFF) continue;
            __m128i a2 = _mm_or_si128(a32, _mm_slli_epi32(a32, 16));
            __m128i alo = _mm_unpacklo_epi16(a2, a2), ahi = _mm_unpackhi_epi16(a2, a2);
            __m128i slo = _mm_unpacklo_epi8(s, zero), shi = _mm_unpackhi_epi8(s, zero), dlo = _mm_unpacklo_epi8(d, zero), dhi = _mm_unpackhi_epi8(d, zero);
            __m128i xlo = _mm_add_epi16(_mm_mullo_epi16(dlo, slo), c128); xlo = _mm_srli_epi16(_mm_add_epi16(xlo, _mm_srli_epi16(xlo, 8)), 8);
            __m128i xhi = _mm_add_epi16(_mm_mullo_epi16(dhi, shi), c128); xhi = _mm_srli_epi16(_mm_add_epi16(xhi, _mm_srli_epi16(xhi, 8)), 8);
            __m128i tlo = _mm_add_epi16(_mm_add_epi16(_mm_mullo_epi16(xlo, alo), _mm_mullo_epi16(dlo, _mm_sub_epi16(c255_16, alo))), c128);
            __m128i thi = _mm_add_epi16(_mm_add_epi16(_mm_mullo_epi16(xhi, ahi), _mm_mullo_epi16(dhi, _mm_sub_epi16(c255_16, ahi))), c128);
            tlo = _mm_srli_epi16(_mm_add_epi16(tlo, _mm_srli_epi16(tlo, 8)), 8);
            thi = _mm_srli_epi16(_mm_add_epi16(thi, _mm_srli_epi16(thi, 8)), 8);
            __m128i out = _mm_packus_epi16(tlo, thi);
            _mm_storeu_si128((__m128i *)(dp + i), _mm_or_si128(_mm_and_si128(out, keep_rgb), _mm_and_si128(d, keep_a)));
        }
    }
#elif SG_NEON
    if (comp == COMP_MULTIPLY) {
        const uint16x8_t c255_16 = vdupq_n_u16(255), c128 = vdupq_n_u16(128);
        const uint32x4_t keep_a = vdupq_n_u32(0xFF000000u), keep_rgb = vdupq_n_u32(0x00FFFFFFu);
        for (; i + 4 <= n; i += 4) {
            uint32x4_t s = vld1q_u32(sp + i), d = vld1q_u32(dp + i);
            uint32x4_t a32 = vshrq_n_u32(s, 24);
            if (vmaxvq_u32(a32) == 0) continue;
            uint16x8_t a2 = vreinterpretq_u16_u32(vorrq_u32(a32, vshlq_n_u32(a32, 16)));
            uint16x8_t alo = vzip1q_u16(a2, a2), ahi = vzip2q_u16(a2, a2);
            uint8x16_t s8 = vreinterpretq_u8_u32(s), d8 = vreinterpretq_u8_u32(d);
            uint16x8_t slo = vmovl_u8(vget_low_u8(s8)), shi = vmovl_high_u8(s8), dlo = vmovl_u8(vget_low_u8(d8)), dhi = vmovl_high_u8(d8);
            uint16x8_t xlo = vaddq_u16(vmulq_u16(dlo, slo), c128); xlo = vshrq_n_u16(vaddq_u16(xlo, vshrq_n_u16(xlo, 8)), 8);
            uint16x8_t xhi = vaddq_u16(vmulq_u16(dhi, shi), c128); xhi = vshrq_n_u16(vaddq_u16(xhi, vshrq_n_u16(xhi, 8)), 8);
            uint16x8_t tlo = vaddq_u16(vaddq_u16(vmulq_u16(xlo, alo), vmulq_u16(dlo, vsubq_u16(c255_16, alo))), c128);
            uint16x8_t thi = vaddq_u16(vaddq_u16(vmulq_u16(xhi, ahi), vmulq_u16(dhi, vsubq_u16(c255_16, ahi))), c128);
            tlo = vshrq_n_u16(vaddq_u16(tlo, vshrq_n_u16(tlo, 8)), 8);
            thi = vshrq_n_u16(vaddq_u16(thi, vshrq_n_u16(thi, 8)), 8);
            uint32x4_t out = vreinterpretq_u32_u8(vcombine_u8(vqmovn_u16(tlo), vqmovn_u16(thi)));
            vst1q_u32(dp + i, vorrq_u32(vandq_u32(out, keep_rgb), vandq_u32(d, keep_a)));
        }
    }
#endif
    for (; i < n; i++) if ((sp[i] >> 24) || comp == COMP_SRC_IN) dp[i] = r_comp_px(sp[i], dp[i], comp);
}

void r_fill_blend(Surf *d, IRect r, uint32_t c, int comp) {
    if (comp == COMP_OVER) {
        if ((c >> 24) == 0) return;
        for (int y = r.y; y < r.y + r.h; y++) r_span_fill_over(d->px + (size_t)y * d->w + r.x, r.w, c);
        return;
    }
    if (comp == COMP_MULTIPLY || comp == COMP_LIGHTER || comp == COMP_SRC_ATOP) {
        for (int y = r.y; y < r.y + r.h; y++) r_span_fill_comp(d->px + (size_t)y * d->w + r.x, r.w, c, comp);
        return;
    }
    for (int y = r.y; y < r.y + r.h; y++) {
        uint32_t *row = d->px + (size_t)y * d->w + r.x;
        for (int x = 0; x < r.w; x++) row[x] = r_comp_px(c, row[x], comp);
    }
}

/* One row of a blit. `sp` is the source row already in destination order (flipped / scaled by the caller). */
static inline void blit_row(uint32_t *dp, const uint32_t *sp, int n, int alpha, int comp, int kind) {
    if (comp == COMP_OVER) {
        if (kind == KIND_OPAQUE && alpha >= 255) { if (dp != sp) memcpy(dp, sp, (size_t)n * 4); for (int i = 0; i < n; i++) dp[i] |= 0xFF000000u; return; }
        r_span_over(dp, sp, n, alpha, kind == KIND_OPAQUE ? SRC_OPAQUE : kind == KIND_KEY ? SRC_KEY : SRC_ALPHA);
        return;
    }
    for (int x = 0; x < n; x++) {
        uint32_t p = sp[x];
        int a = kind == KIND_OPAQUE ? 255 : (int)(p >> 24);
        if (kind == KIND_KEY && a) a = 255;
        if (alpha < 255) a = a * alpha / 255;
        if (!a && comp != COMP_SRC_IN) continue;
        dp[x] = r_comp_px((p & 0x00FFFFFFu) | ((uint32_t)a << 24), dp[x], comp);
    }
}

void r_blit(Surf *d, const Surf *src, int sx, int sy, int sw, int sh, int dx, int dy, int dw, int dh, int flip_x, int flip_y,
            int alpha, int comp, int kind, IRect clip) {
    if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) return;
    IRect dest = irect_clip(irect(dx, dy, dw, dh), clip);
    dest = irect_clip(dest, irect(0, 0, d->w, d->h));
    if (irect_empty(dest)) return;
    int unscaled = (dw == sw && dh == sh && !flip_x && !flip_y);
    if (unscaled) {
        int ox = dest.x - dx, oy = dest.y - dy;
        for (int y = 0; y < dest.h; y++)
            blit_row(d->px + (size_t)(dest.y + y) * d->w + dest.x, src->px + (size_t)(sy + oy + y) * src->w + sx + ox, dest.w, alpha, comp, kind);
        return;
    }
    /* scaled or flipped: gather each source row into destination order once, then blend it like any other row */
    int *cols = r_scratch(0, sizeof(int) * (size_t)dest.w);
    uint32_t *tmp = r_scratch(1, sizeof(uint32_t) * (size_t)dest.w);
    for (int x = 0; x < dest.w; x++) {
        int rx = dest.x + x - dx;
        int scol = (int)(((int64_t)rx * sw) / dw);
        if (flip_x) scol = sw - 1 - scol;
        cols[x] = scol < 0 ? 0 : scol >= sw ? sw - 1 : scol;
    }
    int last_srow = -1;
    for (int y = dest.y; y < dest.y + dest.h; y++) {
        int ry = y - dy;
        int srow = (int)(((int64_t)ry * sh) / dh);
        if (flip_y) srow = sh - 1 - srow;
        if (srow < 0) srow = 0;
        if (srow >= sh) srow = sh - 1;
        if (srow != last_srow) {                          /* enlarged pictures repeat source rows: gather once */
            const uint32_t *sp = src->px + (size_t)(sy + srow) * src->w + sx;
            for (int x = 0; x < dest.w; x++) tmp[x] = sp[cols[x]];
            last_srow = srow;
        }
        blit_row(d->px + (size_t)y * d->w + dest.x, tmp, dest.w, alpha, comp, kind);
    }
}

void r_mask(Surf *d, const uint8_t *mask, int mw, int mh, int dx, int dy, uint32_t c, int comp, IRect clip) {
    IRect dest = irect_clip(irect(dx, dy, mw, mh), clip);
    dest = irect_clip(dest, irect(0, 0, d->w, d->h));
    if (irect_empty(dest)) return;
    int ca = PA(c);
    uint32_t rgb = c & 0x00FFFFFFu;
    for (int y = dest.y; y < dest.y + dest.h; y++) {
        const uint8_t *mp = mask + (size_t)(y - dy) * mw - dx;
        uint32_t *dp = d->px + (size_t)y * d->w;
        int x = dest.x, xe = dest.x + dest.w;
        if (comp == COMP_OVER) {
            while (x < xe) {
                while (x < xe && !mp[x]) x++;                           /* skip empty coverage fast */
                for (; x < xe && mp[x]; x++) {
                    int a = ca == 255 ? mp[x] : mp[x] * ca / 255;
                    if (a >= 255) dp[x] = rgb | 0xFF000000u;
                    else if (a) dp[x] = over(rgb | ((uint32_t)a << 24), dp[x], a);
                }
            }
            continue;
        }
        for (; x < xe; x++) {
            int a = mp[x] * ca / 255;
            if (!a) continue;
            dp[x] = r_comp_px(rgb | ((uint32_t)a << 24), dp[x], comp);
        }
    }
}

typedef struct { float x0, y0, x1, y1; } Edge;

void r_poly(Surf *d, const float *pts, int n, uint32_t color, int comp, IRect clip) {
    if (n < 3) return;
    IRect area = irect_clip(clip, irect(0, 0, d->w, d->h));
    if (irect_empty(area)) return;
    float miny = pts[1], maxy = pts[1];
    for (int i = 1; i < n; i++) { if (pts[i * 2 + 1] < miny) miny = pts[i * 2 + 1]; if (pts[i * 2 + 1] > maxy) maxy = pts[i * 2 + 1]; }
    int y0 = (int)floorf(miny), y1 = (int)ceilf(maxy);
    if (y0 < area.y) y0 = area.y;
    if (y1 > area.y + area.h - 1) y1 = area.y + area.h - 1;
    float *xs = r_scratch(2, sizeof(float) * (size_t)(n + 2));
    for (int y = y0; y <= y1; y++) {
        float sy = y + 0.5f;
        int cnt = 0;
        for (int i = 0; i < n; i++) {
            float ax = pts[i * 2], ay = pts[i * 2 + 1];
            int j = (i + 1) % n;
            float bx = pts[j * 2], by = pts[j * 2 + 1];
            if ((ay <= sy && by > sy) || (by <= sy && ay > sy)) xs[cnt++] = ax + (sy - ay) * (bx - ax) / (by - ay);
        }
        for (int a = 1; a < cnt; a++) { float v = xs[a]; int b = a - 1; while (b >= 0 && xs[b] > v) { xs[b + 1] = xs[b]; b--; } xs[b + 1] = v; }
        for (int k = 0; k + 1 < cnt; k += 2) {
            int xa = (int)ceilf(xs[k] - 0.5f), xb = (int)ceilf(xs[k + 1] - 0.5f) - 1;
            if (xb < xa && xs[k + 1] > xs[k]) xb = xa;        /* thin slivers still touch one pixel */
            if (xa < area.x) xa = area.x;
            if (xb > area.x + area.w - 1) xb = area.x + area.w - 1;
            if (xa > xb) continue;
            if (comp == COMP_OVER && PA(color) >= 255) r_fill_solid(d, irect(xa, y, xb - xa + 1, 1), color);
            else r_fill_blend(d, irect(xa, y, xb - xa + 1, 1), color, comp);
        }
    }
}

static void line_seg(Surf *d, int x0, int y0, int x1, int y1, int width, uint32_t color, int comp, IRect clip) {
    int dx = abs(x1 - x0), dy = abs(y1 - y0), sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1, err = dx - dy;
    int half = (width - 1) / 2;
    int xmajor = dx > dy;                  /* pygame draws a perpendicular span of `width` pixels at every step of the line */
    for (;;) {
        IRect r = irect_clip(xmajor ? irect(x0, y0 - half, 1, width) : irect(x0 - half, y0, width, 1), clip);
        r = irect_clip(r, irect(0, 0, d->w, d->h));
        if (!irect_empty(r)) { if (comp == COMP_OVER && PA(color) >= 255) r_fill_solid(d, r, color); else r_fill_blend(d, r, color, comp); }
        if (x0 == x1 && y0 == y1) break;
        int e2 = 2 * err;
        if (e2 > -dy) { err -= dy; x0 += sx; }
        if (e2 < dx) { err += dx; y0 += sy; }
    }
}

void r_lines(Surf *d, const int *pts, int n, int closed, int width, uint32_t color, int comp, IRect clip) {
    if (n < 2) return;
    for (int i = 0; i + 1 < n; i++) line_seg(d, pts[i * 2], pts[i * 2 + 1], pts[i * 2 + 2], pts[i * 2 + 3], width, color, comp, clip);
    if (closed && n > 2) line_seg(d, pts[(n - 1) * 2], pts[(n - 1) * 2 + 1], pts[0], pts[1], width, color, comp, clip);
}
