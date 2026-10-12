/* SGBA native runtime: shared declarations. */
#ifndef SGBA_H
#define SGBA_H

#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "quickjs.h"

#define LOGICAL_W_DEFAULT 640
#define LOGICAL_H_DEFAULT 480
#define IMAGE_ID_BASE 1000000

/* ---- log ---- */
void sg_log(const char *level, const char *fmt, ...);
void sg_log_open(const char *path);

/* ---- settings from the game's sgba_ui.json ---- */
typedef struct {
    int fps;                 /* simulation steps per second (60 or 30) */
    int alpha_keep;          /* "alpha": "keep" */
    int image_budget_mb;
    char **preload;          /* glob patterns */
    int npreload;
    char *title;
    int engine_nav;          /* "navigation": "engine" (default) draws and drives the page's menus; "game" only draws them */
    char **hide;             /* selectors or text:Label of on-screen buttons to hide on a device */
    int nhide;
    char **hot_action, **hot_id;     /* pad action -> element id pressed when no menu is open */
    int nhot;
} Config;
void config_load(const char *root, Config *c);
void config_free(Config *c);
int sg_glob(const char *pattern, const char *text);
char *sg_exe_dir(const char *argv0);            /* malloc'd directory holding the executable */

/* ---- files ---- */
char *sg_resolve(const char *root, const char *url);          /* malloc'd absolute path inside root, or NULL */
unsigned char *sg_read_file(const char *path, size_t *size);  /* malloc'd, NUL-terminated; NULL on failure */

/* ---- host (host.c) ---- */
typedef struct Host Host;
Host *host_create(const char *root, const char *save_path);
void host_destroy(Host *h);
int host_load_page(Host *h, const char *index);
void host_tick(Host *h, double dt_seconds);
void host_key(Host *h, int is_down, const char *key, const char *code, int key_code);
void host_set_pads(Host *h, const char *json);
void host_eval(Host *h, const char *code, const char *name);
int host_error_count(Host *h);
double host_clock_ms(Host *h);
JSContext *host_ctx(Host *h);
const char *host_root(Host *h);
const Config *host_config(Host *h);
void host_note_error(Host *h);
int host_eval_int(Host *h, const char *code, int fallback);

/* ---- application state shared with the menu natives (shell.c) ---- */
typedef struct Input Input;
typedef struct {
    Input *in;
    char *app_dir;
    const char *shell_mode;            /* "launcher", "pause" or "wizard": what the menu page should show */
    char choice[2200];                 /* what the menu page asked for: "resume", "quit", "done", "launch:PATH" */
} App;
extern App *g_app;
void shell_register(JSContext *ctx);
int host_load_html_text(Host *h, const char *html);
void host_enable_overlay(Host *h);                /* HTML menus over the canvas */
int host_overlay_tick_exists(Host *h);
void host_overlay_nav(Host *h, const char *what);
void host_overlay_hotkey(Host *h, const char *id);

/* natives registered into the JS context (natives_*.c) */
void natives_register(Host *h, JSContext *ctx);

/* ---- small JS helpers ---- */
static inline double js_arg_d(JSContext *ctx, JSValueConst *argv, int i) { double d = 0; JS_ToFloat64(ctx, &d, argv[i]); return d; }
static inline int js_arg_i(JSContext *ctx, JSValueConst *argv, int i) { int32_t v = 0; JS_ToInt32(ctx, &v, argv[i]); return v; }

#endif
