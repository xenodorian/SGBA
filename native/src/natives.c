/* Functions the shim calls into: storage, files, log, and the hooks into canvas, images and audio. */
#include <ctype.h>
#include <sys/stat.h>
#include "natives.h"

#define NATIVE(name) static JSValue name(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
#define H() ((Host *)JS_GetContextOpaque(ctx))
#define N() host_natives(H())

/* ---------------------------------------------------------------- log */
NATIVE(n_log) {
    const char *lvl = JS_ToCString(ctx, argv[0]);
    const char *msg = JS_ToCString(ctx, argv[1]);
    if (lvl && !strcmp(lvl, "error")) host_note_error(H());
    sg_log(lvl ? lvl : "log", "js: %s", msg ? msg : "");
    if (lvl) JS_FreeCString(ctx, lvl);
    if (msg) JS_FreeCString(ctx, msg);
    return JS_UNDEFINED;
}

/* ---------------------------------------------------------------- storage (localStorage kept in one JSON file) */
static void kv_free(Natives *n) { for (int i = 0; i < n->kv_count; i++) { free(n->kv[i].key); free(n->kv[i].value); } free(n->kv); n->kv = NULL; n->kv_count = n->kv_cap = 0; }
static int kv_find(Natives *n, const char *k) { for (int i = 0; i < n->kv_count; i++) if (!strcmp(n->kv[i].key, k)) return i; return -1; }
static void kv_set(Natives *n, const char *k, const char *v) {
    int i = kv_find(n, k);
    if (i >= 0) { free(n->kv[i].value); n->kv[i].value = strdup(v); return; }
    if (n->kv_count == n->kv_cap) { n->kv_cap = n->kv_cap ? n->kv_cap * 2 : 16; n->kv = realloc(n->kv, n->kv_cap * sizeof(KV)); }
    n->kv[n->kv_count].key = strdup(k);
    n->kv[n->kv_count].value = strdup(v);
    n->kv_count++;
}

static char *json_string(const char **pp) {            /* parse a JSON string at *pp (just after the opening quote) */
    const char *p = *pp;
    size_t cap = 64, n = 0;
    char *out = malloc(cap);
    while (*p && *p != '"') {
        unsigned c = (unsigned char)*p++;
        if (c == '\\' && *p) {
            char e = *p++;
            if (e == 'n') c = '\n'; else if (e == 't') c = '\t'; else if (e == 'r') c = '\r'; else if (e == 'b') c = '\b'; else if (e == 'f') c = '\f';
            else if (e == 'u') {
                unsigned cp = 0;
                for (int i = 0; i < 4 && isxdigit((unsigned char)*p); i++, p++) cp = cp * 16 + (unsigned)(isdigit((unsigned char)*p) ? *p - '0' : tolower(*p) - 'a' + 10);
                if (cp >= 0xD800 && cp < 0xDC00 && p[0] == '\\' && p[1] == 'u') {            /* surrogate pair */
                    unsigned lo = 0; const char *q = p + 2;
                    for (int i = 0; i < 4 && isxdigit((unsigned char)*q); i++, q++) lo = lo * 16 + (unsigned)(isdigit((unsigned char)*q) ? *q - '0' : tolower(*q) - 'a' + 10);
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); p = q;
                }
                if (n + 6 >= cap) out = realloc(out, cap *= 2);
                if (cp < 0x80) out[n++] = (char)cp;
                else if (cp < 0x800) { out[n++] = (char)(0xC0 | (cp >> 6)); out[n++] = (char)(0x80 | (cp & 63)); }
                else if (cp < 0x10000) { out[n++] = (char)(0xE0 | (cp >> 12)); out[n++] = (char)(0x80 | ((cp >> 6) & 63)); out[n++] = (char)(0x80 | (cp & 63)); }
                else { out[n++] = (char)(0xF0 | (cp >> 18)); out[n++] = (char)(0x80 | ((cp >> 12) & 63)); out[n++] = (char)(0x80 | ((cp >> 6) & 63)); out[n++] = (char)(0x80 | (cp & 63)); }
                continue;
            } else c = (unsigned char)e;
        }
        if (n + 2 >= cap) out = realloc(out, cap *= 2);
        out[n++] = (char)c;
    }
    out[n] = 0;
    *pp = *p ? p + 1 : p;
    return out;
}

static void storage_load(Natives *n) {
    if (!n->save_path) return;
    char *text = (char *)sg_read_file(n->save_path, NULL);
    if (!text) return;
    const char *p = text;
    while (*p && *p != '{') p++;
    if (*p) p++;
    for (;;) {
        while (*p && *p != '"' && *p != '}') p++;
        if (*p != '"') break;
        p++;
        char *k = json_string(&p);
        while (*p && *p != ':') p++;
        if (*p) p++;
        while (*p && isspace((unsigned char)*p)) p++;
        if (*p != '"') { free(k); break; }
        p++;
        char *v = json_string(&p);
        kv_set(n, k, v);
        free(k); free(v);
    }
    free(text);
}

static void put_json_string(FILE *f, const char *s) {
    fputc('"', f);
    for (; *s; s++) {
        unsigned char c = (unsigned char)*s;
        if (c == '"' || c == '\\') { fputc('\\', f); fputc(c, f); }
        else if (c == '\n') fputs("\\n", f); else if (c == '\r') fputs("\\r", f); else if (c == '\t') fputs("\\t", f);
        else if (c < 0x20) fprintf(f, "\\u%04x", c);
        else fputc(c, f);
    }
    fputc('"', f);
}

