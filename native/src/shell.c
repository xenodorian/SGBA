/* Natives used by the menu page (shell/shell.html): mode, raw pad presses, saving the controller mapping, the game list. */
#include <dirent.h>
#include <sys/stat.h>
#include "sgba.h"
#include "pkg.h"
#include "input.h"
#include "json.h"

App *g_app = NULL;

#define NATIVE(name) static JSValue name(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)

NATIVE(n_mode) { return JS_NewString(ctx, g_app && g_app->shell_mode ? g_app->shell_mode : "launcher"); }

NATIVE(n_raw_next) {
    const char *r = g_app ? input_raw_next(g_app->in) : NULL;
    return r ? JS_NewString(ctx, r) : JS_NULL;
}
NATIVE(n_raw_clear) { if (g_app) input_raw_clear(g_app->in); return JS_UNDEFINED; }

NATIVE(n_action) {
    const char *a = JS_ToCString(ctx, argv[0]);
    if (g_app && a) snprintf(g_app->choice, sizeof g_app->choice, "%s", a);
    if (a) JS_FreeCString(ctx, a);
    return JS_UNDEFINED;
}

NATIVE(n_save_controls) {
    if (!g_app) return JS_UNDEFINED;
    const char *text = JS_ToCString(ctx, argv[0]);
    char path[2300];
    snprintf(path, sizeof path, "%s/controls.json", g_app->app_dir);
    FILE *f = fopen(path, "wb");
    if (f && text) { fputs(text, f); fclose(f); sg_log("info", "controller setup saved"); }
    if (text) {
        JNode *root = json_parse(text);
        JNode *b = json_get(root, "bindings");
        if (b && b->type == J_OBJ) input_load_controls(g_app->in, path); else input_clear_bindings(g_app->in);
        json_free(root);
        JS_FreeCString(ctx, text);
    }
    return JS_UNDEFINED;
}

static void json_put(char **buf, size_t *len, size_t *cap, const char *s) {
    size_t n = strlen(s);
    if (*len + n + 1 > *cap) { *cap = (*cap + n + 1) * 2; *buf = realloc(*buf, *cap); }
    memcpy(*buf + *len, s, n); *len += n; (*buf)[*len] = 0;
}
static void json_quoted(char **buf, size_t *len, size_t *cap, const char *s) {
    json_put(buf, len, cap, "\"");
    for (; *s; s++) { char t[8]; if (*s == '"' || *s == '\\') { t[0] = '\\'; t[1] = *s; t[2] = 0; } else { t[0] = *s; t[1] = 0; } json_put(buf, len, cap, t); }
    json_put(buf, len, cap, "\"");
}

NATIVE(n_list_games) {
    char *out = NULL; size_t len = 0, cap = 0;
    json_put(&out, &len, &cap, "[");
    if (g_app) {
        char dir[2300];
        snprintf(dir, sizeof dir, "%s/games", g_app->app_dir);
        DIR *d = opendir(dir);
        int first = 1;
        struct dirent *e;
        while (d && (e = readdir(d))) {
            if (e->d_name[0] == '.') continue;
            char path[2600], index[2700];
            snprintf(path, sizeof path, "%s/%s", dir, e->d_name);
            snprintf(index, sizeof index, "%s/index.html", path);
            struct stat st;
            char ptitle[200] = "";
            int packaged = pkg_is_package(path) && stat(path, &st) == 0 && !S_ISDIR(st.st_mode);
            if (packaged) { if (pkg_title(path, ptitle, sizeof ptitle) != 0) snprintf(ptitle, sizeof ptitle, "%s", e->d_name); }
            else if (stat(index, &st) != 0) continue;
            Config cfg = {0};
            if (!packaged) config_load(path, &cfg);
            if (!first) json_put(&out, &len, &cap, ",");
            first = 0;
            json_put(&out, &len, &cap, "{\"path\":");
            json_quoted(&out, &len, &cap, path);
            json_put(&out, &len, &cap, ",\"title\":");
            json_quoted(&out, &len, &cap, packaged ? ptitle : (cfg.title ? cfg.title : e->d_name));
            json_put(&out, &len, &cap, "}");
            if (!packaged) config_free(&cfg);
        }
        if (d) closedir(d);
    }
    json_put(&out, &len, &cap, "]");
    JSValue v = JS_NewString(ctx, out);
    free(out);
    return v;
}

void shell_register(JSContext *ctx) {
    JSValue g = JS_GetGlobalObject(ctx);
#define REG(js, fn, argc) JS_SetPropertyStr(ctx, g, js, JS_NewCFunction(ctx, fn, js, argc))
    REG("__sgba_mode", n_mode, 0); REG("__sgba_raw_next", n_raw_next, 0); REG("__sgba_raw_clear", n_raw_clear, 0);
    REG("__sgba_action", n_action, 1); REG("__sgba_save_controls", n_save_controls, 1); REG("__sgba_list_games", n_list_games, 0);
#undef REG
    JS_FreeValue(ctx, g);
}
