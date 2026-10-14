/* Canvas 2D for the page, built like a small console video chip.
 *
 * Every drawing call on the page's main canvas becomes a recorded op (what it draws, the rectangle it touches, whether it
 * covers everything under it). At the end of a tick the new op list is compared with the last one and only the screen
 * regions touched by ops that changed are redrawn and sent to the display. Other canvases (sprite caches, scratch
 * buffers) draw immediately. This mirrors sgba_canvas.py so the two runtimes produce the same pictures.
 */
#include <ctype.h>
#include <math.h>
#include "pool.h"
#include "canvas.h"
#include "image.h"
#include "json.h"
#include "text.h"

#define NATIVE(name) static JSValue name(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
#define HOST() ((Host *)JS_GetContextOpaque(ctx))
#define NAT() host_natives(HOST())

enum { ALIGN_START = 0, ALIGN_CENTER, ALIGN_RIGHT };
enum { BASE_ALPHA = 0, BASE_TOP, BASE_MIDDLE, BASE_BOTTOM };
enum { OP_FILL = 1, OP_GRAD, OP_CLEAR, OP_IMAGE, OP_POLY, OP_LINES, OP_TEXT, OP_SHEAR, OP_ROT };

static inline long rnd(double v) { return lrint(v); }          /* ties to even, like Python's round() */

/* ---------------------------------------------------------------- colours and gradients */

static int hex1(int c) { return isdigit(c) ? c - '0' : tolower(c) - 'a' + 10; }

static const struct { const char *name; uint32_t rgb; } NAMED[] = {
    {"black", 0x000000}, {"white", 0xffffff}, {"red", 0xff0000}, {"lime", 0x00ff00}, {"green", 0x008000}, {"blue", 0x0000ff},
    {"yellow", 0xffff00}, {"cyan", 0x00ffff}, {"aqua", 0x00ffff}, {"magenta", 0xff00ff}, {"fuchsia", 0xff00ff}, {"gray", 0x808080},
    {"grey", 0x808080}, {"silver", 0xc0c0c0}, {"maroon", 0x800000}, {"olive", 0x808000}, {"navy", 0x000080}, {"purple", 0x800080},
    {"teal", 0x008080}, {"orange", 0xffa500}, {"gold", 0xffd700}, {"pink", 0xffc0cb}, {"brown", 0xa52a2a}, {"crimson", 0xdc143c},
    {"darkgray", 0xa9a9a9}, {"darkgrey", 0xa9a9a9}, {"lightgray", 0xd3d3d3}, {"lightgrey", 0xd3d3d3}, {"transparent", 0x000000},
    {"orangered", 0xff4500}, {"tomato", 0xff6347}, {"coral", 0xff7f50}, {"salmon", 0xfa8072}, {"khaki", 0xf0e68c}, {"violet", 0xee82ee},
    {"indigo", 0x4b0082}, {"skyblue", 0x87ceeb}, {"royalblue", 0x4169e1}, {"steelblue", 0x4682b4}, {"forestgreen", 0x228b22},
    {"limegreen", 0x32cd32}, {"darkred", 0x8b0000}, {"darkgreen", 0x006400}, {"darkblue", 0x00008b}, {"beige", 0xf5f5dc}, {"ivory", 0xfffff0},
    {"tan", 0xd2b48c}, {"chocolate", 0xd2691e}, {"sienna", 0xa0522d}, {"firebrick", 0xb22222}, {"deeppink", 0xff1493}, {"hotpink", 0xff69b4},
    {NULL, 0}
};

static double numtok(const char **p, int *pct) {
    while (**p && (isspace((unsigned char)**p) || **p == ',' || **p == '/' || **p == '(')) (*p)++;
    char *end;
    double v = strtod(*p, &end);
    *p = end;
    *pct = 0;
    if (**p == '%') { *pct = 1; (*p)++; }
    while (**p && isalpha((unsigned char)**p)) (*p)++;          /* "deg" */
    return v;
}

static uint32_t hsl_to_rgb(double h, double s, double l, int a) {
    h = fmod(h, 360.0); if (h < 0) h += 360.0;
    s /= 100.0; l /= 100.0;
    double c = (1 - fabs(2 * l - 1)) * s, x = c * (1 - fabs(fmod(h / 60.0, 2) - 1)), m = l - c / 2, r = 0, g = 0, b = 0;
    int sector = (int)(h / 60.0);
    switch (sector) { case 0: r = c; g = x; break; case 1: r = x; g = c; break; case 2: g = c; b = x; break; case 3: g = x; b = c; break; case 4: r = x; b = c; break; default: r = c; b = x; }
    return RGBA((int)rnd((r + m) * 255), (int)rnd((g + m) * 255), (int)rnd((b + m) * 255), a);
}

/* CSS colour string -> ARGB (straight alpha). */
static uint32_t parse_color(const char *text) {
    while (isspace((unsigned char)*text)) text++;
    char s[80];
    size_t n = 0;
    for (; text[n] && n < sizeof s - 1; n++) s[n] = (char)tolower((unsigned char)text[n]);
    s[n] = 0;
    while (n && isspace((unsigned char)s[n - 1])) s[--n] = 0;
    if (s[0] == '#') {
        const char *h = s + 1;
        size_t len = strlen(h);
        int v[8];
        for (size_t i = 0; i < len && i < 8; i++) { if (!isxdigit((unsigned char)h[i])) return 0xFF000000u; v[i] = hex1(h[i]); }
        if (len == 3 || len == 4) return RGBA(v[0] * 17, v[1] * 17, v[2] * 17, len == 4 ? v[3] * 17 : 255);
        if (len == 6 || len == 8) return RGBA(v[0] * 16 + v[1], v[2] * 16 + v[3], v[4] * 16 + v[5], len == 8 ? v[6] * 16 + v[7] : 255);
        return 0xFF000000u;
    }
    if (!strncmp(s, "rgb", 3)) {
        const char *p = strchr(s, '(');
        if (!p) return 0xFF000000u;
        double c[4] = { 0, 0, 0, 1 };
        int pct, got = 0;
        for (int i = 0; i < 4; i++) {
            const char *q = p;
            while (*q && (isspace((unsigned char)*q) || *q == ',' || *q == '/' || *q == '(')) q++;
            if (*q == ')' || !*q) break;
            c[i] = numtok(&p, &pct);
            if (pct) c[i] = i < 3 ? c[i] * 2.55 : c[i] / 100.0;
            got++;
        }
        if (got < 3) return 0xFF000000u;
        int r = (int)rnd(c[0]), g = (int)rnd(c[1]), b = (int)rnd(c[2]), a = (int)rnd(c[3] * 255);
        r = r < 0 ? 0 : r > 255 ? 255 : r; g = g < 0 ? 0 : g > 255 ? 255 : g; b = b < 0 ? 0 : b > 255 ? 255 : b; a = a < 0 ? 0 : a > 255 ? 255 : a;
        return RGBA(r, g, b, a);
    }
    if (!strncmp(s, "hsl", 3)) {
        const char *p = strchr(s, '(');
        if (!p) return 0xFF000000u;
        int pct;
        double h = numtok(&p, &pct), sat = numtok(&p, &pct), l = numtok(&p, &pct), a = 1;
        const char *q = p;
        while (*q && (isspace((unsigned char)*q) || *q == ',' || *q == '/')) q++;
        if (*q && *q != ')') a = numtok(&p, &pct) / (pct ? 100.0 : 1.0);
        return hsl_to_rgb(h, sat, l, (int)rnd(a * 255));
    }
    if (!strcmp(s, "transparent")) return 0;
    for (int i = 0; NAMED[i].name; i++) if (!strcmp(s, NAMED[i].name)) return 0xFF000000u | NAMED[i].rgb;
    return 0xFF000000u;
}

typedef struct Gradient {
    char *json;
    int linear;
    double a[6];
    int n;
    double off[16];
    uint32_t col[16];
    struct Gradient *next;
} Gradient;

static Gradient *g_grads = NULL;              /* interned by their JSON text: equal pointer means equal gradient */

static Gradient *gradient_intern(const char *json) {
    for (Gradient *g = g_grads; g; g = g->next) if (!strcmp(g->json, json)) return g;
    JNode *root = json_parse(json);
    Gradient *g = calloc(1, sizeof *g);
    g->json = strdup(json);
    const char *kind = json_str(json_get(root, "g"), "linear");
    g->linear = strcmp(kind, "radial") != 0;
    JNode *args = json_get(root, "a");
    for (int i = 0; args && i < args->count && i < 6; i++) g->a[i] = json_num(args->items[i], 0);
    JNode *stops = json_get(root, "s");
    for (int i = 0; stops && i < stops->count && g->n < 16; i++) {
        JNode *st = stops->items[i];
        if (st->type != J_ARR || st->count < 2) continue;
        g->off[g->n] = json_num(st->items[0], 0);
        g->col[g->n] = parse_color(json_str(st->items[1], "#000"));
        g->n++;
    }
    for (int i = 1; i < g->n; i++) {                       /* stable sort by offset */
        double o = g->off[i]; uint32_t c = g->col[i]; int j = i - 1;
        while (j >= 0 && g->off[j] > o) { g->off[j + 1] = g->off[j]; g->col[j + 1] = g->col[j]; j--; }
        g->off[j + 1] = o; g->col[j + 1] = c;
    }
    json_free(root);
    g->next = g_grads;
    g_grads = g;
    return g;
}

static uint32_t grad_color(const Gradient *g, double t) {
    if (!g->n) return 0;
    if (t <= g->off[0]) return g->col[0];
    for (int i = 0; i + 1 < g->n; i++) {
        if (t <= g->off[i + 1]) {
            double k = g->off[i + 1] == g->off[i] ? 0 : (t - g->off[i]) / (g->off[i + 1] - g->off[i]);
            uint32_t c0 = g->col[i], c1 = g->col[i + 1];
            int a = (int)rnd(PA(c0) + (PA(c1) - PA(c0)) * k), r = (int)rnd(PR(c0) + (PR(c1) - PR(c0)) * k);
            int gg = (int)rnd(PG(c0) + (PG(c1) - PG(c0)) * k), b = (int)rnd(PB(c0) + (PB(c1) - PB(c0)) * k);
            return RGBA(r, gg, b, a);
        }
    }
    return g->col[g->n - 1];
}

