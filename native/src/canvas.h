#ifndef CANVAS_H
#define CANVAS_H
#include "natives.h"
#include "raster.h"

#define MAX_DIRTY 8
/* After a tick: the rectangles of the main canvas that changed (composited now). Returns the count; 0 = nothing to show.
   *surface is the main canvas pixels (ARGB, opaque). */
int canvas_take_dirty(Natives *n, IRect *out, int max, const Surf **surface);
void canvas_force_full(Natives *n);
int canvas_main_size(Natives *n, int *w, int *h);
void canvas_stats(Natives *n, long *ops, long *replayed, long *skipped, long *frames);
#endif
