#ifndef NATIVES_H
#define NATIVES_H
#include "sgba.h"

typedef struct Canvas Canvas;
typedef struct ImageStore ImageStore;
typedef struct Audio Audio;

typedef struct {
    char *key;
    char *value;
} KV;

typedef struct Natives {
    Host *host;
    /* canvases */
    Canvas **canvases;
    int canvas_cap;
    int main_id;
    ImageStore *images;
    Audio *audio;
    /* localStorage */
    char *save_path;
    KV *kv;
    int kv_count, kv_cap;
    int storage_dirty;
    /* images announced by the page, decoded one per tick */
    int *preload;
    int preload_count, preload_cap;
} Natives;

Natives *host_natives(Host *h);
void natives_init(Host *h, Natives *n, const char *save_path);
void natives_shutdown(Host *h, Natives *n);
void natives_page_loaded(Host *h, Natives *n);
void natives_begin_frame(Host *h, Natives *n);
void natives_end_tick(Host *h, Natives *n);

#endif
