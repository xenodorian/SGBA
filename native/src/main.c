/* sgba: run a web game folder (index.html and its scripts) on the native runtime. */
#include <string.h>
#include <sys/stat.h>
#define STB_IMAGE_WRITE_IMPLEMENTATION
#define STBI_WRITE_NO_STDIO
#include "stb_image_write.h"
#include "sgba.h"
#include "canvas.h"
#include "natives.h"
#include "platform.h"
#include "input.h"
#include <stdlib.h>

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


/* ---------------------------------------------------------------- the interactive loop (window, pad, sound) */

static double now_ms(void) { return (double)sdl.GetPerformanceCounter() * 1000.0 / (double)sdl.GetPerformanceFrequency(); }

static int rss_mb(void) {
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return 0;
    char line[256];
    int mb = 0;
    while (fgets(line, sizeof line, f)) if (!strncmp(line, "VmRSS:", 6)) mb = atoi(line + 6) / 1024;
    fclose(f);
    return mb;
}

static Display *open_display_any(int fullscreen) {
    const char *explicit_driver = getenv("SDL_VIDEODRIVER");
    const char *tries[] = { NULL, "kmsdrm", "x11", "wayland", "fbcon", "directfb", NULL };
    if (explicit_driver) { Display *d = display_open(640, 480, fullscreen); return d; }
    for (int i = 0; i < 6; i++) {
        if (i > 0 && !tries[i]) break;
        if (tries[i]) { setenv("SDL_VIDEODRIVER", tries[i], 1); sdl.QuitSubSystem(SDL_INIT_VIDEO); }
        Display *d = display_open(640, 480, fullscreen);
        if (d) return d;
        sg_log("warn", "video driver %s failed", tries[i] ? tries[i] : "auto");
    }
    return NULL;
}

enum { END_QUIT = 0, END_PAUSE, END_CHOICE };

extern const unsigned char shell_html[];

/* Run one host (a game or the menu page) until the window closes, Start+Select is pressed in a game, or the menu page chooses. */
static int run_loop(Display *disp, Input *in, Host *h, int is_game, double max_seconds, double *started_at) {
    Natives *nat = host_natives(h);
    const Config *cfg = host_config(h);
    double step_ms = 1000.0 / (is_game ? cfg->fps : 60);
    IRect dirty[MAX_DIRTY];
    const Surf *surface = NULL;
    double last = now_ms(), acc = 0, t_update = 0, t_draw = 0, t_present = 0;
    long frames = 0, updates = 0, all_frames = 0;
    int keys_listened = 0, armed = 0;
    char pads[4096];
    int result = END_QUIT;
    if (*started_at == 0) *started_at = last;
    for (;;) {
        if (max_seconds > 0 && now_ms() - *started_at > max_seconds * 1000.0) return END_QUIT;
        SDL_Event e;
        int quit = 0;
        while (sdl.PollEvent(&e)) {
            if (e.type == SDL_QUIT) quit = 1;
            else if (e.type == SDL_KEYDOWN || e.type == SDL_KEYUP) {
                if (e.type == SDL_KEYDOWN && e.key.keysym.sym == SDLK_ESCAPE && !e.key.repeat) { quit = 1; continue; }
                if (is_game && (!e.key.repeat || e.type == SDL_KEYDOWN)) {
                    char key[32], code[32]; int kc;
                    input_key_names((int)e.key.keysym.sym, key, sizeof key, code, sizeof code, &kc);
                    host_key(h, e.type == SDL_KEYDOWN, key, code, kc);
                }
                input_event(in, &e);
            } else input_event(in, &e);
        }
        if (quit) return END_QUIT;
        int combo = input_down(in, A_START, 0) && input_down(in, A_SELECT, 0);
        if (!combo) armed = 1;
        else if (is_game && armed) { result = END_PAUSE; break; }
        if (!keys_listened) keys_listened = host_eval_int(h, "globalThis.__sgba_keys ? 1 : 0", 0);
        input_pad_json(in, is_game ? keys_listened : 0, pads, sizeof pads);
        host_set_pads(h, pads);
        double t = now_ms();
        double elapsed = t - last;
        last = t;
        if (elapsed > 250) elapsed = 250;
        acc += elapsed;
        if (acc > step_ms * 5) acc = step_ms * 5;
        int steps = 0;
        while (acc >= step_ms && steps < 5) {
            double u0 = now_ms();
            host_tick(h, step_ms / 1000.0);
            t_update += now_ms() - u0;
            acc -= step_ms; steps++; updates++;
            if (g_app->choice[0]) break;
        }
        if (steps == 5 && acc >= step_ms) acc = 0;
        if (steps) {
            double d0 = now_ms();
            int count = canvas_take_dirty(nat, dirty, MAX_DIRTY, &surface);
            double d1 = now_ms();
            if (count && surface) display_present(disp, surface, dirty, count, 0);
            double d2 = now_ms();
            t_draw += d1 - d0; t_present += d2 - d1;
            frames++; all_frames++;
            if (is_game && frames % 600 == 0) {
                sg_log("info", "perf: %ld frames, %.2f updates/frame, update %.1f ms each, draw %.1f ms, present %.1f ms, rss %d MB, js errors %d",
                       frames, (double)updates / 600.0, updates ? t_update / (double)updates : 0.0, t_draw / 600.0, t_present / 600.0, rss_mb(), host_error_count(h));
                updates = 0; t_update = t_draw = t_present = 0;
            }
        }
        if (g_app->choice[0] && !is_game) { result = END_CHOICE; break; }
        double spare = step_ms - (now_ms() - last);
        if (spare > 1.0) sdl.Delay((Uint32)(spare - 0.5));
        else sdl.Delay(0);
    }
    if (is_game) sg_log("info", "game left after %ld frames", all_frames);
    return result;
}

