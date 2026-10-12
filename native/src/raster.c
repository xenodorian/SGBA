/* Software drawing primitives for the canvas. */
#include <math.h>
#include <string.h>
#include <stdlib.h>
#include "raster.h"

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

Surf *surf_create(int w, int h) {
    Surf *s = calloc(1, sizeof *s);
    s->w = w; s->h = h;
    s->px = calloc((size_t)w * (size_t)h, 4);
    s->owns = 1;
    return s;
}
void surf_free(Surf *s) { if (!s) return; if (s->owns) free(s->px); free(s); }
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

void r_fill_blend(Surf *d, IRect r, uint32_t c, int comp) {
    if (comp == COMP_OVER) {
        uint32_t a = c >> 24;
        if (a == 0) return;
        for (int y = r.y; y < r.y + r.h; y++) {
            uint32_t *row = d->px + (size_t)y * d->w + r.x;
            for (int x = 0; x < r.w; x++) { uint32_t p = row[x]; row[x] = (p >> 24) == 255 ? (a == 255 ? c | 0xFF000000u : blend_opaque(c, p, a)) : over(c, p, (int)a); }
        }
        return;
    }
    for (int y = r.y; y < r.y + r.h; y++) {
        uint32_t *row = d->px + (size_t)y * d->w + r.x;
        for (int x = 0; x < r.w; x++) row[x] = r_comp_px(c, row[x], comp);
    }
}

/* Fast paths for the common case: a one-to-one copy of a crop with the normal "over" mode. */
static int blit_plain(Surf *d, const Surf *src, int sx, int sy, int dx, int dy, IRect dest, int alpha, int kind) {
    int ox = dest.x - dx, oy = dest.y - dy;                       /* offset of the first written pixel inside the crop */
    for (int y = 0; y < dest.h; y++) {
        const uint32_t *sp = src->px + (size_t)(sy + oy + y) * src->w + sx + ox;
        uint32_t *dp = d->px + (size_t)(dest.y + y) * d->w + dest.x;
        int n = dest.w;
        if (alpha >= 255) {
            if (kind == KIND_OPAQUE) memcpy(dp, sp, (size_t)n * 4);
            else if (kind == KIND_KEY) { for (int x = 0; x < n; x++) { uint32_t p = sp[x]; if (p >> 24) dp[x] = p; } }
            else {
                for (int x = 0; x < n; x++) {
                    uint32_t p = sp[x];
                    int a = (int)(p >> 24);
                    if (a == 0) continue;
                    if (a == 255) { dp[x] = p; continue; }
                    dp[x] = r_comp_px(p, dp[x], COMP_OVER);
                }
            }
        } else {
            for (int x = 0; x < n; x++) {
                uint32_t p = sp[x];
                int a = kind == KIND_OPAQUE ? 255 : (int)(p >> 24);
                if (kind == KIND_KEY && a) a = 255;
                a = a * alpha / 255;
                if (!a) continue;
                dp[x] = r_comp_px((p & 0x00FFFFFFu) | ((uint32_t)a << 24), dp[x], COMP_OVER);
            }
        }
    }
    return 1;
}

void r_blit(Surf *d, const Surf *src, int sx, int sy, int sw, int sh, int dx, int dy, int dw, int dh, int flip_x, int flip_y,
            int alpha, int comp, int kind, IRect clip) {
    if (sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) return;
    IRect dest = irect_clip(irect(dx, dy, dw, dh), clip);
    dest = irect_clip(dest, irect(0, 0, d->w, d->h));
    if (irect_empty(dest)) return;
    int unscaled = (dw == sw && dh == sh && !flip_x && !flip_y);
    if (unscaled && comp == COMP_OVER) { blit_plain(d, src, sx, sy, dx, dy, dest, alpha, kind); return; }
    /* scaled column map once per call */
    int *cols = malloc(sizeof(int) * (size_t)dest.w);
    for (int x = 0; x < dest.w; x++) {
        int rx = dest.x + x - dx;
        int scol = unscaled ? rx : (int)(((int64_t)rx * sw) / dw);
        if (flip_x) scol = sw - 1 - scol;
        cols[x] = scol < 0 ? 0 : scol >= sw ? sw - 1 : scol;
    }
    for (int y = dest.y; y < dest.y + dest.h; y++) {
        int ry = y - dy;
        int srow = unscaled ? ry : (int)(((int64_t)ry * sh) / dh);
        if (flip_y) srow = sh - 1 - srow;
        if (srow < 0) srow = 0;
        if (srow >= sh) srow = sh - 1;
        const uint32_t *sp = src->px + (size_t)(sy + srow) * src->w + sx;
        uint32_t *dp = d->px + (size_t)y * d->w + dest.x;
        for (int x = 0; x < dest.w; x++) {
            uint32_t s = sp[cols[x]];
            if (kind == KIND_OPAQUE && comp == COMP_OVER && alpha >= 255) { dp[x] = s | 0xFF000000u; continue; }
            int a = PA(s);
            if (kind == KIND_KEY) a = a ? 255 : 0;
            if (kind == KIND_OPAQUE) a = 255;
            if (alpha < 255) a = a * alpha / 255;
            if (!a && comp != COMP_SRC_IN) continue;
            dp[x] = r_comp_px((s & 0x00FFFFFFu) | ((uint32_t)a << 24), dp[x], comp);
        }
    }
    free(cols);
}

void r_mask(Surf *d, const uint8_t *mask, int mw, int mh, int dx, int dy, uint32_t c, int comp, IRect clip) {
    IRect dest = irect_clip(irect(dx, dy, mw, mh), clip);
    dest = irect_clip(dest, irect(0, 0, d->w, d->h));
    if (irect_empty(dest)) return;
    int ca = PA(c);
    for (int y = dest.y; y < dest.y + dest.h; y++) {
        const uint8_t *mp = mask + (size_t)(y - dy) * mw - dx;
        uint32_t *dp = d->px + (size_t)y * d->w;
        for (int x = dest.x; x < dest.x + dest.w; x++) {
            int a = mp[x] * ca / 255;
            if (!a) continue;
            dp[x] = r_comp_px((c & 0x00FFFFFFu) | ((uint32_t)a << 24), dp[x], comp);
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
    float *xs = malloc(sizeof(float) * (size_t)(n + 2));
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
    free(xs);
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
