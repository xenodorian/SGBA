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
void host_note_error(Host *h);
int host_eval_int(Host *h, const char *code, int fallback);

/* natives registered into the JS context (natives_*.c) */
void natives_register(Host *h, JSContext *ctx);

/* ---- small JS helpers ---- */
static inline double js_arg_d(JSContext *ctx, JSValueConst *argv, int i) { double d = 0; JS_ToFloat64(ctx, &d, argv[i]); return d; }
static inline int js_arg_i(JSContext *ctx, JSValueConst *argv, int i) { int32_t v = 0; JS_ToInt32(ctx, &v, argv[i]); return v; }

#endif
