#ifndef IMAGE_H
#define IMAGE_H
#include "natives.h"
#include "raster.h"

typedef struct {
    char *path;            /* NULL for pinned data: images */
    int w, h;
    Surf *surf;            /* NULL until decoded */
    int kind;
    size_t bytes;
    int pinned;
    uint64_t used;
} Image;

struct ImageStore {
    Image *e;
    int count, cap;
    size_t bytes, budget, peak;
    uint64_t tick;
    int alpha_keep;        /* sgba_ui.json "alpha": "keep" */
    int loads, evictions;
};

struct ImageStore *imagestore_create(size_t budget_bytes, int alpha_keep);
int image_register_file(Natives *n, const char *path, int *w, int *h);     /* returns id or -1 */
const Surf *image_surface(Natives *n, int id, int *kind);                 /* decode on demand */
int image_size(Natives *n, int id, int *w, int *h);
int image_kind(Natives *n, int id);                                       /* -1 if not decoded yet */
#endif
