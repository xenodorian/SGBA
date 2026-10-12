#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include "json.h"

static void ws(const char **p) { while (**p && isspace((unsigned char)**p)) (*p)++; }

static char *pstr(const char **pp) {
    const char *p = *pp + 1;
    size_t cap = 32, n = 0;
    char *out = malloc(cap);
    while (*p && *p != '"') {
        unsigned c = (unsigned char)*p++;
        if (c == '\\' && *p) {
            char e = *p++;
            if (e == 'n') c = '\n'; else if (e == 't') c = '\t'; else if (e == 'r') c = '\r';
            else if (e == 'u') { unsigned cp = 0; for (int i = 0; i < 4 && isxdigit((unsigned char)*p); i++, p++) cp = cp * 16 + (unsigned)(isdigit((unsigned char)*p) ? *p - '0' : tolower(*p) - 'a' + 10); c = cp < 0x80 ? cp : '?'; }
            else c = (unsigned char)e;
        }
        if (n + 2 >= cap) out = realloc(out, cap *= 2);
        out[n++] = (char)c;
    }
    out[n] = 0;
    *pp = *p ? p + 1 : p;
    return out;
}

static JNode *pval(const char **p) {
    ws(p);
    JNode *n = calloc(1, sizeof *n);
    if (**p == '"') { n->type = J_STR; n->str = pstr(p); return n; }
    if (**p == '{' || **p == '[') {
        int obj = **p == '{';
        n->type = obj ? J_OBJ : J_ARR;
        (*p)++;
        int cap = 0;
        for (;;) {
            ws(p);
            if (!**p) break;
            if (**p == '}' || **p == ']') { (*p)++; break; }
            if (**p == ',') { (*p)++; continue; }
            char *key = NULL;
            if (obj) { if (**p != '"') break; key = pstr(p); ws(p); if (**p == ':') (*p)++; }
            JNode *v = pval(p);
            if (n->count == cap) { cap = cap ? cap * 2 : 8; n->items = realloc(n->items, cap * sizeof *n->items); if (obj) n->keys = realloc(n->keys, cap * sizeof *n->keys); }
            n->items[n->count] = v;
            if (obj) n->keys[n->count] = key;
            n->count++;
        }
        return n;
    }
    if (!strncmp(*p, "true", 4)) { n->type = J_BOOL; n->num = 1; *p += 4; return n; }
    if (!strncmp(*p, "false", 5)) { n->type = J_BOOL; *p += 5; return n; }
    if (!strncmp(*p, "null", 4)) { *p += 4; return n; }
    char *end;
    n->type = J_NUM;
    n->num = strtod(*p, &end);
    if (end == *p) { n->type = J_NULL; (*p)++; } else *p = end;
    return n;
}

JNode *json_parse(const char *text) { const char *p = text; return pval(&p); }

void json_free(JNode *n) {
    if (!n) return;
    for (int i = 0; i < n->count; i++) { json_free(n->items[i]); if (n->keys) free(n->keys[i]); }
    free(n->items); free(n->keys); free(n->str); free(n);
}
JNode *json_get(const JNode *o, const char *key) {
    if (!o || o->type != J_OBJ) return NULL;
    for (int i = 0; i < o->count; i++) if (!strcmp(o->keys[i], key)) return o->items[i];
    return NULL;
}
const char *json_str(const JNode *n, const char *fb) { return n && n->type == J_STR ? n->str : fb; }
double json_num(const JNode *n, double fb) { return n && n->type == J_NUM ? n->num : fb; }
int json_bool(const JNode *n, int fb) { return n && (n->type == J_BOOL || n->type == J_NUM) ? n->num != 0 : fb; }