/* Colour of the gradient at device pixel (px, py) of a rectangle whose top-left is (ox, oy) and size (w, h). */
static uint32_t grad_at(const Gradient *g, int ox, int oy, int w, int h, int px, int py) {
    if (g->linear) {
        double x0 = g->a[0], y0 = g->a[1], x1 = g->a[2], y1 = g->a[3], dx = x1 - x0, dy = y1 - y0, len2 = dx * dx + dy * dy;
        if (len2 == 0) len2 = 1;
        double t;
        if (fabs(dy) >= fabs(dx)) t = ((w / 2.0 + ox - x0) * dx + (py + 0.5 - y0) * dy) / len2;
        else t = ((px + 0.5 - x0) * dx + (h / 2.0 + oy - y0) * dy) / len2;
        return grad_color(g, t < 0 ? 0 : t > 1 ? 1 : t);
    }
    double x1 = g->a[3], y1 = g->a[4], r0 = g->a[2], r1 = g->a[5];
    double dist = sqrt((px + 0.5 - x1) * (px + 0.5 - x1) + (py + 0.5 - y1) * (py + 0.5 - y1));
    double t = r1 == r0 ? 0 : (dist - r0) / (r1 - r0);
    return grad_color(g, t < 0 ? 0 : t > 1 ? 1 : t);
}

/* ---------------------------------------------------------------- state */

typedef struct {
    double m[6];
    double alpha;
    uint32_t fill, stroke;
    Gradient *fillg, *strokeg;
    double lw;
    double font_size;
    int bold, italic;
    int align, baseline, comp;
    int has_clip;
    IRect clip;
} State;

static void state_reset(State *s) {
    memset(s, 0, sizeof *s);
    s->m[0] = 1; s->m[3] = 1;
    s->alpha = 1;
    s->fill = s->stroke = 0xFF000000u;
    s->lw = 1;
    s->font_size = 10;
    s->comp = COMP_OVER;
}

/* ---------------------------------------------------------------- ops */

typedef struct Op {
    int ref;
    uint8_t kind, covers, comp, hasclip;
    IRect box, clip;
    int i[16];
    uint32_t color;
    Gradient *grad;
    char *text;
    float *f; int nf;
    int *iv; int niv;
} Op;

static Op *op_new(int kind) { Op *o = calloc(1, sizeof *o); o->ref = 1; o->kind = (uint8_t)kind; return o; }
static void op_ref(Op *o) { o->ref++; }
static void op_unref(Op *o) { if (--o->ref > 0) return; free(o->text); free(o->f); free(o->iv); free(o); }

static int op_same(const Op *a, const Op *b) {
    if (a == b) return 1;
    if (a->kind != b->kind || a->comp != b->comp || a->color != b->color || a->grad != b->grad || a->hasclip != b->hasclip) return 0;
    if (a->hasclip && memcmp(&a->clip, &b->clip, sizeof(IRect))) return 0;
    if (memcmp(a->i, b->i, sizeof a->i)) return 0;
    if (a->nf != b->nf || a->niv != b->niv) return 0;
    if (a->nf && memcmp(a->f, b->f, (size_t)a->nf * sizeof(float))) return 0;
    if (a->niv && memcmp(a->iv, b->iv, (size_t)a->niv * sizeof(int))) return 0;
    if ((a->text == NULL) != (b->text == NULL)) return 0;
    if (a->text && strcmp(a->text, b->text)) return 0;
    return 1;
}

typedef struct { Op **v; int n, cap; } OpList;
static void ol_push(OpList *l, Op *o) { if (l->n == l->cap) { l->cap = l->cap ? l->cap * 2 : 128; l->v = realloc(l->v, l->cap * sizeof(Op *)); } l->v[l->n++] = o; }
static void ol_clear(OpList *l) { for (int i = 0; i < l->n; i++) op_unref(l->v[i]); l->n = 0; }
static void ol_free(OpList *l) { ol_clear(l); free(l->v); l->v = NULL; l->cap = 0; }

typedef struct { uint64_t hash; char *sig; OpList ops; int *dep_src; int *dep_ver; int ndeps; uint64_t used; } Group;

/* ---------------------------------------------------------------- canvas */

typedef struct { double x, y; } Pt;

struct Canvas {
    int id;
    Surf *surf;
    int retained, opaque, persistent, force_full, touched;
    int opq_ver, opq_val;                  /* is the surface fully opaque? scanned once per version, so drawing it can copy rows */
    int version;
    State s;
    State *stack; int sp, scap;
    Pt *path; int npath, pcap;
    int *sub; int nsub, subcap;                 /* start index of each subpath in path[] */
    OpList pending, prev;
    int have_prev;
    OpList *stale; int nstale, stalecap;
    Group *groups; int ngroups, groupcap; uint64_t group_tick;
    int grp_open; uint64_t grp_hash; char *grp_sig; int grp_start;
};

static long st_ops = 0, st_replayed = 0, st_skipped = 0, st_frames = 0, st_area = 0;

static Canvas *cv_get(Natives *n, int id) { return (id >= 0 && id < n->canvas_cap) ? n->canvases[id] : NULL; }

static void cv_reset_state(Canvas *c) { state_reset(&c->s); c->sp = 0; c->npath = 0; c->nsub = 0; }

static Canvas *cv_create(Natives *n, int id, int w, int h) {
    if (w < 1) w = 1; if (h < 1) h = 1; if (w > 4096) w = 4096; if (h > 4096) h = 4096;
    if (id >= n->canvas_cap) {
        int cap = n->canvas_cap ? n->canvas_cap : 32;
        while (cap <= id) cap *= 2;
        n->canvases = realloc(n->canvases, cap * sizeof(Canvas *));
        for (int i = n->canvas_cap; i < cap; i++) n->canvases[i] = NULL;
        n->canvas_cap = cap;
    }
    Canvas *c = calloc(1, sizeof *c);
    c->id = id;
    c->surf = surf_create(w, h);
    state_reset(&c->s);
    n->canvases[id] = c;
    return c;
}

static void group_free(Group *g) { free(g->sig); ol_free(&g->ops); free(g->dep_src); free(g->dep_ver); }

static void cv_free(Canvas *c) {
    if (!c) return;
    surf_free(c->surf);
    free(c->stack); free(c->path); free(c->sub);
    ol_free(&c->pending); ol_free(&c->prev);
    for (int i = 0; i < c->nstale; i++) ol_free(&c->stale[i]);
    free(c->stale);
    for (int i = 0; i < c->ngroups; i++) group_free(&c->groups[i]);
    free(c->groups);
    free(c->grp_sig);
    free(c);
}

static IRect cv_bounds(const Canvas *c) { return irect(0, 0, c->surf->w, c->surf->h); }

/* ---------------------------------------------------------------- running ops */

/* Worker threads of a parallel repaint only read caches that were filled beforehand. A miss there is flagged and the region is
 * repainted again on one thread. */
static __thread int t_peek;
static volatile int g_mt_miss;

static int canvas_opaque(Canvas *sc) {
    if (sc->opq_ver != sc->version + 1) {
        uint32_t acc = 0xFF000000u;
        size_t cnt = (size_t)sc->surf->w * (size_t)sc->surf->h;
        for (size_t i = 0; i < cnt; i++) acc &= sc->surf->px[i];
        sc->opq_val = (acc >> 24) == 255;
        sc->opq_ver = sc->version + 1;
    }
    return sc->opq_val;
}

static const Surf *source_surf(Natives *n, int id, int *kind) {
    if (id >= IMAGE_ID_BASE) {
        if (t_peek) { const Surf *sp = image_surface_peek(n, id, kind); if (!sp) g_mt_miss = 1; return sp; }
        return image_surface(n, id, kind);
    }
    Canvas *s = cv_get(n, id);
    if (!s) return NULL;
    *kind = (t_peek ? s->opq_ver == s->version + 1 && s->opq_val : canvas_opaque(s)) ? KIND_OPAQUE : KIND_ALPHA;
    return s->surf;
}

static void run_geom(Natives *n, Canvas *c, const Op *o, IRect clip);

static void run_op(Natives *n, Canvas *c, const Op *o, const IRect *region) {
    IRect clip = o->hasclip ? o->clip : cv_bounds(c);
    if (region) clip = irect_clip(clip, *region);
    clip = irect_clip(clip, cv_bounds(c));
    if (irect_empty(clip)) return;
    Surf *d = c->surf;
    switch (o->kind) {
    case OP_FILL: {
        IRect r = irect_clip(irect(o->i[0], o->i[1], o->i[2], o->i[3]), clip);
        if (irect_empty(r)) return;
        if (o->comp == COMP_OVER && PA(o->color) >= 255) r_fill_solid(d, r, o->color);
        else r_fill_blend(d, r, o->color, o->comp);
        break;
    }
    case OP_CLEAR: {
        IRect r = irect_clip(irect(o->i[0], o->i[1], o->i[2], o->i[3]), clip);
        if (!irect_empty(r)) r_fill_solid(d, r, c->opaque ? 0xFF000000u : 0);
        break;
    }
    case OP_GRAD: {
        IRect rect = irect(o->i[0], o->i[1], o->i[2], o->i[3]);
        IRect r = irect_clip(rect, clip);
        if (irect_empty(r)) break;
        int lvl = o->i[4];
        const Gradient *g = o->grad;
        int vertical = g->linear && fabs(g->a[3] - g->a[1]) >= fabs(g->a[2] - g->a[0]);     /* colour depends only on the row */
        int horizontal = g->linear && !vertical;                                             /* colour depends only on the column */
        uint32_t *buf = vertical ? NULL : malloc(sizeof(uint32_t) * (size_t)r.w);
        if (horizontal)
            for (int x = 0; x < r.w; x++) { uint32_t gc = grad_at(g, rect.x, rect.y, rect.w, rect.h, r.x + x, r.y); buf[x] = (gc & 0x00FFFFFFu) | ((uint32_t)(PA(gc) * lvl / 255) << 24); }
        for (int y = r.y; y < r.y + r.h; y++) {
            uint32_t *row = d->px + (size_t)y * d->w + r.x;
            if (vertical) {
                uint32_t gc = grad_at(g, rect.x, rect.y, rect.w, rect.h, rect.x, y);
                int a = PA(gc) * lvl / 255;
                if (a) r_fill_blend(d, irect(r.x, y, r.w, 1), (gc & 0x00FFFFFFu) | ((uint32_t)a << 24), o->comp);
                continue;
            }
            if (!horizontal)
                for (int x = 0; x < r.w; x++) { uint32_t gc = grad_at(g, rect.x, rect.y, rect.w, rect.h, r.x + x, y); buf[x] = (gc & 0x00FFFFFFu) | ((uint32_t)(PA(gc) * lvl / 255) << 24); }
            if (o->comp == COMP_SRC_IN) { for (int x = 0; x < r.w; x++) if (PA(buf[x])) row[x] = r_comp_px(buf[x], row[x], o->comp); }
            else r_span_comp(row, buf, r.w, o->comp);
        }
        free(buf);
        break;
    }
    case OP_IMAGE: {
        int kind = 0;
        const Surf *src = source_surf(n, o->i[0], &kind);
        if (!src) return;
        r_blit(d, src, o->i[2], o->i[3], o->i[4], o->i[5], o->i[6], o->i[7], o->i[8], o->i[9], o->i[10], o->i[11], o->i[12], o->comp, kind, clip);
        break;
    }
    case OP_POLY:
        r_poly(d, o->f, o->nf / 2, o->color, o->comp, clip);
        break;
    case OP_LINES:
        r_lines(d, o->iv, o->niv / 2, o->i[0], o->i[1], o->color, o->comp, clip);
        break;
    case OP_TEXT: {
        const Mask *m = t_peek ? text_mask_peek(o->text, o->i[0], o->i[1], o->i[2]) : text_mask(o->text, o->i[0], o->i[1], o->i[2]);
        if (!m) { if (t_peek) g_mt_miss = 1; return; }
        if (o->grad) {
            IRect dest = irect_clip(irect(o->i[3], o->i[4], m->w, m->h), clip);
            for (int y = dest.y; y < dest.y + dest.h; y++) {
                for (int x = dest.x; x < dest.x + dest.w; x++) {
                    int mv = m->a[(y - o->i[4]) * m->w + (x - o->i[3])];
                    if (!mv) continue;
                    uint32_t gc = grad_at(o->grad, o->i[3], o->i[4], m->w, m->h, x, y);
                    int a = mv * PA(gc) / 255 * PA(o->color) / 255;
                    if (!a) continue;
                    d->px[(size_t)y * d->w + x] = r_comp_px((gc & 0x00FFFFFFu) | ((uint32_t)a << 24), d->px[(size_t)y * d->w + x], o->comp);
                }
            }
        } else r_mask(d, m->a, m->w, m->h, o->i[3], o->i[4], o->color, o->comp, clip);
        break;
    }
    case OP_SHEAR: case OP_ROT:
        run_geom(n, c, o, clip);
        break;
    }
}

