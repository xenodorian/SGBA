/* The JavaScript host: one QuickJS context running the page, the shim and the game. */
#ifdef _WIN32
#include <string.h>
static const char *strcasestr(const char *h, const char *n) {          /* not in the Windows C library */
    size_t m = strlen(n);
    if (!m) return h;
    for (; *h; h++) if (!_strnicmp(h, n, m)) return h;
    return NULL;
}
#endif
#include <ctype.h>
#include <string.h>
#include "sgba.h"
#include "image.h"
#include "natives.h"

extern const unsigned char overlay_js[];
extern const unsigned long overlay_js_len;
extern const unsigned char shim_js[];
extern const unsigned long shim_js_len;

struct Host {
    JSRuntime *rt;
    JSContext *ctx;
    char *root;
    double clock_ms;
    int errors;
    JSValue fn_tick, fn_key, fn_pads, fn_load_html, fn_overlay_tick, fn_overlay_nav, fn_overlay_hotkey;
    char *pads_json;
    Config cfg;
    Natives nat;
    size_t gc_floor;               /* native canvas memory after the last collection */
    int gc_runs;
};

JSContext *host_ctx(Host *h) { return h->ctx; }
const char *host_root(Host *h) { return h->root; }
int host_error_count(Host *h) { return h->errors; }
double host_clock_ms(Host *h) { return h->clock_ms; }
void host_note_error(Host *h) { h->errors++; }
Natives *host_natives(Host *h) { return &h->nat; }
const Config *host_config(Host *h) { return &h->cfg; }

static void report_exception(Host *h, const char *what) {
    JSValue ex = JS_GetException(h->ctx);
    const char *msg = JS_ToCString(h->ctx, ex);
    JSValue stack = JS_GetPropertyStr(h->ctx, ex, "stack");
    const char *st = JS_IsUndefined(stack) ? NULL : JS_ToCString(h->ctx, stack);
    sg_log("error", "%s: %s%s%s", what, msg ? msg : "(exception)", st ? "\n" : "", st ? st : "");
    if (st) JS_FreeCString(h->ctx, st);
    if (msg) JS_FreeCString(h->ctx, msg);
    JS_FreeValue(h->ctx, stack);
    JS_FreeValue(h->ctx, ex);
    h->errors++;
}

static void pump(Host *h) {
    for (int i = 0; i < 10000; i++) {
        JSContext *c;
        int r = JS_ExecutePendingJob(h->rt, &c);
        if (r == 0) break;
        if (r < 0) report_exception(h, "promise job");
    }
}

void host_eval(Host *h, const char *code, const char *name) {
    JSValue v = JS_Eval(h->ctx, code, strlen(code), name, JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(v)) report_exception(h, name); else JS_FreeValue(h->ctx, v);
    pump(h);
}

int host_eval_int(Host *h, const char *code, int fallback) {
    JSValue v = JS_Eval(h->ctx, code, strlen(code), "eval", JS_EVAL_TYPE_GLOBAL);
    int out = fallback;
    if (JS_IsException(v)) report_exception(h, "eval"); else { int32_t i; if (JS_ToInt32(h->ctx, &i, v) == 0) out = i; JS_FreeValue(h->ctx, v); }
    return out;
}

void host_enable_overlay(Host *h) {
    JSValue v = JS_Eval(h->ctx, (const char *)overlay_js, overlay_js_len, "overlay.js", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(v)) report_exception(h, "overlay"); else JS_FreeValue(h->ctx, v);
    JSValue g = JS_GetGlobalObject(h->ctx);
    h->fn_overlay_tick = JS_GetPropertyStr(h->ctx, g, "__sgba_overlay_tick");
    h->fn_overlay_nav = JS_GetPropertyStr(h->ctx, g, "__sgba_overlay_nav");
    h->fn_overlay_hotkey = JS_GetPropertyStr(h->ctx, g, "__sgba_overlay_hotkey");
    JS_FreeValue(h->ctx, g);
    if (h->cfg.nhide) {                                           /* buttons to leave out on a device */
        size_t cap = 256; for (int i = 0; i < h->cfg.nhide; i++) cap += strlen(h->cfg.hide[i]) * 2 + 8;
        char *code = malloc(cap), *p = code;
        p += sprintf(p, "__sgba_set_hide([");
        for (int i = 0; i < h->cfg.nhide; i++) { p += sprintf(p, "%s\"", i ? "," : ""); for (const char *c = h->cfg.hide[i]; *c; c++) { if (*c == '"' || *c == '\\') *p++ = '\\'; *p++ = *c; } *p++ = '"'; }
        p += sprintf(p, "]);");
        host_eval(h, code, "hide");
        free(code);
    }
}

