/* Text rendering with stb_truetype and the same default font pygame uses. */
#define STB_TRUETYPE_IMPLEMENTATION
#include "stb_truetype.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "text.h"

extern const unsigned char font_ttf[];
static stbtt_fontinfo g_font;
static int g_ready = 0;

static int font_ready(void) {
    if (!g_ready) { if (!stbtt_InitFont(&g_font, font_ttf, 0)) return 0; g_ready = 1; }
    return 1;
}

static int next_cp(const unsigned char **pp) {
    const unsigned char *p = *pp;
    int c = *p++;
    if (c < 0x80) { *pp = p; return c; }
    int n = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : 1;
    int cp = c & (0x3F >> n);
    while (n-- && (*p & 0xC0) == 0x80) cp = (cp << 6) | (*p++ & 0x3F);
    *pp = p;
    return cp;
}

static float scale_for(int font_px) {
    int pt = (int)(font_px * 0.6875);
    if (pt < 1) pt = 1;
    return stbtt_ScaleForMappingEmToPixels(&g_font, (float)pt);
}

static void metrics(float scale, int *ascent, int *descent) {
    int a, d, gap;
    stbtt_GetFontVMetrics(&g_font, &a, &d, &gap);
    *ascent = (int)ceilf(a * scale);
    *descent = (int)floorf(d * scale);           /* negative */
}

int text_size(const char *utf8, int font_px, int bold, int italic, int *height) {
    if (!font_ready()) { if (height) *height = font_px; return 0; }
    float scale = scale_for(font_px);
    int asc, desc;
    metrics(scale, &asc, &desc);
    if (height) *height = asc - desc + 1;
    int w = 0;
    const unsigned char *p = (const unsigned char *)utf8;
    while (*p) {
        int cp = next_cp(&p), adv, lsb;
        stbtt_GetCodepointHMetrics(&g_font, cp, &adv, &lsb);
        w += (int)floorf(adv * scale + 0.5f);
    }
    (void)italic;
    return w + (bold ? 1 : 0);
}

#define CACHE_SLOTS 256
typedef struct { char *key; Mask m; unsigned long used; } Slot;
static Slot g_cache[CACHE_SLOTS];
static unsigned long g_tick = 0;
static size_t g_bytes = 0;

const Mask *text_mask(const char *utf8, int font_px, int bold, int italic) {
    if (!font_ready()) return NULL;
    char keybuf[600];
    snprintf(keybuf, sizeof keybuf, "%d/%d/%d/%.500s", font_px, bold, italic, utf8);
    int freeslot = -1, oldest = 0;
    for (int i = 0; i < CACHE_SLOTS; i++) {
        if (g_cache[i].key && !strcmp(g_cache[i].key, keybuf)) { g_cache[i].used = ++g_tick; return &g_cache[i].m; }
        if (!g_cache[i].key && freeslot < 0) freeslot = i;
        if (g_cache[i].key && g_cache[i].used < g_cache[oldest].used) oldest = i;
    }
    int slot = freeslot >= 0 ? freeslot : oldest;
    if (g_cache[slot].key) { g_bytes -= (size_t)g_cache[slot].m.w * g_cache[slot].m.h; free(g_cache[slot].key); free(g_cache[slot].m.a); }
    float scale = scale_for(font_px);
    int asc, desc, height;
    metrics(scale, &asc, &desc);
    int width = text_size(utf8, font_px, bold, italic, &height);
    int extra = (italic ? height / 4 : 0) + (bold ? 1 : 0);
    Mask m = { calloc((size_t)(width + extra + 2) * (size_t)height, 1), width + extra + 2, height };
    int x = 0;
    const unsigned char *p = (const unsigned char *)utf8;
    while (*p) {
        int cp = next_cp(&p), adv, lsb;
        stbtt_GetCodepointHMetrics(&g_font, cp, &adv, &lsb);
        int x0, y0, x1, y1;
        stbtt_GetCodepointBitmapBox(&g_font, cp, scale, scale, &x0, &y0, &x1, &y1);
        int gw = x1 - x0, gh = y1 - y0;
        if (gw > 0 && gh > 0) {
            uint8_t *bmp = calloc((size_t)gw * gh, 1);
            stbtt_MakeCodepointBitmap(&g_font, bmp, gw, gh, gw, scale, scale, cp);
            for (int row = 0; row < gh; row++) {
                int dy = asc + y0 + row;
                if (dy < 0 || dy >= m.h) continue;
                int shear = italic ? (asc - dy) / 4 : 0;
                for (int col = 0; col < gw; col++) {
                    int dx = x + x0 + col + shear;
                    if (dx < 0 || dx >= m.w) continue;
                    uint8_t v = bmp[row * gw + col];
                    if (v > m.a[dy * m.w + dx]) m.a[dy * m.w + dx] = v;
                    if (bold && dx + 1 < m.w && v > m.a[dy * m.w + dx + 1]) m.a[dy * m.w + dx + 1] = v;
                }
            }
            free(bmp);
        }
        x += (int)floorf(adv * scale + 0.5f);
    }
    g_cache[slot].key = strdup(keybuf);
    g_cache[slot].m = m;
    g_cache[slot].used = ++g_tick;
    g_bytes += (size_t)m.w * m.h;
    return &g_cache[slot].m;
}

void text_shutdown(void) {
    for (int i = 0; i < CACHE_SLOTS; i++) { free(g_cache[i].key); free(g_cache[i].m.a); g_cache[i].key = NULL; }
}