/* rotated and sheared pictures (cast shadows, spinning sprites) */
static void run_geom(Natives *n, Canvas *c, const Op *o, IRect clip) {
    int kind = 0;
    const Surf *src = source_surf(n, o->i[0], &kind);
    if (!src) return;
    Surf *d = c->surf;
    if (o->kind == OP_SHEAR) {
        int sx = o->i[2], sy = o->i[3], sw = o->i[4], sh = o->i[5], w = o->i[6], h = o->i[7], left = o->i[8], top = o->i[9];
        int fx = o->i[10], fy = o->i[11], alpha = o->i[12];
        for (int r = 0; r < h; r++) {
            int srow = (int)(((int64_t)(fy ? h - 1 - r : r) * sh) / h);
            if (srow >= sh) srow = sh - 1;
            r_blit(d, src, sx, sy + srow, sw, 1, left + (int)rnd(o->f[r]), top + r, w, 1, fx, 0, alpha, o->comp, kind, clip);
        }
        return;
    }
    /* OP_ROT: i = src,version,sx,sy,sw,sh, tw,th, mirrored, qangle2, cx, cy, alpha */
    int sx = o->i[2], sy = o->i[3], sw = o->i[4], sh = o->i[5], tw = o->i[6], th = o->i[7], mirrored = o->i[8];
    double theta = (o->i[9] / 2.0) * M_PI / 180.0, cs = cos(theta), sn = sin(theta);
    int cx = o->i[10], cy = o->i[11], alpha = o->i[12];
    int ow = (int)ceil(fabs(tw * cs) + fabs(th * sn)), oh = (int)ceil(fabs(tw * sn) + fabs(th * cs));
    int left = cx - ow / 2, top = cy - oh / 2;
    IRect dest = irect_clip(irect(left, top, ow, oh), clip);
    for (int y = dest.y; y < dest.y + dest.h; y++) {
        for (int x = dest.x; x < dest.x + dest.w; x++) {
            double px = (x - left) + 0.5 - ow / 2.0, py = (y - top) + 0.5 - oh / 2.0;
            double u = px * cs + py * sn + tw / 2.0, v = -px * sn + py * cs + th / 2.0;
            int iu = (int)floor(u), iv = (int)floor(v);
            if (iu < 0 || iv < 0 || iu >= tw || iv >= th) continue;
            int srow = (int)(((int64_t)(mirrored ? th - 1 - iv : iv) * sh) / th), scol = (int)(((int64_t)iu * sw) / tw);
            uint32_t s = src->px[(size_t)(sy + srow) * src->w + sx + scol];
            int a = PA(s);
            if (kind == KIND_KEY) a = a ? 255 : 0;
            if (kind == KIND_OPAQUE) a = 255;
            a = a * alpha / 255;
            if (!a) continue;
            d->px[(size_t)y * d->w + x] = r_comp_px((s & 0x00FFFFFFu) | ((uint32_t)a << 24), d->px[(size_t)y * d->w + x], o->comp);
        }
    }
}

/* ---------------------------------------------------------------- recording */

static void cv_go_persistent(Natives *n, Canvas *c);

static void emit(Natives *n, Canvas *c, Op *o, IRect box, int covers) {
    State *s = &c->s;
    if (s->has_clip) { box = irect_clip(box, s->clip); o->hasclip = 1; o->clip = s->clip; }
    box = irect_clip(box, cv_bounds(c));
    if (irect_empty(box)) { op_unref(o); return; }
    o->box = box;
    o->covers = (uint8_t)covers;
    if (c->retained && !c->persistent) { ol_push(&c->pending, o); st_ops++; }
    else { run_op(n, c, o, NULL); c->version++; c->touched = 1; op_unref(o); }
}

static void cv_go_persistent(Natives *n, Canvas *c) {
    if (c->persistent) return;
    for (int i = 0; i < c->nstale; i++) { for (int k = 0; k < c->stale[i].n; k++) run_op(n, c, c->stale[i].v[k], NULL); ol_clear(&c->stale[i]); }
    c->nstale = 0;
    for (int k = 0; k < c->pending.n; k++) run_op(n, c, c->pending.v[k], NULL);
    ol_clear(&c->pending);
    ol_clear(&c->prev);
    c->have_prev = 0;
    c->persistent = 1;
    c->version++;
    c->touched = 1;
}

static inline void tf(const Canvas *c, double x, double y, double *ox, double *oy) {
    const double *m = c->s.m;
    *ox = m[0] * x + m[2] * y + m[4];
    *oy = m[1] * x + m[3] * y + m[5];
}

static int axis_aligned(const Canvas *c) { return c->s.m[1] == 0 && c->s.m[2] == 0; }

static IRect device_rect(const Canvas *c, double x, double y, double w, double h) {
    double x0, y0, x1, y1;
    tf(c, x, y, &x0, &y0);
    tf(c, x + w, y + h, &x1, &y1);
    long l = rnd(x0), r = rnd(x1), t = rnd(y0), b = rnd(y1);
    if (r < l) { long q = l; l = r; r = q; }
    if (b < t) { long q = t; t = b; b = q; }
    return irect((int)l, (int)t, (int)(r - l), (int)(b - t));
}

static int eff_alpha(uint32_t color, double alpha) { return (int)(PA(color) * alpha); }   /* int() truncation, as the Python runtime */

static void fill_polygon(Natives *n, Canvas *c, const Pt *pts, int cnt, uint32_t style, Gradient *grad) {
    if (cnt < 3 || grad) return;
    int a = eff_alpha(style, c->s.alpha);
    if (a <= 0) return;
    float *f = malloc(sizeof(float) * 2 * (size_t)cnt);
    double minx = 1e18, miny = 1e18, maxx = -1e18, maxy = -1e18;
    for (int i = 0; i < cnt; i++) {
        double qx = rnd(pts[i].x * 4) / 4.0, qy = rnd(pts[i].y * 4) / 4.0;
        f[i * 2] = (float)qx; f[i * 2 + 1] = (float)qy;
        if (qx < minx) minx = qx; if (qx > maxx) maxx = qx; if (qy < miny) miny = qy; if (qy > maxy) maxy = qy;
    }
    int left = (int)floor(minx), top = (int)floor(miny), w = (int)ceil(maxx) - left + 1, h = (int)ceil(maxy) - top + 1;
    if (w <= 0 || h <= 0 || w > 4096 || h > 4096) { free(f); return; }
    Op *o = op_new(OP_POLY);
    o->f = f; o->nf = cnt * 2;
    o->color = (style & 0x00FFFFFFu) | ((uint32_t)a << 24);
    o->comp = (uint8_t)c->s.comp;
    emit(n, c, o, irect(left, top, w, h), 0);
}

static void stroke_points(Natives *n, Canvas *c, const Pt *pts, int cnt, int closed, uint32_t style, Gradient *grad) {
    if (cnt < 2 || grad) return;
    const double *m = c->s.m;
    double prod = c->s.lw * sqrt(fabs(m[0] * m[3] - m[1] * m[2]));
    long width = rnd(prod != 0 ? prod : 1);
    if (width < 1) width = 1;
    int a = eff_alpha(style, c->s.alpha);
    if (a <= 0) return;
    int *iv = malloc(sizeof(int) * 2 * (size_t)cnt);
    int minx = 1 << 30, miny = 1 << 30, maxx = -(1 << 30), maxy = -(1 << 30);
    for (int i = 0; i < cnt; i++) {
        int x = (int)rnd(pts[i].x), y = (int)rnd(pts[i].y);
        iv[i * 2] = x; iv[i * 2 + 1] = y;
        if (x < minx) minx = x; if (x > maxx) maxx = x; if (y < miny) miny = y; if (y > maxy) maxy = y;
    }
    int left = minx - (int)width, top = miny - (int)width, w = maxx - left + (int)width + 1, h = maxy - top + (int)width + 1;
    if (w <= 0 || h <= 0 || w > 4096 || h > 4096) { free(iv); return; }
    Op *o = op_new(OP_LINES);
    o->iv = iv; o->niv = cnt * 2;
    o->i[0] = closed; o->i[1] = (int)width;
    o->color = (style & 0x00FFFFFFu) | ((uint32_t)a << 24);
    o->comp = (uint8_t)c->s.comp;
    emit(n, c, o, irect(left, top, w, h), 0);
}

static void path_add_sub(Canvas *c) {
    if (c->nsub == c->subcap) { c->subcap = c->subcap ? c->subcap * 2 : 8; c->sub = realloc(c->sub, c->subcap * sizeof(int)); }
    c->sub[c->nsub++] = c->npath;
}
static void path_add_pt(Canvas *c, Pt p) {
    if (c->npath == c->pcap) { c->pcap = c->pcap ? c->pcap * 2 : 64; c->path = realloc(c->path, c->pcap * sizeof(Pt)); }
    c->path[c->npath++] = p;
}
static int sub_count(const Canvas *c, int k) { return (k + 1 < c->nsub ? c->sub[k + 1] : c->npath) - c->sub[k]; }

