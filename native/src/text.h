#ifndef TEXT_H
#define TEXT_H
#include <stdint.h>
typedef struct { uint8_t *a; int w, h; } Mask;
/* Sizes follow pygame's default font: css px * 1.25 is the "size", the real point size is size * 0.6875. */
int text_size(const char *utf8, int font_px, int bold, int italic, int *height);      /* returns width */
int text_font_height(int font_px);                                                    /* pygame's get_height() (a rendered line is a little taller) */
const Mask *text_mask_peek(const char *utf8, int font_px, int bold, int italic);
const Mask *text_mask(const char *utf8, int font_px, int bold, int italic);           /* cached; do not free */
void text_shutdown(void);
#endif
