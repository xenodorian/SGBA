/* Images: lazy PNG decode into 32-bit surfaces with a byte budget, classified like the Python store (opaque / key / alpha). */
#define STB_IMAGE_IMPLEMENTATION
#define STBI_NO_STDIO
#define STBI_ONLY_PNG
#define STBI_ONLY_JPEG
#define STBI_ONLY_GIF
#define STBI_ONLY_BMP
#include "stb_image.h"
#include <ctype.h>
#include "image.h"
#include "json.h"

#define N() host_natives((Host *)JS_GetContextOpaque(ctx))
#define NATIVE(name) static JSValue name(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)

struct ImageStore *imagestore_create(size_t budget, int alpha_keep) {
    struct ImageStore *s = calloc(1, sizeof *s);
    s->budget = budget;
    s->alpha_keep = alpha_keep;
    return s;
}

static int add_entry(struct ImageStore *s, const char *path, int w, int h, int pinned) {
    if (s->count == s->cap) { s->cap = s->cap ? s->cap * 2 : 256; s->e = realloc(s->e, s->cap * sizeof(Image)); }
    Image *e = &s->e[s->count];
    memset(e, 0, sizeof *e);
    e->path = path ? strdup(path) : NULL;
    e->w = w; e->h = h; e->pinned = pinned; e->kind = -1;
    return IMAGE_ID_BASE + s->count++;
}

/* RGBA bytes from stb -> ARGB32 surface, deciding opaque/key/alpha like sgba_gfx.to_compact. */
static Surf *classify(const unsigned char *rgba, int w, int h, int channels, int alpha_keep, int *kind) {
    Surf *s = surf_create(w, h);
    size_t total = (size_t)w * h, visible = 0, solid = 0;
    for (size_t i = 0; i < total; i++) {
        unsigned r = rgba[i * 4], g = rgba[i * 4 + 1], b = rgba[i * 4 + 2], a = rgba[i * 4 + 3];
        s->px[i] = (a << 24) | (r << 16) | (g << 8) | b;
        if (a > 0) visible++;
        if (a == 255) solid++;
    }
    int has_alpha = (channels == 2 || channels == 4);
    if (!has_alpha || solid == total) {
        for (size_t i = 0; i < total; i++) s->px[i] |= 0xFF000000u;
        *kind = KIND_OPAQUE;
        return s;
    }
    size_t partial = visible - solid;
    if (partial && (alpha_keep || partial * 20 > (visible ? visible : 1))) { *kind = KIND_ALPHA; return s; }
    if (partial) {                                      /* faint edges: keep pixels at least half opaque, drop the rest */
        for (size_t i = 0; i < total; i++) {
            uint32_t p = s->px[i];
            s->px[i] = PA(p) > 127 ? (p | 0xFF000000u) : 0;
        }
    }
    *kind = KIND_KEY;
    return s;
}

static Surf *decode_memory(const unsigned char *data, size_t len, int alpha_keep, int *kind) {
    int w, h, ch;
    unsigned char *px = stbi_load_from_memory(data, (int)len, &w, &h, &ch, 4);
    if (!px) return NULL;
    Surf *s = classify(px, w, h, ch, alpha_keep, kind);
    stbi_image_free(px);
    return s;
}

int image_register_file(Natives *n, const char *path, int *w, int *h) {
    struct ImageStore *s = n->images;
    for (int i = 0; i < s->count; i++) if (s->e[i].path && !strcmp(s->e[i].path, path)) { *w = s->e[i].w; *h = s->e[i].h; return IMAGE_ID_BASE + i; }
    size_t len;
    unsigned char *data = sg_read_file(path, &len);
    if (!data) return -1;
    int ok = stbi_info_from_memory(data, (int)len, w, h, NULL);
    free(data);
    if (!ok) return -1;
    return add_entry(s, path, *w, *h, 0);
}

static void evict(struct ImageStore *s, size_t need) {
    while (s->bytes + need > s->budget) {
        int best = -1;
        for (int i = 0; i < s->count; i++) {
            Image *e = &s->e[i];
            if (e->surf && !e->pinned && (best < 0 || e->used < s->e[best].used)) best = i;
        }
        if (best < 0) break;
        Image *e = &s->e[best];
        s->bytes -= e->bytes;
        surf_free(e->surf);
        e->surf = NULL;
        e->bytes = 0;
        s->evictions++;
    }
}