static void m_moveTo(Canvas *c, double x, double y) { Pt p; tf(c, x, y, &p.x, &p.y); path_add_sub(c); path_add_pt(c, p); }
static void m_lineTo(Canvas *c, double x, double y) { Pt p; tf(c, x, y, &p.x, &p.y); if (!c->nsub) path_add_sub(c); path_add_pt(c, p); }
static void m_closePath(Canvas *c) { if (c->nsub && sub_count(c, c->nsub - 1) > 0) path_add_pt(c, c->path[c->sub[c->nsub - 1]]); }
static void m_rect(Canvas *c, double x, double y, double w, double h) {
    path_add_sub(c);
    double xs[5] = { x, x + w, x + w, x, x }, ys[5] = { y, y, y + h, y + h, y };
    for (int i = 0; i < 5; i++) { Pt p; tf(c, xs[i], ys[i], &p.x, &p.y); path_add_pt(c, p); }
}
static void m_ellipse(Canvas *c, double x, double y, double rx, double ry, double rot, double start, double end, int ccw) {
    if (ccw && end > start) end -= 2 * M_PI * ceil((end - start) / (2 * M_PI));
    else if (!ccw && end < start) end += 2 * M_PI * ceil((start - end) / (2 * M_PI));
    double span = end - start;
    if (fabs(span) > 2 * M_PI) span = span > 0 ? 2 * M_PI : -2 * M_PI;
    int steps = (int)(fabs(span) / (2 * M_PI) * 32);
    if (steps < 8) steps = 8;
    int fresh = !(c->nsub && sub_count(c, c->nsub - 1) > 0);
    if (fresh) path_add_sub(c);
    for (int i = 0; i <= steps; i++) {
        double t = start + span * i / steps, px = rx * cos(t), py = ry * sin(t);
        if (rot) { double qx = px * cos(rot) - py * sin(rot), qy = px * sin(rot) + py * cos(rot); px = qx; py = qy; }
        Pt p; tf(c, x + px, y + py, &p.x, &p.y);
        path_add_pt(c, p);
    }
}
static void m_quad(Canvas *c, double cx, double cy, double x, double y) {
    if (!(c->nsub && sub_count(c, c->nsub - 1) > 0)) { m_moveTo(c, x, y); return; }
    Pt p0 = c->path[c->npath - 1], c1, p1;
    tf(c, cx, cy, &c1.x, &c1.y); tf(c, x, y, &p1.x, &p1.y);
    for (int i = 1; i <= 8; i++) {
        double t = i / 8.0, u = 1 - t;
        Pt p = { u * u * p0.x + 2 * u * t * c1.x + t * t * p1.x, u * u * p0.y + 2 * u * t * c1.y + t * t * p1.y };
        path_add_pt(c, p);
    }
}
static void m_bezier(Canvas *c, double c1x, double c1y, double c2x, double c2y, double x, double y) {
    if (!(c->nsub && sub_count(c, c->nsub - 1) > 0)) { m_moveTo(c, x, y); return; }
    Pt p0 = c->path[c->npath - 1], a, b, p1;
    tf(c, c1x, c1y, &a.x, &a.y); tf(c, c2x, c2y, &b.x, &b.y); tf(c, x, y, &p1.x, &p1.y);
    for (int i = 1; i <= 10; i++) {
        double t = i / 10.0, u = 1 - t;
        Pt p = { u * u * u * p0.x + 3 * u * u * t * a.x + 3 * u * t * t * b.x + t * t * t * p1.x,
                 u * u * u * p0.y + 3 * u * u * t * a.y + 3 * u * t * t * b.y + t * t * t * p1.y };
        path_add_pt(c, p);
    }
}
static void m_fill(Natives *n, Canvas *c) { for (int k = 0; k < c->nsub; k++) fill_polygon(n, c, c->path + c->sub[k], sub_count(c, k), c->s.fill, c->s.fillg); }
static void m_stroke(Natives *n, Canvas *c) {
    for (int k = 0; k < c->nsub; k++) {
        const Pt *p = c->path + c->sub[k];
        int cnt = sub_count(c, k);
        stroke_points(n, c, p, cnt, cnt > 2 && p[0].x == p[cnt - 1].x && p[0].y == p[cnt - 1].y, c->s.stroke, c->s.strokeg);
    }
}
static void m_clip(Canvas *c) {
    if (!c->npath) return;
    double minx = 1e18, miny = 1e18, maxx = -1e18, maxy = -1e18;
    for (int i = 0; i < c->npath; i++) {
        if (c->path[i].x < minx) minx = c->path[i].x; if (c->path[i].x > maxx) maxx = c->path[i].x;
        if (c->path[i].y < miny) miny = c->path[i].y; if (c->path[i].y > maxy) maxy = c->path[i].y;
    }
    IRect r = irect((int)floor(minx), (int)floor(miny), (int)ceil(maxx - minx), (int)ceil(maxy - miny));
    c->s.clip = c->s.has_clip ? irect_clip(r, c->s.clip) : r;
    c->s.has_clip = 1;
}

static void m_fillRect(Natives *n, Canvas *c, double x, double y, double w, double h) {
    State *s = &c->s;
    if (!axis_aligned(c)) {
        Pt p[4]; double xs[4] = { x, x + w, x + w, x }, ys[4] = { y, y, y + h, y + h };
        for (int i = 0; i < 4; i++) tf(c, xs[i], ys[i], &p[i].x, &p[i].y);
        fill_polygon(n, c, p, 4, s->fill, s->fillg);
        return;
    }
    IRect r = device_rect(c, x, y, w, h);
    if (r.w <= 0 || r.h <= 0) return;
    if (s->fillg) {
        Op *o = op_new(OP_GRAD);
        o->i[0] = r.x; o->i[1] = r.y; o->i[2] = r.w; o->i[3] = r.h;
        int level = ((int)rnd(s->alpha * 255)) >> 3 << 3;
        o->i[4] = level >= 248 ? 255 : (level ? level : 8);
        o->grad = s->fillg;
        o->comp = (uint8_t)s->comp;
        emit(n, c, o, r, 0);
        return;
    }
    int a = eff_alpha(s->fill, s->alpha);
    if (s->comp == COMP_SRC_IN) { /* clears where the colour has no alpha: no early return */ }
    else if (a <= 0) return;
    Op *o = op_new(OP_FILL);
    o->i[0] = r.x; o->i[1] = r.y; o->i[2] = r.w; o->i[3] = r.h;
    o->color = (s->fill & 0x00FFFFFFu) | ((uint32_t)(a < 0 ? 0 : a) << 24);
    o->comp = (uint8_t)s->comp;
    emit(n, c, o, r, a >= 255 && s->comp == COMP_OVER);
}

static void m_strokeRect(Natives *n, Canvas *c, double x, double y, double w, double h) {
    Pt p[4]; double xs[4] = { x, x + w, x + w, x }, ys[4] = { y, y, y + h, y + h };
    for (int i = 0; i < 4; i++) tf(c, xs[i], ys[i], &p[i].x, &p[i].y);
    stroke_points(n, c, p, 4, 1, c->s.stroke, c->s.strokeg);
}

static void m_clearRect(Natives *n, Canvas *c, double x, double y, double w, double h) {
    IRect r = irect_clip(device_rect(c, x, y, w, h), cv_bounds(c));
    if (irect_empty(r)) return;
    Op *o = op_new(OP_CLEAR);
    o->i[0] = r.x; o->i[1] = r.y; o->i[2] = r.w; o->i[3] = r.h;
    emit(n, c, o, r, c->opaque);
}

static void state_mul(Canvas *c, double na, double nb, double nc, double nd, double ne, double nf) {
    double *m = c->s.m, a = m[0], b = m[1], cc = m[2], d = m[3], e = m[4], f = m[5];
    m[0] = a * na + cc * nb; m[1] = b * na + d * nb; m[2] = a * nc + cc * nd; m[3] = b * nc + d * nd;
    m[4] = a * ne + cc * nf + e; m[5] = b * ne + d * nf + f;
}

/* ---------------------------------------------------------------- images */