static void storage_flush(Natives *n) {
    if (!n->save_path) return;
    size_t total = 2;
    for (int i = 0; i < n->kv_count; i++) total += strlen(n->kv[i].key) + strlen(n->kv[i].value) + 8;
    if (total > 4u * 1024 * 1024) { sg_log("warn", "saves too large, not written"); return; }
    char tmp[1100];
    snprintf(tmp, sizeof tmp, "%s.tmp", n->save_path);
    FILE *f = fopen(tmp, "wb");
    if (!f) { sg_log("warn", "cannot write %s", tmp); return; }
    fputc('{', f);
    for (int i = 0; i < n->kv_count; i++) { if (i) fputc(',', f); put_json_string(f, n->kv[i].key); fputc(':', f); put_json_string(f, n->kv[i].value); }
    fputc('}', f);
    fclose(f);
    rename(tmp, n->save_path);
}

NATIVE(n_storage) {
    Natives *n = N();
    const char *op = JS_ToCString(ctx, argv[0]);
    const char *key = JS_ToCString(ctx, argv[1]);
    JSValue result = JS_UNDEFINED;
    if (!strcmp(op, "get")) { int i = kv_find(n, key); result = i >= 0 ? JS_NewString(ctx, n->kv[i].value) : JS_NULL; }
    else if (!strcmp(op, "set")) { const char *v = JS_ToCString(ctx, argv[2]); kv_set(n, key, v ? v : ""); if (v) JS_FreeCString(ctx, v); n->storage_dirty = 1; }
    else if (!strcmp(op, "remove")) { int i = kv_find(n, key); if (i >= 0) { free(n->kv[i].key); free(n->kv[i].value); n->kv[i] = n->kv[--n->kv_count]; n->storage_dirty = 1; } }
    else if (!strcmp(op, "clear")) { kv_free(n); n->storage_dirty = 1; }
    else if (!strcmp(op, "key")) { int i = atoi(key); result = (i >= 0 && i < n->kv_count) ? JS_NewString(ctx, n->kv[i].key) : JS_NULL; }
    else if (!strcmp(op, "length")) result = JS_NewInt32(ctx, n->kv_count);
    JS_FreeCString(ctx, op);
    JS_FreeCString(ctx, key);
    return result;
}

/* ---------------------------------------------------------------- local files (fetch / XHR) */
NATIVE(n_read_text) {
    const char *url = JS_ToCString(ctx, argv[0]);
    char *path = sg_resolve(host_root(H()), url);
    JS_FreeCString(ctx, url);
    if (!path) return JS_NULL;
    size_t len;
    unsigned char *data = sg_read_file(path, &len);
    free(path);
    if (!data) return JS_NULL;
    JSValue s = JS_NewStringLen(ctx, (const char *)data, len);      /* UTF-8; a BOM is kept as the page text would be */
    free(data);
    return s;
}

NATIVE(n_stat) {
    const char *url = JS_ToCString(ctx, argv[0]);
    char *path = sg_resolve(host_root(H()), url);
    JS_FreeCString(ctx, url);
    if (!path) return JS_NewInt32(ctx, -1);
    struct stat st;
    int ok = stat(path, &st) == 0;
    free(path);
    return JS_NewInt64(ctx, ok ? (int64_t)st.st_size : -1);
}

/* ---------------------------------------------------------------- hooks provided by the other modules */
void canvas_register(Host *h, Natives *n, JSContext *ctx);
void canvas_page_loaded(Host *h, Natives *n);
void canvas_shutdown(Natives *n);
void images_register(Host *h, Natives *n, JSContext *ctx);
void images_shutdown(Natives *n);
void audio_register(Host *h, Natives *n, JSContext *ctx);
void audio_shutdown(Natives *n);
void audio_update(Natives *n);
void canvas_begin_frame(Natives *n);
void images_preload_step(Natives *n);

void natives_init(Host *h, Natives *n, const char *save_path) {
    n->host = h;
    n->main_id = -1;
    n->save_path = save_path ? strdup(save_path) : NULL;
    storage_load(n);
}

void natives_shutdown(Host *h, Natives *n) {
    (void)h;
    if (n->storage_dirty) storage_flush(n);
    audio_shutdown(n);
    canvas_shutdown(n);
    images_shutdown(n);
    kv_free(n);
    free(n->preload);
    free(n->save_path);
}

void natives_register(Host *h, JSContext *ctx) {
    Natives *n = host_natives(h);
    JSValue g = JS_GetGlobalObject(ctx);
#define REG(js, fn, argc) JS_SetPropertyStr(ctx, g, js, JS_NewCFunction(ctx, fn, js, argc))
    REG("__py_log", n_log, 2);
    REG("__py_storage", n_storage, 3);
    REG("__py_read_text", n_read_text, 1);
    REG("__py_stat", n_stat, 1);
#undef REG
    JS_FreeValue(ctx, g);
    canvas_register(h, n, ctx);
    images_register(h, n, ctx);
    audio_register(h, n, ctx);
}

void natives_page_loaded(Host *h, Natives *n) { canvas_page_loaded(h, n); }

void natives_begin_frame(Host *h, Natives *n) { (void)h; canvas_begin_frame(n); }

void natives_end_tick(Host *h, Natives *n) {
    (void)h;
    images_preload_step(n);
    audio_update(n);
    if (n->storage_dirty) { n->storage_dirty = 0; storage_flush(n); }
}