/* Already-decoded image, no decode and no bookkeeping: safe to call from several threads while nothing loads or evicts. */
const Surf *image_surface_peek(Natives *n, int id, int *kind) {
    struct ImageStore *s = n->images;
    int i = id - IMAGE_ID_BASE;
    if (i < 0 || i >= s->count || !s->e[i].surf) return NULL;
    if (kind) *kind = s->e[i].kind;
    return s->e[i].surf;
}

const Surf *image_surface(Natives *n, int id, int *kind) {
    struct ImageStore *s = n->images;
    int i = id - IMAGE_ID_BASE;
    if (i < 0 || i >= s->count) return NULL;
    Image *e = &s->e[i];
    e->used = ++s->tick;
    if (!e->surf) {
        if (!e->path) return NULL;
        size_t len;
        unsigned char *data = sg_read_file(e->path, &len);
        if (!data) return NULL;
        int k;
        Surf *surf = decode_memory(data, len, s->alpha_keep, &k);
        free(data);
        if (!surf) { sg_log("warn", "image failed to decode: %s", e->path); return NULL; }
        size_t b = (size_t)surf->w * surf->h * 4;
        evict(s, b);
        e->surf = surf; e->kind = k; e->bytes = b;
        s->bytes += b;
        if (s->bytes > s->peak) s->peak = s->bytes;
        s->loads++;
    }
    if (kind) *kind = e->kind;
    return e->surf;
}

/* decode the images whose path (relative to the game folder) matches a pattern from sgba_ui.json "preload" */
void images_preload_patterns(Natives *n, const char *root, char **patterns, int np) {
    struct ImageStore *s = n->images;
    size_t rl = strlen(root);
    for (int i = 0; i < s->count; i++) {
        Image *e = &s->e[i];
        if (!e->path || strncmp(e->path, root, rl)) continue;
        const char *rel = e->path + rl;
        while (*rel == '/') rel++;
        for (int k = 0; k < np; k++) if (sg_glob(patterns[k], rel)) { image_surface(n, IMAGE_ID_BASE + i, NULL); break; }
    }
}

int image_size(Natives *n, int id, int *w, int *h) {
    struct ImageStore *s = n->images;
    int i = id - IMAGE_ID_BASE;
    if (i < 0 || i >= s->count) return 0;
    *w = s->e[i].w; *h = s->e[i].h;
    return 1;
}

int image_kind(Natives *n, int id) {
    struct ImageStore *s = n->images;
    int i = id - IMAGE_ID_BASE;
    return (i < 0 || i >= s->count) ? -1 : s->e[i].kind;
}

/* ---------------------------------------------------------------- JS side */

