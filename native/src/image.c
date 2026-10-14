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


/* ---- read-ahead: a worker thread decodes images in the background so the frame that first draws one does not pay for it ----
 * The worker only decodes into Image.ready (at most READY_CAP bytes); the main thread installs a ready image at its first use,
 * exactly where the old code decoded it, so budget, eviction order and pixels are unchanged. */
#define READY_CAP (24u * 1024 * 1024)
#define READAHEAD 6
static double clock_ms(void);
#ifdef _WIN32
static void pf_start(struct ImageStore *s) { (void)s; }
static void pf_stop(struct ImageStore *s) { (void)s; }
static void pf_enqueue(struct ImageStore *s, int i, int front) { (void)s; (void)i; (void)front; }
static void pf_lock(struct ImageStore *s) { (void)s; }
static void pf_unlock(struct ImageStore *s) { (void)s; }
static Surf *pf_take(struct ImageStore *s, int i, int *kind) { (void)s; (void)i; (void)kind; return NULL; }
#else
static unsigned char *sg_read_file_(const char *path, size_t *len) { return sg_read_file(path, len); }
static Surf *decode_memory(const unsigned char *data, size_t len, int alpha_keep, int *kind);

static void pf_lock(struct ImageStore *s) { if (s->thr_on) pthread_mutex_lock(&s->mu); }
static void pf_unlock(struct ImageStore *s) { if (s->thr_on) pthread_mutex_unlock(&s->mu); }

static void *pf_worker(void *arg) {
    struct ImageStore *s = arg;
    pthread_mutex_lock(&s->mu);
    for (;;) {
        while (!s->quit && (s->qn == 0 || s->ready_bytes >= READY_CAP)) pthread_cond_wait(&s->cv, &s->mu);
        if (s->quit) break;
        int i = s->queue[0];
        memmove(s->queue, s->queue + 1, (size_t)(s->qn - 1) * sizeof(int));
        s->qn--;
        Image *e = &s->e[i];
        if (e->pf != 1 || e->surf || !e->path) continue;           /* decoded or needed by the main thread meanwhile */
        e->pf = 2;
        char *path = strdup(e->path);
        int alpha_keep = s->alpha_keep;
        pthread_mutex_unlock(&s->mu);
        size_t len;
        unsigned char *data = sg_read_file_(path, &len);
        int k = 0;
        Surf *surf = data ? decode_memory(data, len, alpha_keep, &k) : NULL;
        free(data);
        free(path);
        pthread_mutex_lock(&s->mu);
        e = &s->e[i];
        if (surf) { e->ready = surf; e->ready_kind = k; e->pf = 3; s->ready_bytes += (size_t)surf->w * surf->h * 4; }
        else e->pf = 0;
        pthread_cond_broadcast(&s->cv);
    }
    pthread_mutex_unlock(&s->mu);
    return NULL;
}

static void pf_start(struct ImageStore *s) {
    const char *off = getenv("SGBA_NO_READAHEAD");
    if (off && *off == '1') return;
    pthread_mutex_init(&s->mu, NULL);
    pthread_cond_init(&s->cv, NULL);
    if (pthread_create(&s->thr, NULL, pf_worker, s) == 0) s->thr_on = 1;
}

static void pf_stop(struct ImageStore *s) {
    if (!s->thr_on) return;
    pthread_mutex_lock(&s->mu);
    s->quit = 1;
    pthread_cond_broadcast(&s->cv);
    pthread_mutex_unlock(&s->mu);
    pthread_join(s->thr, NULL);
    s->thr_on = 0;
    for (int i = 0; i < s->count; i++) if (s->e[i].ready) { surf_free(s->e[i].ready); s->e[i].ready = NULL; }
    free(s->queue);
    s->queue = NULL;
}