int host_overlay_tick_exists(Host *h) { return JS_IsFunction(h->ctx, h->fn_overlay_tick); }

void host_overlay_nav(Host *h, const char *what) {
    if (!JS_IsFunction(h->ctx, h->fn_overlay_nav)) return;
    JSValue a = JS_NewString(h->ctx, what);
    JSValue r = JS_Call(h->ctx, h->fn_overlay_nav, JS_UNDEFINED, 1, &a);
    if (JS_IsException(r)) report_exception(h, "overlay nav"); else JS_FreeValue(h->ctx, r);
    JS_FreeValue(h->ctx, a);
    pump(h);
}

void host_overlay_hotkey(Host *h, const char *id) {
    if (!JS_IsFunction(h->ctx, h->fn_overlay_hotkey)) return;
    JSValue a = JS_NewString(h->ctx, id);
    JSValue r = JS_Call(h->ctx, h->fn_overlay_hotkey, JS_UNDEFINED, 1, &a);
    if (JS_IsException(r)) report_exception(h, "overlay hotkey"); else JS_FreeValue(h->ctx, r);
    JS_FreeValue(h->ctx, a);
    pump(h);
}

Host *host_create(const char *root, const char *save_path) {
    Host *h = calloc(1, sizeof *h);
    h->root = strdup(root);
    config_load(root, &h->cfg);
    h->rt = JS_NewRuntime();
    JS_SetMemoryLimit(h->rt, 96u * 1024 * 1024);
    { const char *gcmb = getenv("SGBA_GC_MB"); JS_SetGCThreshold(h->rt, (size_t)(gcmb ? atoi(gcmb) : 24) * 1024 * 1024); }        /* QuickJS frees most objects by reference count; the cycle collector rarely needs to interrupt a frame */
    JS_SetMaxStackSize(h->rt, 2u * 1024 * 1024);
    h->ctx = JS_NewContext(h->rt);
    JS_SetContextOpaque(h->ctx, h);
    natives_init(h, &h->nat, save_path);
    natives_register(h, h->ctx);
    JSValue v = JS_Eval(h->ctx, (const char *)shim_js, shim_js_len, "sgba_js_shim.js", JS_EVAL_TYPE_GLOBAL);
    if (JS_IsException(v)) report_exception(h, "shim"); else JS_FreeValue(h->ctx, v);
    JSValue g = JS_GetGlobalObject(h->ctx);
    h->fn_tick = JS_GetPropertyStr(h->ctx, g, "__sgba_tick");
    h->fn_key = JS_GetPropertyStr(h->ctx, g, "__sgba_key");
    h->fn_pads = JS_GetPropertyStr(h->ctx, g, "__sgba_set_pads");
    h->fn_load_html = JS_GetPropertyStr(h->ctx, g, "__sgba_load_html");
    JS_FreeValue(h->ctx, g);
    return h;
}

void host_destroy(Host *h) {
    if (!h) return;
    natives_shutdown(h, &h->nat);
    JS_FreeValue(h->ctx, h->fn_tick);
    JS_FreeValue(h->ctx, h->fn_key);
    JS_FreeValue(h->ctx, h->fn_pads);
    JS_FreeValue(h->ctx, h->fn_load_html);
    JS_FreeValue(h->ctx, h->fn_overlay_tick);
    JS_FreeValue(h->ctx, h->fn_overlay_nav);
    JS_FreeValue(h->ctx, h->fn_overlay_hotkey);
    JS_FreeContext(h->ctx);
    JS_FreeRuntime(h->rt);
    free(h->pads_json);
    config_free(&h->cfg);
    free(h->root);
    free(h);
}

/* ---- the page: pull the <script> tags out of index.html, in order ---- */

typedef struct { char *src; char *inline_code; int module; } Script;

