#ifndef NATIVES_H
#define NATIVES_H
#include "sgba.h"
#include "raster.h"

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
    /* the HTML overlay panel (menus drawn by shell/overlay.js) */
    int ov_cid, ov_x, ov_y, ov_w, ov_h, ov_modal, ov_changed;
    /* the page called window.close(): leave the game as if Quit was chosen in the pause menu */
    int quit_req;
    /* images announced by the page, decoded one per tick */
    int *preload;
    int preload_count, preload_cap;
    /* canvases the page no longer references, freed a few frames later (a frame still being drawn may use them) */
    int *rel_id; long *rel_at; int nrel, caprel; long frame_no;
} Natives;

Natives *host_natives(Host *h);
const Surf *canvas_surface(Natives *n, int id);
void natives_init(Host *h, Natives *n, const char *save_path);
void natives_shutdown(Host *h, Natives *n);
void natives_page_loaded(Host *h, Natives *n);
void images_preload_patterns(Natives *n, const char *root, char **patterns, int np);
void natives_begin_frame(Host *h, Natives *n);
void natives_end_tick(Host *h, Natives *n);

#endif