/* queue image i for decoding; `front` puts it ahead of the older entries (what the game is about to need) */
static void pf_enqueue(struct ImageStore *s, int i, int front) {
    if (!s->thr_on) return;
    pthread_mutex_lock(&s->mu);
    Image *e = &s->e[i];
    if (!e->surf && e->path && e->pf == 0) {
        if (s->qn == s->qcap) { s->qcap = s->qcap ? s->qcap * 2 : 256; s->queue = realloc(s->queue, (size_t)s->qcap * sizeof(int)); }
        if (front) { memmove(s->queue + 1, s->queue, (size_t)s->qn * sizeof(int)); s->queue[0] = i; }
        else s->queue[s->qn] = i;
        s->qn++;
        e->pf = 1;
        pthread_cond_signal(&s->cv);
    }
    pthread_mutex_unlock(&s->mu);
}

/* the main thread needs image i: wait for the worker if it is decoding it, take the result if it is ready */
static Surf *pf_take(struct ImageStore *s, int i, int *kind) {
    if (!s->thr_on) return NULL;
    pthread_mutex_lock(&s->mu);
    Image *e = &s->e[i];
    int waited = e->pf == 2;
    while (e->pf == 2) pthread_cond_wait(&s->cv, &s->mu);
    Surf *r = NULL;
    if (e->pf == 3) {
        r = e->ready; *kind = e->ready_kind;
        if (waited) s->ra_wait++; else s->ra_hit++;
        s->ready_bytes -= (size_t)r->w * r->h * 4;
        e->ready = NULL; e->pf = 0;
        pthread_cond_broadcast(&s->cv);
    } else { s->ra_miss++; if (e->pf == 1) e->pf = 0; }                              /* still queued: the main thread decodes it itself, the worker skips it */
    pthread_mutex_unlock(&s->mu);
    return r;
}
#endif

size_t image_bytes_total(Natives *n) {
    struct ImageStore *s = n->images;
    if (!s) return 0;
    size_t b = s->bytes;
#ifndef _WIN32
    b += s->ready_bytes;
#endif
    return b;
}

struct ImageStore *imagestore_create(size_t budget, int alpha_keep) {
    struct ImageStore *s = calloc(1, sizeof *s);
    s->budget = budget;
    s->alpha_keep = alpha_keep;
    pf_start(s);
    return s;
}