static void m_drawImage(Natives *n, Canvas *c, int src_id, const double *a, int na) {
    int bw, bh;
    if (src_id >= IMAGE_ID_BASE) { if (!image_size(n, src_id, &bw, &bh)) return; }
    else { Canvas *sc = cv_get(n, src_id); if (!sc) return; bw = sc->surf->w; bh = sc->surf->h; }
    double sxd, syd, swd, shd, dx, dy, dw, dh;
    if (na == 2) { sxd = 0; syd = 0; swd = bw; shd = bh; dx = a[0]; dy = a[1]; dw = bw; dh = bh; }
    else if (na == 4) { sxd = 0; syd = 0; swd = bw; shd = bh; dx = a[0]; dy = a[1]; dw = a[2]; dh = a[3]; }
    else if (na == 8) { sxd = a[0]; syd = a[1]; swd = a[2]; shd = a[3]; dx = a[4]; dy = a[5]; dw = a[6]; dh = a[7]; }
    else return;
    int sx = (int)rnd(sxd), sy = (int)rnd(syd), sw = (int)rnd(swd), sh = (int)rnd(shd);
    if (sw <= 0 || sh <= 0 || dw == 0 || dh == 0) return;
    double fx = dw / sw, fy = dh / sh;
    if (sx < 0) { dx -= sx * fx; dw += sx * fx; sw += sx; sx = 0; }
    if (sy < 0) { dy -= sy * fy; dh += sy * fy; sh += sy; sy = 0; }
    if (sx + sw > bw) { dw -= (sx + sw - bw) * fx; sw = bw - sx; }
    if (sy + sh > bh) { dh -= (sy + sh - bh) * fy; sh = bh - sy; }
    if (sw <= 0 || sh <= 0 || dw == 0 || dh == 0) return;
    int version = 0;
    if (src_id < IMAGE_ID_BASE) {
        Canvas *sc = cv_get(n, src_id);
        if (src_id == c->id || (sc->retained && !sc->persistent)) {          /* reading a retained canvas back: stop deferring its drawing */
            cv_go_persistent(n, sc);
            if (src_id == c->id) cv_go_persistent(n, c);
        }
        version = sc->version;
    }
    State *s = &c->s;
    int alpha = (int)(s->alpha * 255);
    const double *m = s->m;
    int kind = 0;
    if (src_id >= IMAGE_ID_BASE) kind = image_kind(n, src_id);
    if (src_id >= IMAGE_ID_BASE && kind < 0) { image_surface(n, src_id, &kind); }
    if (src_id < IMAGE_ID_BASE) { Canvas *oc = cv_get(n, src_id); kind = oc && oc != c && !(oc->retained && !oc->persistent) && canvas_opaque(oc) ? KIND_OPAQUE : KIND_ALPHA; }
    if (axis_aligned(c)) {
        double x0, y0, x1, y1;
        tf(c, dx, dy, &x0, &y0); tf(c, dx + dw, dy + dh, &x1, &y1);
        int flip_x = x1 < x0, flip_y = y1 < y0;
        int left = (int)rnd(flip_x ? x1 : x0), top = (int)rnd(flip_y ? y1 : y0);
        int width = (int)rnd(fabs(x1 - x0)), height = (int)rnd(fabs(y1 - y0));
        if (width < 1) width = 1;
        if (height < 1) height = 1;
        Op *o = op_new(OP_IMAGE);
        o->i[0] = src_id; o->i[1] = version; o->i[2] = sx; o->i[3] = sy; o->i[4] = sw; o->i[5] = sh;
        o->i[6] = left; o->i[7] = top; o->i[8] = width; o->i[9] = height; o->i[10] = flip_x; o->i[11] = flip_y; o->i[12] = alpha;
        o->comp = (uint8_t)s->comp;
        emit(n, c, o, irect(left, top, width, height), kind == KIND_OPAQUE && alpha >= 255 && s->comp == COMP_OVER);
        return;
    }
    if (m[1] == 0 && m[2] != 0 && m[0] != 0 && m[3] != 0) {                   /* horizontal shear: cast shadows */
        double ma = m[0], mc = m[2], md = m[3], me = m[4], mf = m[5];
        int w = (int)rnd(fabs(ma * dw)), h = (int)rnd(fabs(md * dh));
        if (w < 1) w = 1;
        if (h < 1) h = 1;
        if (w > 2048 || h > 2048) return;
        float *rel = malloc(sizeof(float) * (size_t)h);
        double lo = 1e18, hi = -1e18;
        double *xs = malloc(sizeof(double) * (size_t)h);
        for (int r = 0; r < h; r++) {
            double y = md > 0 ? dy + (r + 0.5) * dh / h : dy + dh - (r + 0.5) * dh / h;
            xs[r] = mc * y;
            if (xs[r] < lo) lo = xs[r]; if (xs[r] > hi) hi = xs[r];
        }
        for (int r = 0; r < h; r++) rel[r] = (float)(xs[r] - lo);
        free(xs);
        double left_x = (ma > 0 ? ma * dx : ma * (dx + dw)) + me;
        int left = (int)floor(left_x + lo);
        double t0 = md * dy, t1 = md * (dy + dh);
        int top = (int)rnd((t0 < t1 ? t0 : t1) + mf);
        int span = (int)ceil(hi - lo) + 1;
        Op *o = op_new(OP_SHEAR);
        o->f = rel; o->nf = h;
        o->i[0] = src_id; o->i[1] = version; o->i[2] = sx; o->i[3] = sy; o->i[4] = sw; o->i[5] = sh;
        o->i[6] = w; o->i[7] = h; o->i[8] = left; o->i[9] = top; o->i[10] = ma < 0; o->i[11] = md < 0; o->i[12] = alpha;
        o->i[13] = (int)rnd(mc * 64);
        o->comp = (uint8_t)s->comp;
        emit(n, c, o, irect(left, top, w + span, h), 0);
        return;
    }
    /* rotated or skewed: rotate around the destination centre */
    double sxs = hypot(m[0], m[1]), sys_ = hypot(m[2], m[3]);
    double angle = atan2(m[1], m[0]) * 180.0 / M_PI;
    int tw = (int)rnd(fabs(dw) * sxs), th = (int)rnd(fabs(dh) * sys_);
    if (tw < 1) tw = 1;
    if (th < 1) th = 1;
    int mirrored = (m[0] * m[3] - m[1] * m[2]) < 0;
    double cx, cy;
    tf(c, dx + dw / 2, dy + dh / 2, &cx, &cy);
    int radius = (int)(hypot(tw, th) / 2) + 2;
    int qangle2 = (int)rnd(angle * 2);
    Op *o = op_new(OP_ROT);
    o->i[0] = src_id; o->i[1] = version; o->i[2] = sx; o->i[3] = sy; o->i[4] = sw; o->i[5] = sh;
    o->i[6] = tw; o->i[7] = th; o->i[8] = mirrored; o->i[9] = qangle2; o->i[10] = (int)rnd(cx); o->i[11] = (int)rnd(cy); o->i[12] = alpha;
    o->comp = (uint8_t)s->comp;
    emit(n, c, o, irect((int)rnd(cx) - radius, (int)rnd(cy) - radius, radius * 2, radius * 2), 0);
}

/* ---------------------------------------------------------------- text */

static int font_px_of(const State *s) { long v = rnd(s->font_size * 1.25); return v < 6 ? 6 : (int)v; }

static void m_text(Natives *n, Canvas *c, const char *text, double x, double y, int stroke) {
    if (!*text) return;
    State *s = &c->s;
    Gradient *grad = stroke ? s->strokeg : s->fillg;
    uint32_t style = stroke ? s->stroke : s->fill;
    uint32_t color = grad ? RGBA(255, 255, 255, (int)(255 * s->alpha)) : ((style & 0x00FFFFFFu) | ((uint32_t)eff_alpha(style, s->alpha) << 24));
    if (PA(color) <= 0) return;
    int px_size = font_px_of(s), h;
    int w = text_size(text, px_size, s->bold, s->italic, &h);
    double px, py;
    tf(c, x, y, &px, &py);
    if (s->align == ALIGN_CENTER) px -= w / 2.0; else if (s->align == ALIGN_RIGHT) px -= w;
    if (s->baseline == BASE_MIDDLE) py -= h / 2.0; else if (s->baseline == BASE_BOTTOM) py -= h; else if (s->baseline != BASE_TOP) py -= h * 0.8;
    int left = (int)rnd(px), top = (int)rnd(py);
    Op *o = op_new(OP_TEXT);
    o->text = strdup(text);
    o->i[0] = px_size; o->i[1] = s->bold; o->i[2] = s->italic; o->i[3] = left; o->i[4] = top;
    o->color = color; o->grad = grad;
    o->comp = (uint8_t)s->comp;
    emit(n, c, o, irect(left, top, w + (s->italic ? h / 4 : 0) + (s->bold ? 1 : 0) + 2, h), 0);
}

/* ---------------------------------------------------------------- compositing the main canvas */

#define FULL_REDRAW_FRACTION 0.6
#define MARGIN 6

typedef struct { IRect *v; int n, cap; } RectList;
static void rl_push(RectList *l, IRect r) { if (l->n == l->cap) { l->cap = l->cap ? l->cap * 2 : 16; l->v = realloc(l->v, l->cap * sizeof(IRect)); } l->v[l->n++] = r; }

/* Do the opaque ops of a frame together paint every pixel? (subtract their boxes from the canvas) */
static int fully_covered(const Canvas *c, OpList *ops) {
    IRect full = cv_bounds(c);
    RectList left = { 0 };
    rl_push(&left, full);
    for (int i = 0; i < ops->n && left.n; i++) {
        const Op *o = ops->v[i];
        if (!o->covers) continue;
        RectList next = { 0 };
        for (int k = 0; k < left.n; k++) {
            IRect r = left.v[k];
            if (!irect_collide(r, o->box)) { rl_push(&next, r); continue; }
            IRect b = irect_clip(r, o->box);
            if (b.y > r.y) rl_push(&next, irect(r.x, r.y, r.w, b.y - r.y));
            if (b.y + b.h < r.y + r.h) rl_push(&next, irect(r.x, b.y + b.h, r.w, r.y + r.h - b.y - b.h));
            if (b.x > r.x) rl_push(&next, irect(r.x, b.y, b.x - r.x, b.h));
            if (b.x + b.w < r.x + r.w) rl_push(&next, irect(b.x + b.w, b.y, r.x + r.w - b.x - b.w, b.h));
        }
        free(left.v);
        left = next;
        if (left.n > 512) { free(left.v); return 0; }
    }
    int covered = left.n == 0;
    free(left.v);
    return covered;
}

static void merge_rects(RectList *in, RectList *out, int limit) {
    for (int i = 0; i < in->n; i++) if (in->v[i].w > 0 && in->v[i].h > 0) rl_push(out, in->v[i]);
    int changed = 1;
    while (changed && out->n > 1) {
        changed = 0;
        RectList next = { 0 };
        for (int i = 0; i < out->n; i++) {
            IRect r = out->v[i];
            int merged = 0;
            for (int k = 0; k < next.n; k++) {
                IRect o = next.v[k];
                IRect big = irect(o.x - MARGIN, o.y - MARGIN, o.w + MARGIN * 2, o.h + MARGIN * 2);
                if (irect_collide(big, r)) { next.v[k] = irect_union(o, r); merged = 1; changed = 1; break; }
            }
            if (!merged) rl_push(&next, r);
        }
        free(out->v);
        *out = next;
    }
    if (out->n > limit) {
        IRect u = out->v[0];
        for (int i = 1; i < out->n; i++) u = irect_union(u, out->v[i]);
        out->n = 1; out->v[0] = u;
    }
}

static void run_all(Natives *n, Canvas *c, OpList *ops) { for (int i = 0; i < ops->n; i++) run_op(n, c, ops->v[i], NULL); }

static void out_push(IRect *out, int *count, int max, IRect r) {
    if (*count < max) out[(*count)++] = r;
    else { out[max - 1] = irect_union(out[max - 1], r); }
}

/* Repainting: the changed area is a set of rectangles (see composite). Big ones are cut into horizontal strips, and all pieces are
 * painted by the pool on every CPU core: the pieces never overlap, and every op only reads shared data. Images and text the pieces
 * need are loaded first on this thread; a miss inside the pool is flagged and everything is painted again on one thread. */
typedef struct { IRect r; int start; } Item;
typedef struct { Natives *n; Canvas *c; OpList *ops; Item *items; } Replay;

static void replay_item(void *vp, int k) {
    Replay *rp = vp;
    const Item *it = &rp->items[k];
    t_peek = 1;
    for (int i = it->start; i < rp->ops->n; i++) {
        const Op *o = rp->ops->v[i];
        if (irect_collide(o->box, it->r)) run_op(rp->n, rp->c, o, &it->r);
    }
    t_peek = 0;
}

