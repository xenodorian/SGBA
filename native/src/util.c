/* Logging and path helpers. */
#include <stdarg.h>
#include <time.h>
#include <sys/stat.h>
#include <ctype.h>
#include "sgba.h"

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
    fprintf(stderr, "[%s] %s\n", level, buf);
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