static char *attr_value(const char *tag, size_t len, const char *name) {
    size_t nl = strlen(name);
    for (size_t i = 0; i + nl < len; i++) {
        if ((i == 0 || isspace((unsigned char)tag[i - 1])) && !strncasecmp(tag + i, name, nl)) {
            size_t k = i + nl;
            while (k < len && isspace((unsigned char)tag[k])) k++;
            if (k >= len || tag[k] != '=') continue;
            k++;
            while (k < len && isspace((unsigned char)tag[k])) k++;
            char q = (k < len && (tag[k] == '"' || tag[k] == '\'')) ? tag[k++] : 0;
            size_t s = k;
            while (k < len && (q ? tag[k] != q : !isspace((unsigned char)tag[k]) && tag[k] != '>')) k++;
            char *v = malloc(k - s + 1);
            memcpy(v, tag + s, k - s);
            v[k - s] = 0;
            return v;
        }
    }
    return NULL;
}

static int scan_scripts(const char *html, Script **out) {
    int n = 0, cap = 16;
    Script *list = calloc(cap, sizeof *list);
    const char *p = html;
    while ((p = strcasestr(p, "<script")) != NULL) {
        const char *tag = p + 7;
        if (!isspace((unsigned char)*tag) && *tag != '>') { p = tag; continue; }
        const char *gt = strchr(tag, '>');
        if (!gt) break;
        size_t tl = (size_t)(gt - tag);
        const char *body = gt + 1;
        const char *end = strcasestr(body, "</script");
        if (!end) end = body + strlen(body);
        if (n == cap) { cap *= 2; list = realloc(list, cap * sizeof *list); }
        Script *s = &list[n++];
        memset(s, 0, sizeof *s);
        s->src = attr_value(tag, tl, "src");
        char *type = attr_value(tag, tl, "type");
        s->module = type && !strcasecmp(type, "module");
        free(type);
        if (!s->src) { s->inline_code = malloc((size_t)(end - body) + 1); memcpy(s->inline_code, body, (size_t)(end - body)); s->inline_code[end - body] = 0; }
        p = end;
        if (*p) { const char *e2 = strchr(p, '>'); p = e2 ? e2 + 1 : p + 1; }
    }
    *out = list;
    return n;
}

int host_load_html_text(Host *h, const char *html) {
    size_t len = strlen(html);
    JSValue arg = JS_NewStringLen(h->ctx, html, len);
    JSValue r = JS_Call(h->ctx, h->fn_load_html, JS_UNDEFINED, 1, &arg);
    if (JS_IsException(r)) report_exception(h, "load html"); else JS_FreeValue(h->ctx, r);
    JS_FreeValue(h->ctx, arg);
    pump(h);
    natives_page_loaded(h, &h->nat);          /* finds the main canvas */
    Script *scripts;
    int n = scan_scripts(html, &scripts);
    for (int i = 0; i < n; i++) {
        Script *s = &scripts[i];
        if (s->module) { sg_log("warn", "module scripts are not supported yet"); }
        else if (s->src) {
            if (!strncmp(s->src, "http://", 7) || !strncmp(s->src, "https://", 8)) sg_log("warn", "skipped remote script %s", s->src);
            else {
                char *sp = sg_resolve(h->root, s->src);
                if (!sp) sg_log("warn", "missing script %s", s->src);
                else {
                    size_t sl;
                    char *code = (char *)sg_read_file(sp, &sl);
                    free(sp);
                    if (code) { JSValue v = JS_Eval(h->ctx, code, sl, s->src, JS_EVAL_TYPE_GLOBAL); if (JS_IsException(v)) report_exception(h, s->src); else JS_FreeValue(h->ctx, v); pump(h); free(code); }
                }
            }
        } else if (s->inline_code && strspn(s->inline_code, " \t\r\n") < strlen(s->inline_code)) {
            JSValue v = JS_Eval(h->ctx, s->inline_code, strlen(s->inline_code), "inline script", JS_EVAL_TYPE_GLOBAL);
            if (JS_IsException(v)) report_exception(h, "inline script"); else JS_FreeValue(h->ctx, v);
            pump(h);
        }
        free(s->src);
        free(s->inline_code);
    }
    free(scripts);
    host_eval(h, "document.dispatchEvent(new Event('DOMContentLoaded')); window.dispatchEvent(new Event('DOMContentLoaded'));", "DOMContentLoaded");
    host_eval(h, "document.dispatchEvent(new Event('load')); window.dispatchEvent(new Event('load'));", "load");
    if (h->cfg.npreload) images_preload_patterns(&h->nat, h->root, h->cfg.preload, h->cfg.npreload);
    return 0;
}