static void replay_items(Natives *n, Canvas *c, OpList *ops, Item *items, int count) {
    long area = 0;
    for (int k = 0; k < count; k++) area += (long)items[k].r.w * items[k].r.h;
    if (pool_threads() < 2 || area < 30000 || count < 2) {
        for (int k = 0; k < count; k++)
            for (int i = items[k].start; i < ops->n; i++) if (irect_collide(ops->v[i]->box, items[k].r)) run_op(n, c, ops->v[i], &items[k].r);
        return;
    }
    for (int k = 0; k < count; k++)                          /* load what the pieces will read */
        for (int i = items[k].start; i < ops->n; i++) {
            const Op *o = ops->v[i];
            if (!irect_collide(o->box, items[k].r)) continue;
            int kind;
            if (o->kind == OP_IMAGE || o->kind == OP_SHEAR || o->kind == OP_ROT) source_surf(n, o->i[0], &kind);
            else if (o->kind == OP_TEXT) text_mask(o->text, o->i[0], o->i[1], o->i[2]);
        }
    Replay rp = { n, c, ops, items };
    g_mt_miss = 0;
    pool_run(replay_item, &rp, count);
    if (g_mt_miss) {                                         /* something was evicted while loading: paint everything again, alone */
        g_mt_miss = 0;
        for (int k = 0; k < count; k++)
            for (int i = items[k].start; i < ops->n; i++) if (irect_collide(ops->v[i]->box, items[k].r)) run_op(n, c, ops->v[i], &items[k].r);
    }
}

/* The changed area as tiles: scattered small changes and long thin ones (a line across the screen) repaint only the tiles they touch,
 * not their bounding boxes. Horizontal runs of dirty tiles become rectangles; equal runs in consecutive rows are joined. */
#define TILE 32
static void tiles_to_rects(const uint8_t *tile, int tw, int th, IRect full, RectList *out) {
    int first = out->n;
    for (int ty = 0; ty < th; ty++) {
        int tx = 0;
        while (tx < tw) {
            while (tx < tw && !tile[ty * tw + tx]) tx++;
            if (tx >= tw) break;
            int x0 = tx;
            while (tx < tw && tile[ty * tw + tx]) tx++;
            IRect r = irect(x0 * TILE, ty * TILE, (tx - x0) * TILE, TILE);
            r = irect_clip(r, full);
            int joined = 0;
            for (int k = out->n - 1; k >= first && k >= out->n - 64; k--) {          /* the run above, if identical in x */
                IRect *o = &out->v[k];
                if (o->x == r.x && o->w == r.w && o->y + o->h == r.y) { o->h += r.h; joined = 1; break; }
            }
            if (!joined) rl_push(out, r);
        }
    }
}

/* Draw one recorded frame; appends the changed regions to out. */
static void composite(Natives *n, Canvas *c, OpList *ops, IRect *out, int *count, int max) {
    IRect full = cv_bounds(c);
    if (!fully_covered(c, ops)) {                           /* not a full repaint: behave like a normal persistent canvas */
        run_all(n, c, ops);
        ol_clear(&c->prev);
        c->have_prev = 0;
        c->version++;
        ol_clear(ops);
        out_push(out, count, max, full);
        return;
    }
    if (!c->have_prev) {
        run_all(n, c, ops);
        c->version++;
        st_replayed += ops->n;
        OpList old = c->prev; c->prev = *ops; ol_free(&old); memset(ops, 0, sizeof *ops);
        c->have_prev = 1;
        out_push(out, count, max, full);
        return;
    }
    OpList *prev = &c->prev;
    RectList changed = { 0 };
    int total = prev->n > ops->n ? prev->n : ops->n;
    for (int i = 0; i < total; i++) {
        Op *a = i < prev->n ? prev->v[i] : NULL, *b = i < ops->n ? ops->v[i] : NULL;
        if (!a || !b || !op_same(a, b)) { if (a) rl_push(&changed, a->box); if (b) rl_push(&changed, b->box); }
    }
    if (!changed.n) {
        st_skipped++;
        free(changed.v);
        ol_clear(ops);
        return;
    }
    int tw = (full.w + TILE - 1) / TILE, th = (full.h + TILE - 1) / TILE;
    uint8_t *tile = calloc((size_t)tw * (size_t)th, 1);
    long ndirty = 0;
    for (int k = 0; k < changed.n; k++) {
        IRect q = irect_clip(irect(changed.v[k].x - 1, changed.v[k].y - 1, changed.v[k].w + 2, changed.v[k].h + 2), full);
        if (irect_empty(q)) continue;
        for (int ty = q.y / TILE; ty <= (q.y + q.h - 1) / TILE; ty++)
            for (int tx = q.x / TILE; tx <= (q.x + q.w - 1) / TILE; tx++)
                if (!tile[ty * tw + tx]) { tile[ty * tw + tx] = 1; ndirty++; }
    }
    free(changed.v);
    RectList regions = { 0 };
    if (getenv("SGBA_FULL") || (double)ndirty * TILE * TILE > FULL_REDRAW_FRACTION * full.w * full.h) rl_push(&regions, full);
    else tiles_to_rects(tile, tw, th, full, &regions);
    free(tile);
    int nthreads = pool_threads();
    Item *items = malloc(sizeof(Item) * (size_t)(regions.n * (4 * nthreads + 1) + 1));
    int nitems = 0;
    for (int r = 0; r < regions.n; r++) {
        IRect region = regions.v[r];
        int start = 0;
        for (int i = ops->n - 1; i >= 0; i--) {              /* anything under the last opaque op that covers the region is hidden */
            const Op *o = ops->v[i];
            if (o->covers && irect_contains(o->box, region)) { start = i; break; }
        }
        for (int i = start; i < ops->n; i++) if (irect_collide(ops->v[i]->box, region)) st_replayed++;
        int strips = 1;
        if (nthreads > 1 && (long)region.w * region.h >= 30000 && region.h >= 48) {
            strips = region.h / 24;
            if (strips > 4 * nthreads) strips = 4 * nthreads;
            if (strips < 2) strips = 2;
        }
        for (int k = 0; k < strips; k++) {
            int y0 = region.y + (int)((int64_t)region.h * k / strips), y1 = region.y + (int)((int64_t)region.h * (k + 1) / strips);
            items[nitems].r = irect(region.x, y0, region.w, y1 - y0);
            items[nitems].start = start;
            nitems++;
        }
    }
    for (int k = 0; k < nitems; k++) st_area += (long)items[k].r.w * items[k].r.h;
    replay_items(n, c, ops, items, nitems);
    free(items);
    RectList shown = { 0 };                                   /* what is handed to the display: a few joined rectangles */
    merge_rects(&regions, &shown, 6);
    for (int r = 0; r < shown.n; r++) out_push(out, count, max, shown.v[r]);
    free(shown.v);
    free(regions.v);
    c->version++;
    OpList old = c->prev; c->prev = *ops; ol_free(&old); memset(ops, 0, sizeof *ops);
}

static void flush_stale(Natives *n, Canvas *c) {
    for (int i = 0; i < c->nstale; i++) { run_all(n, c, &c->stale[i]); ol_clear(&c->stale[i]); }
    c->nstale = 0;
    ol_clear(&c->prev);
    c->have_prev = 0;
    c->force_full = 1;
    c->version++;
}

void canvas_begin_frame(Natives *n) {
    Canvas *c = cv_get(n, n->main_id);
    if (!c || !c->retained || c->persistent || !c->pending.n) return;
    if (c->nstale == c->stalecap) { c->stalecap = c->stalecap ? c->stalecap * 2 : 8; c->stale = realloc(c->stale, c->stalecap * sizeof(OpList)); }
    c->stale[c->nstale++] = c->pending;
    memset(&c->pending, 0, sizeof c->pending);
    if (c->nstale > 8) flush_stale(n, c);
}

int canvas_take_dirty(Natives *n, IRect *out, int max, const Surf **surface) {
    Canvas *c = cv_get(n, n->main_id);
    if (!c) return 0;
    *surface = c->surf;
    int count = 0;
    IRect full = cv_bounds(c);
    st_frames++;
    static int first = 1;
    if (!c->retained || c->persistent) {
        int touched = c->touched || c->pending.n || c->nstale;
        c->touched = 0;
        if (first) { first = 0; out[0] = full; return 1; }
        if (touched) { out[0] = full; return 1; }
        return 0;
    }
    int nframes = c->nstale + (c->pending.n ? 1 : 0);
    OpList *frames = malloc(sizeof(OpList) * (size_t)(nframes ? nframes : 1));
    int k = 0;
    for (int i = 0; i < c->nstale; i++) if (c->stale[i].n) frames[k++] = c->stale[i]; else ol_free(&c->stale[i]);
    c->nstale = 0;
    if (c->pending.n) { frames[k++] = c->pending; memset(&c->pending, 0, sizeof c->pending); }
    nframes = k;
    if (!nframes) {
        free(frames);
        if (c->force_full) { c->force_full = 0; return first ? (first = 0, out[0] = full, 1) : (out[0] = full, 1); }
        if (first) { first = 0; out[0] = full; return 1; }
        return 0;
    }
    int from = 0;
    for (int i = nframes - 1; i >= 0; i--) {                 /* a complete repaint makes every earlier frame irrelevant */
        if (fully_covered(c, &frames[i])) { from = i; st_skipped += i; break; }
    }
    for (int i = 0; i < from; i++) ol_free(&frames[i]);
    for (int i = from; i < nframes; i++) { composite(n, c, &frames[i], out, &count, max); ol_free(&frames[i]); }
    free(frames);
    if (c->force_full) { c->force_full = 0; out[0] = full; count = 1; }
    if (first) { first = 0; out[0] = full; count = 1; }
    return count;
}

const Surf *canvas_surface(Natives *n, int id) { Canvas *c = cv_get(n, id); return c ? c->surf : NULL; }

/* ---- the HTML overlay: shell/overlay.js lays the page's menus out on a panel canvas; the runtime draws it over the game ---- */
NATIVE(n_overlay_set) {
    Natives *n = NAT();
    int cid = js_arg_i(ctx, argv, 0);
    n->ov_cid = cid;
    n->ov_x = js_arg_i(ctx, argv, 1); n->ov_y = js_arg_i(ctx, argv, 2); n->ov_w = js_arg_i(ctx, argv, 3); n->ov_h = js_arg_i(ctx, argv, 4);
    n->ov_changed = 1;
    return JS_UNDEFINED;
}
NATIVE(n_overlay_modal) { Natives *n = NAT(); int m = js_arg_i(ctx, argv, 0); if (m != n->ov_modal) { n->ov_modal = m; } return JS_UNDEFINED; }
NATIVE(n_measure_h) {
    Natives *n = NAT(); Canvas *c = cv_get(n, js_arg_i(ctx, argv, 0)); if (!c) return JS_UNDEFINED;
    int h = 0;
    if (js_arg_i(ctx, argv, 1)) {                                                                    /* size()[1] of this text */
        const char *t = JS_ToCString(ctx, argv[2]);
        text_size(t ? t : "", font_px_of(&c->s), c->s.bold, c->s.italic, &h);
        if (t) JS_FreeCString(ctx, t);
    }
    else h = text_font_height(font_px_of(&c->s));                                                    /* get_height() */
    return JS_NewInt32(ctx, h);
}