/* the menu page in one of its modes; returns what it chose */
static int run_menu(Display *disp, Input *in, const char *mode, double max_seconds, double *started_at) {
    g_app->shell_mode = mode;
    g_app->choice[0] = 0;
    Host *h = host_create(g_app->app_dir, NULL);
    host_load_html_text(h, (const char *)shell_html);
    int rc = run_loop(disp, in, h, 0, max_seconds, started_at);
    host_destroy(h);
    return rc;
}

static int run_interactive(const char *game, const char *save, const char *app_dir, int fullscreen, double max_seconds) {
    if (sdl_load(app_dir) != 0) return 1;
    Display *disp = open_display_any(fullscreen);
    if (!disp) return 1;
    Input *in = input_create();
    input_open_joysticks(in);
    char path[2300];
    snprintf(path, sizeof path, "%s/controls.json", app_dir);
    int have_controls = input_load_controls(in, path);
    struct stat st;
    if (!have_controls && stat(path, &st) == 0) have_controls = 1;           /* a file that says "keyboard, do not ask again" */
    static App app;
    app.in = in; app.app_dir = (char *)app_dir;
    g_app = &app;
    double started = 0;
    int exit_code = 0;
    if (!have_controls && input_joystick_count(in) > 0) {
        int rc = run_menu(disp, in, "wizard", max_seconds, &started);
        if (rc == END_QUIT) goto done;
    }
    for (;;) {
        char launch[2300];
        if (!game) {                                                             /* no game given: the launcher */
            int rc = run_menu(disp, in, "launcher", max_seconds, &started);
            if (rc == END_QUIT || strncmp(g_app->choice, "launch:", 7)) break;
            snprintf(launch, sizeof launch, "%s", g_app->choice + 7);
        } else snprintf(launch, sizeof launch, "%s", game);
        const char *save_file = save;
        char savebuf[2400];
        if (!save_file || !game) { const char *base = strrchr(launch, '/'); snprintf(savebuf, sizeof savebuf, "%s/saves/%s.json", app_dir, base ? base + 1 : launch); save_file = savebuf; }
        Host *h = host_create(launch, save_file);
        if (host_load_page(h, "index.html") != 0) { host_destroy(h); break; }
        g_app->choice[0] = 0;
        int over = 0;
        while (!over) {
            int rc = run_loop(disp, in, h, 1, max_seconds, &started);
            if (rc == END_QUIT) { over = 2; break; }
            int m = run_menu(disp, in, "pause", max_seconds, &started);                 /* Start+Select: the pause menu */
            if (m == END_QUIT) { over = 2; break; }
            if (!strcmp(g_app->choice, "quit")) { over = 1; break; }
            canvas_force_full(host_natives(h));                                         /* resume: repaint everything */
        }
        host_destroy(h);
        if (over == 2 || game) break;                                                   /* window closed, or a direct launch ended */
    }
done:
    input_free(in);
    display_close(disp);
    return exit_code;
}

int main(int argc, char **argv) {
    const char *game = NULL, *save = NULL, *script = "9999:a", *shot = NULL;
    int frames = 0, njs = 0, fullscreen = 0;
    double seconds = 0;
    double hz = 60.0;
    JsAt js[64];
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--frames") && i + 1 < argc) frames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--hz") && i + 1 < argc) hz = atof(argv[++i]);
        else if (!strcmp(argv[i], "--save") && i + 1 < argc) save = argv[++i];
        else if (!strcmp(argv[i], "--script") && i + 1 < argc) script = argv[++i];
        else if (!strcmp(argv[i], "--fullscreen")) fullscreen = 1;
        else if (!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atof(argv[++i]);
        else if (!strcmp(argv[i], "--shot") && i + 1 < argc) shot = argv[++i];
        else if (!strcmp(argv[i], "--js") && i + 1 < argc && njs < 64) {
            char *arg = argv[++i], *eq = strchr(arg, '=');
            if (eq) { js[njs].frame = atoi(arg); js[njs].code = eq + 1; njs++; }
        }
        else if (argv[i][0] != '-') game = argv[i];
    }
    if (!game) { fprintf(stderr, "usage: sgba GAME_FOLDER [--frames N] [--hz 60] [--save FILE] [--script S] [--js FRAME=CODE] [--shot out.png]\n"); return 2; }
    char *app = sg_exe_dir(argv[0]);
    char logpath[2048], savepath[2048];
    snprintf(logpath, sizeof logpath, "%s/sgba.log", app);
    sg_log_open(logpath);
    if (!frames) {
        if (!save) { const char *base = strrchr(game, '/'); snprintf(savepath, sizeof savepath, "%s/saves/%s.json", app, base ? base + 1 : game); save = savepath; }
        char dir[2100]; snprintf(dir, sizeof dir, "%s/saves", app);
#ifdef _WIN32
        mkdir(dir);
#else
        mkdir(dir, 0755);
#endif
        int rc = run_interactive(game, save, app, fullscreen, seconds);
        free(app);
        return rc;
    }
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
