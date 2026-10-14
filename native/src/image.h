#ifndef IMAGE_H
#define IMAGE_H
#include "natives.h"
#include "raster.h"
#ifndef _WIN32
#include <pthread.h>
#endif

typedef struct {
    char *path;            /* NULL for pinned data: images */
    int w, h;
    Surf *surf;            /* NULL until decoded */
    int kind;
    size_t bytes;
    int pinned;
    uint64_t used;
    int traced;            /* already in the first-use order list */
    int pf;                /* read-ahead state: 0 none, 1 queued, 2 decoding on the worker, 3 decoded and waiting (ready) */
    Surf *ready;
    int ready_kind;
} Image;

struct ImageStore {
    Image *e;
    int count, cap;
    size_t bytes, budget, peak;
    uint64_t tick;
    int alpha_keep;        /* sgba_ui.json "alpha": "keep" */
    int loads, evictions;
#ifndef _WIN32
    pthread_t thr; int thr_on, quit;       /* read-ahead worker: decodes images before the game first draws them */
    pthread_mutex_t mu; pthread_cond_t cv;
    int *queue; int qn, qcap;
    size_t ready_bytes;
#endif
    int *order; int norder, ocap, trace_loaded;   /* images in the order the game first used them (saved for the next run) */
    char *trace_path;
    int ra_hit, ra_wait, ra_miss;      /* read-ahead: ready when needed / had to wait for the worker / not started */
    double decode_ms, decode_max;   /* time spent reading and decoding images on first use (a frame that draws a new image pays it) */
};

struct ImageStore *imagestore_create(size_t budget_bytes, int alpha_keep);
int image_register_file(Natives *n, const char *path, int *w, int *h);     /* returns id or -1 */
const Surf *image_surface_peek(Natives *n, int id, int *kind);                  /* cached only, thread-safe read */
const Surf *image_surface(Natives *n, int id, int *kind);                 /* decode on demand */
int image_size(Natives *n, int id, int *w, int *h);
size_t image_bytes_total(Natives *n);                                      /* decoded images, including read-ahead ones not yet in use */
void image_stats(Natives *n, int *loads, double *ms, double *max_ms, int reset);   /* since the last reset */
int image_kind(Natives *n, int id);                                       /* -1 if not decoded yet */
#endif