int overlay_get(Natives *n, const Surf **panel, int *x, int *y, int *modal, int *changed, int *was_visible) {
    static int last_visible = 0;
    int visible = n->ov_cid >= 0 && n->ov_w > 0 && n->ov_h > 0;
    *changed = n->ov_changed || visible != last_visible;
    if (was_visible) *was_visible = last_visible;
    last_visible = visible;
    n->ov_changed = 0;
    *modal = n->ov_modal;
    if (!visible) return 0;
    *panel = canvas_surface(n, n->ov_cid);
    *x = n->ov_x; *y = n->ov_y;
    return *panel != NULL;
}

void canvas_force_full(Natives *n) { Canvas *c = cv_get(n, n->main_id); if (c) c->force_full = 1; }

int canvas_main_size(Natives *n, int *w, int *h) {
    Canvas *c = cv_get(n, n->main_id);
    if (!c) return 0;
    *w = c->surf->w; *h = c->surf->h;
    return 1;
}

void canvas_stats(Natives *n, long *ops, long *replayed, long *skipped, long *frames) { (void)n; *ops = st_ops; *replayed = st_replayed; *skipped = st_skipped; *frames = st_frames; }
long canvas_repaint_pixels(void) { return st_area; }

/* ---------------------------------------------------------------- display lists ("groups") */

#define GROUP_LIMIT 600
#define GROUP_MAX_OPS 400

static uint64_t fnv(const char *s) { uint64_t h = 1469598103934665603ULL; while (*s) { h ^= (unsigned char)*s++; h *= 1099511628211ULL; } return h; }

static int group_begin(Natives *n, Canvas *c, const char *key) {
    if (!c->retained || c->persistent || c->grp_open) return 2;
    State *s = &c->s;
    char buf[1024];
    snprintf(buf, sizeof buf, "%.400s|%.9g,%.9g,%.9g,%.9g,%.9g,%.9g|%.6g|%d|%u,%p|%u,%p|%.6g|%.6g,%d,%d,%d,%d|%d,%d,%d,%d,%d", key, s->m[0], s->m[1], s->m[2], s->m[3], s->m[4], s->m[5],
             s->alpha, s->comp, s->fill, (void *)s->fillg, s->stroke, (void *)s->strokeg, s->lw, s->font_size, s->bold, s->italic, s->align, s->baseline,
             s->has_clip, s->clip.x, s->clip.y, s->clip.w, s->clip.h);
    uint64_t h = fnv(buf);
    for (int i = 0; i < c->ngroups; i++) {
        Group *g = &c->groups[i];
        if (g->hash != h || strcmp(g->sig, buf)) continue;
        for (int d = 0; d < g->ndeps; d++) { Canvas *sc = cv_get(n, g->dep_src[d]); if (!sc || sc->version != g->dep_ver[d]) return (c->grp_open = 1, c->grp_hash = h, c->grp_sig = strdup(buf), c->grp_start = c->pending.n, 0); }
        g->used = ++c->group_tick;
        for (int k = 0; k < g->ops.n; k++) { op_ref(g->ops.v[k]); ol_push(&c->pending, g->ops.v[k]); }
        st_ops += g->ops.n;
        return 1;
    }
    c->grp_open = 1; c->grp_hash = h; c->grp_sig = strdup(buf); c->grp_start = c->pending.n;
    return 0;
}

static void group_end(Natives *n, Canvas *c) {
    (void)n;
    if (!c->grp_open) return;
    c->grp_open = 0;
    char *sig = c->grp_sig; c->grp_sig = NULL;
    int count = c->pending.n - c->grp_start;
    if (c->persistent || !c->retained || count <= 0 || count > GROUP_MAX_OPS) { free(sig); return; }
    int *dsrc = malloc(sizeof(int) * (size_t)count), *dver = malloc(sizeof(int) * (size_t)count), nd = 0;
    for (int k = c->grp_start; k < c->pending.n; k++) {
        const Op *o = c->pending.v[k];
        if ((o->kind == OP_IMAGE || o->kind == OP_ROT || o->kind == OP_SHEAR) && o->i[0] < IMAGE_ID_BASE) {
            int seen = 0;
            for (int d = 0; d < nd; d++) if (dsrc[d] == o->i[0]) seen = 1;
            if (!seen) { dsrc[nd] = o->i[0]; dver[nd] = o->i[1]; nd++; }
        }
    }
    int slot = -1;
    for (int i = 0; i < c->ngroups; i++) if (c->groups[i].hash == c->grp_hash && !strcmp(c->groups[i].sig, sig)) { slot = i; break; }
    if (slot < 0) {
        if (c->ngroups < GROUP_LIMIT) {
            if (c->ngroups == c->groupcap) { c->groupcap = c->groupcap ? c->groupcap * 2 : 64; c->groups = realloc(c->groups, c->groupcap * sizeof(Group)); }
            slot = c->ngroups++;
        } else {
            slot = 0;
            for (int i = 1; i < c->ngroups; i++) if (c->groups[i].used < c->groups[slot].used) slot = i;
            group_free(&c->groups[slot]);
        }
    } else group_free(&c->groups[slot]);
    Group *g = &c->groups[slot];
    memset(g, 0, sizeof *g);
    g->hash = c->grp_hash; g->sig = sig; g->used = ++c->group_tick;
    for (int k = c->grp_start; k < c->pending.n; k++) { op_ref(c->pending.v[k]); ol_push(&g->ops, c->pending.v[k]); }
    g->dep_src = dsrc; g->dep_ver = dver; g->ndeps = nd;
}

/* ---------------------------------------------------------------- JS entry points */

static int comp_of(const char *s) {
    if (!strcmp(s, "multiply")) return COMP_MULTIPLY;
    if (!strcmp(s, "lighter")) return COMP_LIGHTER;
    if (!strcmp(s, "source-in")) return COMP_SRC_IN;
    if (!strcmp(s, "source-atop")) return COMP_SRC_ATOP;
    return COMP_OVER;
}

static void set_style(State *s, int stroke, const char *text) {
    if (text[0] == '{') { Gradient *g = gradient_intern(text); if (stroke) s->strokeg = g; else s->fillg = g; return; }
    if (stroke) { s->stroke = parse_color(text); s->strokeg = NULL; } else { s->fill = parse_color(text); s->fillg = NULL; }
}

static void set_font(State *s, const char *text) {
    double size = 10;
    for (const char *p = text; *p; p++) {
        if (isdigit((unsigned char)*p)) {
            char *end; double v = strtod(p, &end);
            const char *q = end; while (isspace((unsigned char)*q)) q++;
            if (!strncmp(q, "px", 2)) { size = v; break; }
            p = end - 1;
        }
    }
    s->font_size = size;
    s->bold = strstr(text, "bold") || strstr(text, "700") || strstr(text, "800");
    s->italic = strstr(text, "italic") != NULL;
}

NATIVE(n_new_canvas) {
    Natives *n = NAT();
    int id = js_arg_i(ctx, argv, 0);
    if (id >= 0 && id < n->canvas_cap && n->canvases[id]) { cv_free(n->canvases[id]); n->canvases[id] = NULL; }
    cv_create(n, id, js_arg_i(ctx, argv, 1), js_arg_i(ctx, argv, 2));
    return JS_UNDEFINED;
}

NATIVE(n_resize) {
    Natives *n = NAT();
    Canvas *c = cv_get(n, js_arg_i(ctx, argv, 0));
    if (!c) return JS_UNDEFINED;
    int w = js_arg_i(ctx, argv, 1), h = js_arg_i(ctx, argv, 2);
    if (w < 1) w = 1; if (h < 1) h = 1; if (w > 4096) w = 4096; if (h > 4096) h = 4096;
    if (w == c->surf->w && h == c->surf->h) return JS_UNDEFINED;
    surf_free(c->surf);
    c->surf = surf_create(w, h);
    if (c->retained) {
        surf_fill_all(c->surf, 0xFF000000u);
        ol_clear(&c->prev); c->have_prev = 0; ol_clear(&c->pending);
        for (int i = 0; i < c->nstale; i++) ol_clear(&c->stale[i]);
        c->nstale = 0;
        for (int i = 0; i < c->ngroups; i++) group_free(&c->groups[i]);
        c->ngroups = 0;
        c->force_full = 1;
    }
    cv_reset_state(c);
    c->version++;
    return JS_UNDEFINED;
}

NATIVE(n_set) {
    Natives *n = NAT();
    Canvas *c = cv_get(n, js_arg_i(ctx, argv, 0));
    if (!c) return JS_UNDEFINED;
    const char *name = JS_ToCString(ctx, argv[1]);
    State *s = &c->s;
    if (!strcmp(name, "fillStyle") || !strcmp(name, "strokeStyle")) {
        const char *v = JS_ToCString(ctx, argv[2]);
        if (v) { set_style(s, name[0] == 's', v); JS_FreeCString(ctx, v); }
    } else if (!strcmp(name, "globalAlpha")) { double a = js_arg_d(ctx, argv, 2); s->alpha = a < 0 ? 0 : a > 1 ? 1 : a; }
    else if (!strcmp(name, "lineWidth")) { double w = js_arg_d(ctx, argv, 2); s->lw = w < 0 ? 0 : w; }
    else if (!strcmp(name, "font")) { const char *v = JS_ToCString(ctx, argv[2]); if (v) { set_font(s, v); JS_FreeCString(ctx, v); } }
    else if (!strcmp(name, "textAlign")) { const char *v = JS_ToCString(ctx, argv[2]); if (v) { s->align = !strcmp(v, "center") ? ALIGN_CENTER : (!strcmp(v, "right") || !strcmp(v, "end")) ? ALIGN_RIGHT : ALIGN_START; JS_FreeCString(ctx, v); } }
    else if (!strcmp(name, "textBaseline")) { const char *v = JS_ToCString(ctx, argv[2]); if (v) { s->baseline = !strcmp(v, "middle") ? BASE_MIDDLE : (!strcmp(v, "bottom") || !strcmp(v, "ideographic")) ? BASE_BOTTOM : (!strcmp(v, "top") || !strcmp(v, "hanging")) ? BASE_TOP : BASE_ALPHA; JS_FreeCString(ctx, v); } }
    else if (!strcmp(name, "globalCompositeOperation")) { const char *v = JS_ToCString(ctx, argv[2]); if (v) { s->comp = comp_of(v); JS_FreeCString(ctx, v); } }
    JS_FreeCString(ctx, name);
    return JS_UNDEFINED;
}

