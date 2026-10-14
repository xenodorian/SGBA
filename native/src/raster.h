/* Pixel surfaces and the drawing primitives. ARGB8888, straight (non-premultiplied) alpha, row stride = width. */
#ifndef RASTER_H
#define RASTER_H
#include <stdint.h>
#include <stddef.h>

typedef struct { int x, y, w, h; } IRect;

typedef struct {
    int w, h;
    uint32_t *px;
    int owns;           /* px is freed with the surface */
    size_t nbytes;      /* what surf_create allocated, for surf_live_bytes() */
} Surf;

enum { COMP_OVER = 0, COMP_MULTIPLY, COMP_LIGHTER, COMP_SRC_IN, COMP_SRC_ATOP };
enum { KIND_OPAQUE = 0, KIND_KEY = 1, KIND_ALPHA = 2 };

#define RGBA(r, g, b, a) (((uint32_t)(a) << 24) | ((uint32_t)(r) << 16) | ((uint32_t)(g) << 8) | (uint32_t)(b))
#define PA(p) ((int)((p) >> 24))
#define PR(p) ((int)(((p) >> 16) & 255))
#define PG(p) ((int)(((p) >> 8) & 255))
#define PB(p) ((int)((p) & 255))

static inline IRect irect(int x, int y, int w, int h) { IRect r = { x, y, w, h }; return r; }
static inline int irect_empty(IRect r) { return r.w <= 0 || r.h <= 0; }
IRect irect_clip(IRect a, IRect b);
IRect irect_union(IRect a, IRect b);
int irect_collide(IRect a, IRect b);
int irect_contains(IRect outer, IRect inner);

void *r_scratch(int slot, size_t bytes);     /* thread-local grow-only buffer; slots 0 to 3 */
Surf *surf_create(int w, int h);
void surf_free(Surf *s);
size_t surf_live_bytes(void);                    /* pixel memory held by all surfaces: images, canvases, copies */
void surf_fill_all(Surf *s, uint32_t argb);

uint32_t r_comp_px(uint32_t src, uint32_t dst, int comp);   /* one pixel; src carries its own alpha */

/* Fill `r` (already clipped to the surface and clip by the caller) with colour c (alpha ignored: a solid fill). */
void r_fill_solid(Surf *d, IRect r, uint32_t c);
/* Blend colour c (with its alpha) over r using composite mode comp. */
void r_fill_blend(Surf *d, IRect r, uint32_t c, int comp);
/* Copy/blend a source crop into d. dst rect (dx,dy,dw,dh) is scaled from src crop (sx,sy,sw,sh) with nearest sampling; flips mirror.
   `clip` limits what is written. alpha 0..255 multiplies the source alpha. kind: KIND_OPAQUE copies, KIND_KEY skips transparent
   pixels, KIND_ALPHA blends. */
void r_blit(Surf *d, const Surf *src, int sx, int sy, int sw, int sh, int dx, int dy, int dw, int dh, int flip_x, int flip_y,
            int alpha, int comp, int kind, IRect clip);
/* Filled polygon (even-odd, pixel-centre sampling) from points in device space. */
void r_poly(Surf *d, const float *pts, int n, uint32_t color, int comp, IRect clip);
/* Thick polyline with square ends, integer points. */
void r_lines(Surf *d, const int *pts, int n, int closed, int width, uint32_t color, int comp, IRect clip);
/* Alpha mask (8-bit coverage, w*h) painted with colour c. */
void r_mask(Surf *d, const uint8_t *mask, int mw, int mh, int dx, int dy, uint32_t c, int comp, IRect clip);


/* Span kernels (SSE2 / NEON), and their scalar references for the differential test. mode: 0 straight alpha, 1 opaque source, 2 colour key. */
void r_span_over(uint32_t *dp, const uint32_t *sp, int n, int ga, int mode);
void r_span_over_ref(uint32_t *dp, const uint32_t *sp, int n, int ga, int mode);
void r_span_fill_over(uint32_t *dp, int n, uint32_t c);
void r_span_fill_over_ref(uint32_t *dp, int n, uint32_t c);
void r_span_fill_comp(uint32_t *dp, int n, uint32_t c, int comp);
void r_span_fill_comp_ref(uint32_t *dp, int n, uint32_t c, int comp);
void r_span_comp(uint32_t *dp, const uint32_t *sp, int n, int comp);     /* per-pixel colours with their own alpha */
void r_span_comp_ref(uint32_t *dp, const uint32_t *sp, int n, int comp);

#endif