static unsigned char *b64(const char *in, size_t *outlen) {
    static signed char tab[256];
    if (!tab['A'] && !tab['B']) { memset(tab, -1, sizeof tab); const char *al = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"; for (int i = 0; i < 64; i++) tab[(unsigned char)al[i]] = (signed char)i; }
    size_t n = strlen(in);
    unsigned char *out = malloc(n * 3 / 4 + 4);
    size_t o = 0; unsigned acc = 0; int bits = 0;
    for (size_t i = 0; i < n; i++) {
        int v = tab[(unsigned char)in[i]];
        if (v < 0) continue;
        acc = (acc << 6) | (unsigned)v; bits += 6;
        if (bits >= 8) { bits -= 8; out[o++] = (unsigned char)((acc >> bits) & 255); }
    }
    *outlen = o;
    return out;
}

NATIVE(n_load_image) {
    Natives *n = N();
    const char *src = JS_ToCString(ctx, argv[0]);
    JSValue result = JS_NewString(ctx, "-1,0,0");
    if (!src) return result;
    char buf[96];
    if (!strncmp(src, "data:", 5)) {
        const char *comma = strchr(src, ',');
        if (comma) {
            size_t len = 0;
            unsigned char *data = NULL;
            if (strstr(src, ";base64")) data = b64(comma + 1, &len);
            else {                                       /* percent-encoded */
                size_t m = strlen(comma + 1);
                data = malloc(m + 1);
                for (size_t i = 0; i < m; i++) { char c = comma[1 + i]; if (c == '%' && i + 2 < m + 0 && isxdigit((unsigned char)comma[2 + i]) && isxdigit((unsigned char)comma[3 + i])) { char hx[3] = { comma[2 + i], comma[3 + i], 0 }; data[len++] = (unsigned char)strtol(hx, NULL, 16); i += 2; } else data[len++] = (unsigned char)c; }
            }
            int kind;
            Surf *surf = data ? decode_memory(data, len, n->images->alpha_keep, &kind) : NULL;
            free(data);
            if (surf) {
                int id = add_entry(n->images, NULL, surf->w, surf->h, 1);
                Image *e = &n->images->e[id - IMAGE_ID_BASE];
                e->surf = surf; e->kind = kind; e->bytes = (size_t)surf->w * surf->h * 4;
                snprintf(buf, sizeof buf, "%d,%d,%d", id, surf->w, surf->h);
                JS_FreeValue(ctx, result);
                result = JS_NewString(ctx, buf);
            }
        }
    } else {
        char *path = sg_resolve(host_root((Host *)JS_GetContextOpaque(ctx)), src);
        if (path) {
            int w, h, id = image_register_file(n, path, &w, &h);
            free(path);
            if (id >= 0) { snprintf(buf, sizeof buf, "%d,%d,%d", id, w, h); JS_FreeValue(ctx, result); result = JS_NewString(ctx, buf); }
        }
    }
    JS_FreeCString(ctx, src);
    return result;
}

NATIVE(n_preload) {
    Natives *n = N();
    const char *json = JS_ToCString(ctx, argv[0]);
    if (!json) return JS_UNDEFINED;
    JNode *list = json_parse(json);
    JS_FreeCString(ctx, json);
    if (list && list->type == J_ARR) {
        for (int i = 0; i < list->count; i++) {
            const char *url = json_str(list->items[i], NULL);
            if (!url) continue;
            char *path = sg_resolve(host_root((Host *)JS_GetContextOpaque(ctx)), url);
            if (!path) continue;
            int w, h, id = image_register_file(n, path, &w, &h);
            free(path);
            if (id < 0) continue;
            int dup = 0;
            for (int k = 0; k < n->preload_count; k++) if (n->preload[k] == id) dup = 1;
            if (dup) continue;
            if (n->preload_count == n->preload_cap) { n->preload_cap = n->preload_cap ? n->preload_cap * 2 : 16; n->preload = realloc(n->preload, n->preload_cap * sizeof(int)); }
            n->preload[n->preload_count++] = id;
        }
    }
    json_free(list);
    return JS_UNDEFINED;
}

void images_preload_step(Natives *n) {
    while (n->preload_count) {
        int id = n->preload[0];
        memmove(n->preload, n->preload + 1, (size_t)(n->preload_count - 1) * sizeof(int));
        n->preload_count--;
        Image *e = &n->images->e[id - IMAGE_ID_BASE];
        if (!e->surf) { image_surface(n, id, NULL); return; }
    }
}

void images_register(Host *h, Natives *n, JSContext *ctx) {
    (void)h;
    if (!n->images) { const Config *cfg = host_config(h); n->images = imagestore_create((size_t)cfg->image_budget_mb * 1024 * 1024, cfg->alpha_keep); }
    JSValue g = JS_GetGlobalObject(ctx);
    JS_SetPropertyStr(ctx, g, "__py_load_image", JS_NewCFunction(ctx, n_load_image, "__py_load_image", 1));
    JS_SetPropertyStr(ctx, g, "__py_preload", JS_NewCFunction(ctx, n_preload, "__py_preload", 1));
    JS_FreeValue(ctx, g);
}

void images_shutdown(Natives *n) {
    struct ImageStore *s = n->images;
    if (!s) return;
    for (int i = 0; i < s->count; i++) { free(s->e[i].path); surf_free(s->e[i].surf); }
    free(s->e);
    free(s);
    n->images = NULL;
}