#define CANVAS_ARG() Natives *n = NAT(); Canvas *c = cv_get(n, js_arg_i(ctx, argv, 0)); if (!c) return JS_UNDEFINED
#define D(i) js_arg_d(ctx, argv, i)

NATIVE(n_fillRect) { CANVAS_ARG(); m_fillRect(n, c, D(1), D(2), D(3), D(4)); return JS_UNDEFINED; }
NATIVE(n_strokeRect) { CANVAS_ARG(); m_strokeRect(n, c, D(1), D(2), D(3), D(4)); return JS_UNDEFINED; }
NATIVE(n_clearRect) { CANVAS_ARG(); m_clearRect(n, c, D(1), D(2), D(3), D(4)); return JS_UNDEFINED; }
NATIVE(n_rect) { CANVAS_ARG(); m_rect(c, D(1), D(2), D(3), D(4)); return JS_UNDEFINED; }
NATIVE(n_moveTo) { CANVAS_ARG(); m_moveTo(c, D(1), D(2)); return JS_UNDEFINED; }
NATIVE(n_lineTo) { CANVAS_ARG(); m_lineTo(c, D(1), D(2)); return JS_UNDEFINED; }
NATIVE(n_translate) { CANVAS_ARG(); state_mul(c, 1, 0, 0, 1, D(1), D(2)); return JS_UNDEFINED; }
NATIVE(n_scale) { CANVAS_ARG(); state_mul(c, D(1), 0, 0, D(2), 0, 0); return JS_UNDEFINED; }
NATIVE(n_beginPath) { CANVAS_ARG(); c->npath = 0; c->nsub = 0; return JS_UNDEFINED; }
NATIVE(n_save) {
    CANVAS_ARG();
    if (c->sp == c->scap) { c->scap = c->scap ? c->scap * 2 : 16; c->stack = realloc(c->stack, c->scap * sizeof(State)); }
    c->stack[c->sp++] = c->s;
    return JS_UNDEFINED;
}
NATIVE(n_restore) { CANVAS_ARG(); if (c->sp) c->s = c->stack[--c->sp]; return JS_UNDEFINED; }
NATIVE(n_reset_state) { CANVAS_ARG(); cv_reset_state(c); return JS_UNDEFINED; }

NATIVE(n_drawImage) {
    CANVAS_ARG();
    int src = js_arg_i(ctx, argv, 1);
    double a[8];
    int na = argc - 2;
    if (na != 2 && na != 4 && na != 8) return JS_UNDEFINED;
    for (int i = 0; i < na; i++) a[i] = D(i + 2);
    m_drawImage(n, c, src, a, na);
    return JS_UNDEFINED;
}

NATIVE(n_call) {                          /* the less frequent calls, by name */
    CANVAS_ARG();
    const char *name = JS_ToCString(ctx, argv[1]);
    int na = argc - 2;
    double a[8] = { 0 };
    for (int i = 0; i < na && i < 8; i++) { if (JS_IsString(argv[i + 2])) a[i] = 0; else a[i] = D(i + 2); }
    if (!strcmp(name, "closePath")) m_closePath(c);
    else if (!strcmp(name, "arc")) m_ellipse(c, a[0], a[1], a[2], a[2], 0, a[3], a[4], na > 5 && JS_ToBool(ctx, argv[7]) > 0);
    else if (!strcmp(name, "ellipse")) m_ellipse(c, a[0], a[1], a[2], a[3], a[4], a[5], a[6], na > 7 && JS_ToBool(ctx, argv[9]) > 0);
    else if (!strcmp(name, "arcTo")) m_lineTo(c, a[0], a[1]);
    else if (!strcmp(name, "quadraticCurveTo")) m_quad(c, a[0], a[1], a[2], a[3]);
    else if (!strcmp(name, "bezierCurveTo")) m_bezier(c, a[0], a[1], a[2], a[3], a[4], a[5]);
    else if (!strcmp(name, "fill")) m_fill(n, c);
    else if (!strcmp(name, "stroke")) m_stroke(n, c);
    else if (!strcmp(name, "clip")) m_clip(c);
    else if (!strcmp(name, "rotate")) { double cs = cos(a[0]), sn = sin(a[0]); state_mul(c, cs, sn, -sn, cs, 0, 0); }
    else if (!strcmp(name, "transform")) state_mul(c, a[0], a[1], a[2], a[3], a[4], a[5]);
    else if (!strcmp(name, "setTransform")) { double dflt[6] = { 1, 0, 0, 1, 0, 0 }; for (int i = 0; i < 6; i++) c->s.m[i] = i < na ? a[i] : dflt[i]; }
    else if (!strcmp(name, "resetTransform")) { double dflt[6] = { 1, 0, 0, 1, 0, 0 }; memcpy(c->s.m, dflt, sizeof dflt); }
    else if (!strcmp(name, "fillText") || !strcmp(name, "strokeText")) {
        const char *t = JS_ToCString(ctx, argv[2]);
        if (t) { m_text(n, c, t, D(3), D(4), name[0] == 's'); JS_FreeCString(ctx, t); }
    }
    JS_FreeCString(ctx, name);
    return JS_UNDEFINED;
}

NATIVE(n_measure) {
    CANVAS_ARG();
    const char *t = JS_ToCString(ctx, argv[1]);
    int h, w = t ? text_size(t, font_px_of(&c->s), c->s.bold, c->s.italic, &h) : 0;
    if (t) JS_FreeCString(ctx, t);
    return JS_NewFloat64(ctx, w);
}

NATIVE(n_getimage) {
    CANVAS_ARG();
    int x = js_arg_i(ctx, argv, 1), y = js_arg_i(ctx, argv, 2), w = js_arg_i(ctx, argv, 3), h = js_arg_i(ctx, argv, 4);
    cv_go_persistent(n, c);
    if (w <= 0 || h <= 0 || w > 4096 || h > 4096) return JS_NewString(ctx, "");
    char *hex = malloc((size_t)w * h * 8 + 1);
    static const char *dig = "0123456789abcdef";
    size_t o = 0;
    for (int r = 0; r < h; r++) for (int col = 0; col < w; col++) {
        uint32_t p = 0;
        int sx = x + col, sy = y + r;
        if (sx >= 0 && sy >= 0 && sx < c->surf->w && sy < c->surf->h) p = c->surf->px[(size_t)sy * c->surf->w + sx];
        int ch[4] = { PR(p), PG(p), PB(p), PA(p) };
        for (int k = 0; k < 4; k++) { hex[o++] = dig[ch[k] >> 4]; hex[o++] = dig[ch[k] & 15]; }
    }
    hex[o] = 0;
    JSValue v = JS_NewStringLen(ctx, hex, o);
    free(hex);
    return v;
}

NATIVE(n_putimage) {
    CANVAS_ARG();
    int x = js_arg_i(ctx, argv, 1), y = js_arg_i(ctx, argv, 2), w = js_arg_i(ctx, argv, 3), h = js_arg_i(ctx, argv, 4);
    const char *hex = JS_ToCString(ctx, argv[5]);
    cv_go_persistent(n, c);
    if (hex && strlen(hex) == (size_t)w * h * 8) {
        for (int r = 0; r < h; r++) for (int col = 0; col < w; col++) {
            int sx = x + col, sy = y + r;
            if (sx < 0 || sy < 0 || sx >= c->surf->w || sy >= c->surf->h) continue;
            const char *p = hex + ((size_t)r * w + col) * 8;
            int v[4];
            for (int k = 0; k < 4; k++) v[k] = hex1(p[k * 2]) * 16 + hex1(p[k * 2 + 1]);
            uint32_t src = RGBA(v[0], v[1], v[2], v[3]), *dst = &c->surf->px[(size_t)sy * c->surf->w + sx];
            *dst = r_comp_px(src, *dst, COMP_OVER);
        }
        c->version++;
    }
    if (hex) JS_FreeCString(ctx, hex);
    return JS_UNDEFINED;
}

NATIVE(n_group_begin) {
    CANVAS_ARG();
    const char *key = JS_ToCString(ctx, argv[1]);
    int r = group_begin(n, c, key ? key : "");
    if (key) JS_FreeCString(ctx, key);
    return JS_NewInt32(ctx, r);
}
NATIVE(n_group_end) { CANVAS_ARG(); group_end(n, c); return JS_UNDEFINED; }

void canvas_register(Host *h, Natives *n, JSContext *ctx) {
    (void)h; (void)n;
    JSValue g = JS_GetGlobalObject(ctx);
#define REG(js, fn, argc) JS_SetPropertyStr(ctx, g, js, JS_NewCFunction(ctx, fn, js, argc))
    REG("__py_new_canvas", n_new_canvas, 3); REG("__py_resize", n_resize, 3); REG("__py_set", n_set, 3); REG("__py_call", n_call, 2);
    REG("__py_reset_state", n_reset_state, 1); REG("__py_fillRect", n_fillRect, 5); REG("__py_strokeRect", n_strokeRect, 5);
    REG("__py_clearRect", n_clearRect, 5); REG("__py_rect", n_rect, 5); REG("__py_moveTo", n_moveTo, 3); REG("__py_lineTo", n_lineTo, 3);
    REG("__py_translate", n_translate, 3); REG("__py_scale", n_scale, 3); REG("__py_beginPath", n_beginPath, 1);
    REG("__py_save", n_save, 1); REG("__py_restore", n_restore, 1); REG("__py_drawImage", n_drawImage, 3);
    REG("__py_measure", n_measure, 2); REG("__py_getimage", n_getimage, 5); REG("__py_putimage", n_putimage, 6);
    REG("__py_group_begin", n_group_begin, 2); REG("__py_group_end", n_group_end, 1);
    REG("__sgba_overlay_set", n_overlay_set, 5); REG("__sgba_overlay_modal", n_overlay_modal, 1); REG("__py_measure_h", n_measure_h, 1);
    n->ov_cid = -1;
#undef REG
    JS_FreeValue(ctx, g);
}

void canvas_page_loaded(Host *h, Natives *n) {
    int id = host_eval_int(h, "(() => { const c = document.querySelector('canvas'); return c ? c.__id : -1; })()", -1);
    Canvas *c = cv_get(n, id);
    if (!c) return;
    n->main_id = id;
    c->retained = 1; c->opaque = 1; c->persistent = 0; c->force_full = 1;
    surf_fill_all(c->surf, 0xFF000000u);
}

void canvas_shutdown(Natives *n) {
    for (int i = 0; i < n->canvas_cap; i++) cv_free(n->canvases[i]);
    free(n->canvases);
    n->canvases = NULL;
    text_shutdown();
}
