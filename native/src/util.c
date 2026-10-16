/* Logging and path helpers. */
#include <stdarg.h>
#include <time.h>
#include <sys/stat.h>
#include <ctype.h>
#include "sgba.h"
#include "json.h"
#include "report.h"
#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#endif

static FILE *g_log = NULL;

void sg_log_open(const char *path) { if (path) g_log = fopen(path, "a"); }

void sg_log(const char *level, const char *fmt, ...) {
    char buf[2048];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof buf, fmt, ap);
    va_end(ap);
    time_t t = time(NULL);
    struct tm tmv;
#ifdef _WIN32
    localtime_s(&tmv, &t);
#else
    localtime_r(&t, &tmv);
#endif
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", &tmv);
    report_log_line(level, buf);
    fprintf(stderr, "%s [%s] %s\n", ts, level, buf);          /* every line carries its date and time, also in sgba_launch.log */
    if (g_log) { fprintf(g_log, "%s %s %s\n", ts, level, buf); fflush(g_log); }
}

static int hexv(int c) { return isdigit(c) ? c - '0' : (tolower(c) - 'a' + 10); }

/* Percent-decode and normalise `url` (relative to the game folder); refuse anything that leaves it or is remote. */
char *sg_resolve(const char *root, const char *url) {
    if (!url || !*url) return NULL;
    if (!strncmp(url, "http://", 7) || !strncmp(url, "https://", 8) || !strncmp(url, "data:", 5)) return NULL;
    size_t n = strlen(url), j = 0;
    char *dec = malloc(n + 1);
    for (size_t i = 0; i < n; i++) {
        char c = url[i];
        if (c == '?' || c == '#') break;
        if (c == '%' && i + 2 < n && isxdigit((unsigned char)url[i + 1]) && isxdigit((unsigned char)url[i + 2])) {
            dec[j++] = (char)(hexv(url[i + 1]) * 16 + hexv(url[i + 2]));
            i += 2;
        } else dec[j++] = (c == '\\') ? '/' : c;
    }
    dec[j] = 0;
    /* normalise components */
    char *out = malloc(strlen(root) + j + 4);
    size_t rl = strlen(root);
    memcpy(out, root, rl);
    if (rl && out[rl - 1] != '/') out[rl++] = '/';
    size_t base = rl, depth = 0, o = rl;
    char *p = dec;
    while (*p) {
        while (*p == '/') p++;
        char *s = p;
        while (*p && *p != '/') p++;
        size_t len = (size_t)(p - s);
        if (!len || (len == 1 && s[0] == '.')) continue;
        if (len == 2 && s[0] == '.' && s[1] == '.') {
            if (!depth) { free(dec); free(out); return NULL; }
            depth--;
            while (o > base && out[o - 1] != '/') o--;
            if (o > base) o--;
            continue;
        }
        if (o > base) out[o++] = '/';
        memcpy(out + o, s, len);
        o += len;
        depth++;
    }
    out[o] = 0;
    free(dec);
    struct stat st;
    if (stat(out, &st) != 0 || !S_ISREG(st.st_mode)) { free(out); return NULL; }
    return out;
}

unsigned char *sg_read_file(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) { fclose(f); return NULL; }
    unsigned char *buf = malloc((size_t)n + 1);
    size_t got = fread(buf, 1, (size_t)n, f);
    fclose(f);
    buf[got] = 0;
    if (size) *size = got;
    return buf;
}

/* glob: * matches any run, ? any one character (case-insensitive, / is ordinary) */
int sg_glob(const char *p, const char *t) {
    while (*p) {
        if (*p == '*') { while (*p == '*') p++; if (!*p) return 1; for (; *t; t++) if (sg_glob(p, t)) return 1; return sg_glob(p, t); }
        if (!*t || (*p != '?' && tolower((unsigned char)*p) != tolower((unsigned char)*t))) return 0;
        p++; t++;
    }
    return !*t;
}

char *sg_exe_dir(const char *argv0) {
    char buf[2048];
#ifdef _WIN32
    DWORD n = GetModuleFileNameA(NULL, buf, sizeof buf);
    if (n == 0 || n >= sizeof buf) return strdup(".");
#else
    ssize_t n = readlink("/proc/self/exe", buf, sizeof buf - 1);
    if (n <= 0) { if (!argv0) return strdup("."); snprintf(buf, sizeof buf, "%s", argv0); n = (ssize_t)strlen(buf); }
    buf[n] = 0;
#endif
    char *slash = strrchr(buf, '/');
#ifdef _WIN32
    char *bs = strrchr(buf, '\\');
    if (bs && (!slash || bs > slash)) slash = bs;
#endif
    if (!slash) return strdup(".");
    *slash = 0;
    return strdup(buf);
}

void config_load(const char *root, Config *c) {
    memset(c, 0, sizeof *c);
    c->fps = 60; c->image_budget_mb = 96; c->engine_nav = 1;
    char path[2048];
    snprintf(path, sizeof path, "%s/sgba_ui.json", root);
    char *text = (char *)sg_read_file(path, NULL);
    if (!text) return;
    JNode *r = json_parse(text);
    free(text);
    double fps = json_num(json_get(r, "fps"), 60);
    c->fps = fps < 10 ? 10 : fps > 60 ? 60 : (int)fps;
    c->alpha_keep = !strcmp(json_str(json_get(r, "alpha"), "threshold"), "keep");
    c->image_budget_mb = (int)json_num(json_get(r, "image_budget_mb"), 96);
    JNode *pre = json_get(r, "preload");
    if (pre && pre->type == J_ARR) {
        c->preload = calloc((size_t)pre->count + 1, sizeof(char *));
        for (int i = 0; i < pre->count; i++) if (pre->items[i]->type == J_STR) c->preload[c->npreload++] = strdup(pre->items[i]->str);
    }
    c->engine_nav = strcmp(json_str(json_get(r, "navigation"), "engine"), "game") != 0 && strcmp(json_str(json_get(r, "navigation"), "engine"), "off") != 0;
    JNode *hide = json_get(r, "hide");
    if (hide && hide->type == J_ARR) { c->hide = calloc((size_t)hide->count + 1, sizeof(char *)); for (int i = 0; i < hide->count; i++) if (hide->items[i]->type == J_STR) c->hide[c->nhide++] = strdup(hide->items[i]->str); }
    JNode *hot = json_get(r, "hotkeys");
    if (hot && hot->type == J_OBJ) {
        c->hot_action = calloc((size_t)hot->count + 1, sizeof(char *)); c->hot_id = calloc((size_t)hot->count + 1, sizeof(char *));
        for (int i = 0; i < hot->count; i++) if (hot->items[i]->type == J_STR) { c->hot_action[c->nhot] = strdup(hot->keys[i]); c->hot_id[c->nhot] = strdup(hot->items[i]->str); c->nhot++; }
    }
    const char *title = json_str(json_get(r, "title"), NULL);
    if (title) c->title = strdup(title);
    json_free(r);
}

void config_free(Config *c) {
    for (int i = 0; i < c->npreload; i++) free(c->preload[i]);
    for (int i = 0; i < c->nhide; i++) free(c->hide[i]);
    for (int i = 0; i < c->nhot; i++) { free(c->hot_action[i]); free(c->hot_id[i]); }
    free(c->preload); free(c->hide); free(c->hot_action); free(c->hot_id); free(c->title);
}