static int add_entry(struct ImageStore *s, const char *path, int w, int h, int pinned) {
    pf_lock(s);                                                       /* the worker reads s->e: do not move it under its feet */
    if (s->count == s->cap) { s->cap = s->cap ? s->cap * 2 : 256; s->e = realloc(s->e, s->cap * sizeof(Image)); }
    Image *e = &s->e[s->count];
    memset(e, 0, sizeof *e);
    e->path = path ? strdup(path) : NULL;
    e->w = w; e->h = h; e->pinned = pinned; e->kind = -1;
    int id = IMAGE_ID_BASE + s->count++;
    pf_unlock(s);
    if (path && !pinned) pf_enqueue(s, id - IMAGE_ID_BASE, 0);
    return id;
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

static void trace_note(struct ImageStore *s, int i);

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

#include <time.h>
static double clock_ms(void) { struct timespec t; timespec_get(&t, TIME_UTC); return (double)t.tv_sec * 1000.0 + (double)t.tv_nsec / 1e6; }

void image_stats(Natives *n, int *loads, double *ms, double *max_ms, int reset) {
    struct ImageStore *s = n->images;
    if (!s) { *loads = 0; *ms = *max_ms = 0; return; }
    *loads = s->loads; *ms = s->decode_ms; *max_ms = s->decode_max;
    if (reset) { s->loads = 0; s->decode_ms = s->decode_max = 0; }
}

const Surf *image_surface(Natives *n, int id, int *kind) {
    struct ImageStore *s = n->images;
    int i = id - IMAGE_ID_BASE;
    if (i < 0 || i >= s->count) return NULL;
    Image *e = &s->e[i];
    e->used = ++s->tick;
    if (!e->surf) {
        if (!e->path) return NULL;
        int k = 0;
        double t0 = clock_ms();
        Surf *surf = pf_take(s, i, &k);                               /* decoded ahead of time (or waited for) */
        if (!surf) {
            size_t len;
            unsigned char *data = sg_read_file(e->path, &len);
            if (!data) return NULL;
            surf = decode_memory(data, len, s->alpha_keep, &k);
            free(data);
        }
        for (int j = i + 1; j <= i + READAHEAD && j < s->count; j++) pf_enqueue(s, j, 1);   /* images registered next to this one are usually the same scene */
        double dt = clock_ms() - t0;
        s->decode_ms += dt;
        if (dt > s->decode_max) s->decode_max = dt;
        if (!surf) { sg_log("warn", "image failed to decode: %s", e->path); return NULL; }
        size_t b = (size_t)surf->w * surf->h * 4;
        evict(s, b);
        e->surf = surf; e->kind = k; e->bytes = b;
        s->bytes += b;
        if (s->bytes > s->peak) s->peak = s->bytes;
        s->loads++;
    }
    trace_note(s, i);
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


/* ---- first-use trace: the order in which a game first drew its images last time. Next run the read-ahead worker decodes them in
 * that order from the start, so the frame that first draws an image usually finds it ready. Only for unpacked .sgba games
 * (their private cache folder); any problem with the file is ignored. */
static int png_dims(const char *path, int *w, int *h) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    unsigned char b[32];
    size_t got = fread(b, 1, sizeof b, f);
    fclose(f);
    if (got < 24 || memcmp(b, "\x89PNG\r\n\x1a\n", 8) != 0) return 0;
    *w = (int)((b[16] << 24) | (b[17] << 16) | (b[18] << 8) | b[19]);
    *h = (int)((b[20] << 24) | (b[21] << 16) | (b[22] << 8) | b[23]);
    return *w > 0 && *h > 0;
}

static void trace_load(struct ImageStore *s, const char *root) {
    if (!root || !strstr(root, "/cache/")) return;
    size_t n = strlen(root) + 24;
    s->trace_path = malloc(n);
    snprintf(s->trace_path, n, "%s/.image-order", root);
    size_t len;
    char *txt = (char *)sg_read_file(s->trace_path, &len);
    if (!txt) return;
    txt = realloc(txt, len + 1);
    txt[len] = 0;
    int added = 0;
    for (char *line = strtok(txt, "\n"); line && added < 1500; line = strtok(NULL, "\n")) {
        int w, h;
        if (!png_dims(line, &w, &h)) continue;
        int dup = 0;
        for (int i = 0; i < s->count && !dup; i++) if (s->e[i].path && !strcmp(s->e[i].path, line)) dup = 1;
        if (dup) continue;
        add_entry(s, line, w, h, 0);                                  /* also queues it for the worker, in file order */
        added++;
    }
    free(txt);
    s->trace_loaded = added;
}

static void trace_note(struct ImageStore *s, int i) {
    Image *e = &s->e[i];
    if (e->traced || !e->path || e->pinned) return;
    e->traced = 1;
    if (s->norder == s->ocap) { s->ocap = s->ocap ? s->ocap * 2 : 128; s->order = realloc(s->order, (size_t)s->ocap * sizeof(int)); }
    s->order[s->norder++] = i;
}

static void trace_save(struct ImageStore *s) {
    if (!s->trace_path || s->norder == 0) return;
    char tmp[2400];
    snprintf(tmp, sizeof tmp, "%s.tmp", s->trace_path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    for (int k = 0; k < s->norder && k < 1500; k++) fprintf(f, "%s\n", s->e[s->order[k]].path);
    fclose(f);
    rename(tmp, s->trace_path);
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
    if (!n->images) { const Config *cfg = host_config(h); n->images = imagestore_create((size_t)cfg->image_budget_mb * 1024 * 1024, cfg->alpha_keep); trace_load(n->images, host_root(h)); }
    JSValue g = JS_GetGlobalObject(ctx);
    JS_SetPropertyStr(ctx, g, "__py_load_image", JS_NewCFunction(ctx, n_load_image, "__py_load_image", 1));
    JS_SetPropertyStr(ctx, g, "__py_preload", JS_NewCFunction(ctx, n_preload, "__py_preload", 1));
    JS_FreeValue(ctx, g);
}

void images_shutdown(Natives *n) {
    struct ImageStore *s = n->images;
    if (!s) return;
    pf_stop(s);
    trace_save(s);
    free(s->order);
    free(s->trace_path);
    for (int i = 0; i < s->count; i++) { free(s->e[i].path); surf_free(s->e[i].surf); }
    free(s->e);
    free(s);
    n->images = NULL;
}