int host_load_page(Host *h, const char *index) {
    char *path = sg_resolve(h->root, index);
    if (!path) { sg_log("error", "cannot find %s in %s", index, h->root); return -1; }
    size_t len;
    char *html = (char *)sg_read_file(path, &len);
    free(path);
    if (!html) return -1;
    int rc = host_load_html_text(h, html);
    free(html);
    return rc;
}

void host_tick(Host *h, double dt) {
    h->clock_ms += dt * 1000.0;
    natives_begin_frame(h, &h->nat);
    JSValue t = JS_NewFloat64(h->ctx, h->clock_ms);
    JSValue r = JS_Call(h->ctx, h->fn_tick, JS_UNDEFINED, 1, &t);
    if (JS_IsException(r)) report_exception(h, "frame"); else JS_FreeValue(h->ctx, r);
    JS_FreeValue(h->ctx, t);
    pump(h);
    if (JS_IsFunction(h->ctx, h->fn_overlay_tick)) {
        JSValue o = JS_Call(h->ctx, h->fn_overlay_tick, JS_UNDEFINED, 0, NULL);
        if (JS_IsException(o)) report_exception(h, "overlay"); else JS_FreeValue(h->ctx, o);
        pump(h);
    }
    natives_end_tick(h, &h->nat);
    /* Canvases are small objects on the JS side but megabytes of pixels on ours, and QuickJS cannot see them: a game that makes
     * canvases and drops them would fill memory before the collector ever ran. Collect when canvas memory has grown by a lot. */
    size_t canvas_bytes = surf_live_bytes();
    size_t img = image_bytes_total(&h->nat);
    canvas_bytes = canvas_bytes > img ? canvas_bytes - img : 0;
    if (canvas_bytes < h->gc_floor) h->gc_floor = canvas_bytes;
    if (canvas_bytes > h->gc_floor + 24u * 1024 * 1024) {
        JS_RunGC(h->rt);
        size_t after = surf_live_bytes();
        after = after > img ? after - img : 0;
        if (h->gc_runs < 3) sg_log("info", "native memory gc: canvases %zu MB -> %zu MB", canvas_bytes >> 20, after >> 20);
        h->gc_floor = after;
        h->gc_runs++;
    }
}

void host_key(Host *h, int is_down, const char *key, const char *code, int key_code) {
    JSValue args[4] = { JS_NewString(h->ctx, is_down ? "keydown" : "keyup"), JS_NewString(h->ctx, key), JS_NewString(h->ctx, code), JS_NewInt32(h->ctx, key_code) };
    JSValue r = JS_Call(h->ctx, h->fn_key, JS_UNDEFINED, 4, args);
    if (JS_IsException(r)) report_exception(h, "key"); else JS_FreeValue(h->ctx, r);
    for (int i = 0; i < 4; i++) JS_FreeValue(h->ctx, args[i]);
    pump(h);
}

void host_set_pads(Host *h, const char *json) {
    if (h->pads_json && !strcmp(h->pads_json, json)) return;
    free(h->pads_json);
    h->pads_json = strdup(json);
    JSValue g = JS_GetGlobalObject(h->ctx);
    JSValue json_obj = JS_GetPropertyStr(h->ctx, g, "JSON");
    JSValue parse = JS_GetPropertyStr(h->ctx, json_obj, "parse");
    JS_FreeValue(h->ctx, json_obj);
    JSValue js = JS_NewString(h->ctx, json);
    JSValue obj = JS_Call(h->ctx, parse, JS_UNDEFINED, 1, &js);
    if (!JS_IsException(obj)) { JSValue r = JS_Call(h->ctx, h->fn_pads, JS_UNDEFINED, 1, &obj); if (JS_IsException(r)) report_exception(h, "pads"); else JS_FreeValue(h->ctx, r); }
    else report_exception(h, "pads json");
    JS_FreeValue(h->ctx, obj);
    JS_FreeValue(h->ctx, js);
    JS_FreeValue(h->ctx, parse);
    JS_FreeValue(h->ctx, g);
}
