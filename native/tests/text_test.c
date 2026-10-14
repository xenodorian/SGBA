/* The text cache is shared by every host, and each host calls text_shutdown() when destroyed (game, pause menu, launcher).
 * Shutting down twice must be safe. Build: see .github/workflows/native.yml (run under AddressSanitizer). */
#include <stdio.h>
#include "text.h"

void text_shutdown(void);

int main(void) {
    const Mask *m = text_mask("Hello", 20, 0, 0);
    if (!m || m->w <= 0) { fprintf(stderr, "no mask\n"); return 1; }
    text_mask("Paused", 36, 1, 0);
    text_shutdown();
    text_shutdown();                       /* a second host going away: used to free the pixel buffers again */
    m = text_mask("Resume", 20, 0, 0);
    if (!m || m->w <= 0) { fprintf(stderr, "no mask after shutdown\n"); return 1; }
    text_shutdown();
    text_shutdown();
    puts("text cache shutdown ok");
    return 0;
}
