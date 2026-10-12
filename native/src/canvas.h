#ifndef CANVAS_H
#define CANVAS_H
#include "natives.h"
#include "raster.h"

#define MAX_DIRTY 8
/* After a tick: the rectangles of the main canvas that changed (composited now). Returns the count; 0 = nothing to show.
   *surface is the main canvas pixels (ARGB, opaque). */
int canvas_take_dirty(Natives *n, IRect *out, int max, const Surf **surface);
void canvas_force_full(Natives *n);
/* The overlay panel to draw over the game: returns 1 when visible. *changed is set when it appeared, vanished or was redrawn. */
int overlay_get(Natives *n, const Surf **panel, int *x, int *y, int *modal, int *changed, int *was_visible);
int canvas_main_size(Natives *n, int *w, int *h);
void canvas_stats(Natives *n, long *ops, long *replayed, long *skipped, long *frames);
#endif
