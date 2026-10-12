/* sgba: run a web game folder (index.html and its scripts) on the native runtime. */
#include <string.h>
#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STBI_WRITE_NO_STDIO
#include "stb_image_write.h"
#include "sgba.h"
#include "canvas.h"
#include "natives.h"

typedef struct { int frame; char action[16]; int down; } PadEvent;
typedef struct { int frame; char *code; } JsAt;

static const char *PAD_ORDER[16] = { "a", "b", "x", "y", "l1", "r1", "l2", "r2", "select", "start", "l3", "r3", "up", "down", "left", "right" };

static int pad_index(const char *name) { for (int i = 0; i < 16; i++) if (!strcmp(PAD_ORDER[i], name)) return i; return -1; }

/* "170:hold:right:300,200:tap:a:25,30:a" (the same script language as tools/js_bench.py) */
static int parse_script(const char *text, int frames, PadEvent **out) {
    int n = 0, cap = 64;
    PadEvent *ev = malloc(cap * sizeof *ev);
    char *copy = strdup(text), *save = NULL;
    for (char *item = strtok_r(copy, ",", &save); item; item = strtok_r(NULL, ",", &save)) {
        char *parts[4] = { 0 };
        int np = 0;
        char *s2 = NULL;
        for (char *t = strtok_r(item, ":", &s2); t && np < 4; t = strtok_r(NULL, ":", &s2)) parts[np++] = t;
        if (np < 2) continue;
        int frame = atoi(parts[0]);
#define ADD(f, a, d) do { if (n >= cap - 2) { cap *= 2; ev = realloc(ev, cap * sizeof *ev); } ev[n].frame = (f); snprintf(ev[n].action, sizeof ev[n].action, "%s", (a)); ev[n].down = (d); n++; } while (0)
        if (np == 2) { ADD(frame, parts[1], 1); ADD(frame + 3, parts[1], 0); }
        else if (!strcmp(parts[1], "hold") && np >= 4) { ADD(frame, parts[2], 1); ADD(frame + atoi(parts[3]), parts[2], 0); }
        else if (!strcmp(parts[1], "tap") && np >= 4) { int step = atoi(parts[3]); if (step < 1) step = 1; for (int f = frame; f < frames; f += step) { ADD(f, parts[2], 1); ADD(f + 3, parts[2], 0); } }
    }
    free(copy);
    *out = ev;
    return n;
}

static void pad_json(unsigned mask, char *buf, size_t size) {
    size_t o = (size_t)snprintf(buf, size, "[{\"id\":\"SGBA standard gamepad\",\"index\":0,\"connected\":true,\"mapping\":\"standard\",\"timestamp\":0,\"buttons\":[");
    for (int i = 0; i < 16; i++) {
        int v = (mask >> i) & 1;
        o += (size_t)snprintf(buf + o, size - o, "%s{\"pressed\":%s,\"touched\":%s,\"value\":%d}", i ? "," : "", v ? "true" : "false", v ? "true" : "false", v);
    }
    snprintf(buf + o, size - o, "],\"axes\":[0,0,0,0]}]");
}

static void write_png(const char *path, const Surf *s) {
    unsigned char *rgb = malloc((size_t)s->w * s->h * 3);
    for (int i = 0; i < s->w * s->h; i++) { uint32_t p = s->px[i]; rgb[i * 3] = (unsigned char)PR(p); rgb[i * 3 + 1] = (unsigned char)PG(p); rgb[i * 3 + 2] = (unsigned char)PB(p); }
    int len;
    unsigned char *png = stbi_write_png_to_mem(rgb, s->w * 3, s->w, s->h, 3, &len);
    FILE *f = fopen(path, "wb");
    if (f && png) { fwrite(png, 1, (size_t)len, f); }
    if (f) fclose(f);
    free(png); free(rgb);
}

int main(int argc, char **argv) {
    const char *game = NULL, *save = NULL, *script = "9999:a", *shot = NULL;
    int frames = 0, njs = 0;
    double hz = 60.0;
    JsAt js[64];
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--frames") && i + 1 < argc) frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--hz") && i + 1 < argc) hz = atof(argv[++i]);
        else if (!strcmp(argv[i], "--save") && i + 1 < argc) save = argv[++i];
        else if (!strcmp(argv[i], "--script") && i + 1 < argc) script = argv[++i];
        else if (!strcmp(argv[i], "--shot") && i + 1 < argc) shot = argv[++i];
        else if (!strcmp(argv[i], "--js") && i + 1 < argc && njs < 64) {
            char *arg = argv[++i], *eq = strchr(arg, '=');
            if (eq) { js[njs].frame = atoi(arg); js[njs].code = eq + 1; njs++; }
        }
        else if (argv[i][0] != '-') game = argv[i];
    }
    if (!game) { fprintf(stderr, "usage: sgba GAME_FOLDER [--frames N] [--hz 60] [--save FILE] [--script S] [--js FRAME=CODE] [--shot out.png]\n"); return 2; }
    Host *h = host_create(game, save);
    if (host_load_page(h, "index.html") != 0) return 1;
    PadEvent *events;
    int nev = parse_script(script, frames, &events);
    unsigned mask = 0;
    IRect dirty[MAX_DIRTY];
    const Surf *surface = NULL;
    for (int f = 0; f < frames; f++) {
        for (int k = 0; k < njs; k++) if (js[k].frame == f) host_eval(h, js[k].code, "--js");
        int changed = 0;
        for (int k = 0; k < nev; k++) if (events[k].frame == f) { int bit = pad_index(events[k].action); if (bit >= 0) { if (events[k].down) mask |= 1u << bit; else mask &= ~(1u << bit); changed = 1; } }
        if (changed || f == 0) { char buf[4096]; pad_json(mask, buf, sizeof buf); host_set_pads(h, buf); }
        host_tick(h, 1.0 / hz);
        canvas_take_dirty(host_natives(h), dirty, MAX_DIRTY, &surface);
    }
    if (shot && surface) write_png(shot, surface);
    long ops, replayed, skipped, fr;
    canvas_stats(host_natives(h), &ops, &replayed, &skipped, &fr);
    fprintf(stderr, "frames %d, js errors %d, ops/frame %ld, replayed/frame %ld, skipped %ld\n", frames, host_error_count(h), ops / (fr ? fr : 1), replayed / (fr ? fr : 1), skipped);
    int errors = host_error_count(h);
    free(events);
    host_destroy(h);
    return errors ? 1 : 0;
}
