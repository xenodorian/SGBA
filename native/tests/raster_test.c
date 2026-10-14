/* Differential test: the SIMD span kernels against their scalar references on random data.
 *   gcc -O2 -I src tests/raster_test.c src/raster.c -lm -o raster_test && ./raster_test
 * (also built for aarch64 and run under qemu to check the NEON path) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "raster.h"

static uint32_t rng = 12345;
static uint32_t rnd32(void) { rng = rng * 1664525u + 1013904223u; return rng >> 8 ^ (rng << 16); }
static uint32_t rpix(int opaque_bias) {
    uint32_t r = rnd32() ^ (rnd32() << 7), m = rnd32() % 10;
    uint32_t a = m < 3 ? 0 : m < 6 ? 255 : (rnd32() & 255);
    if (opaque_bias) a = m < 9 ? 255 : (rnd32() & 255);
    return (r & 0x00FFFFFFu) | (a << 24);
}
static int maxdiff(uint32_t a, uint32_t b) {
    int m = 0;
    for (int s = 0; s < 32; s += 8) { int d = (int)((a >> s) & 255) - (int)((b >> s) & 255); if (d < 0) d = -d; if (d > m) m = d; }
    return m;
}

int main(void) {
    enum { MAXN = 70 };
    uint32_t src[MAXN], d1[MAXN], d2[MAXN];
    long bad = 0, tests = 0;
    static const int gas[] = { 255, 254, 200, 128, 77, 1 };
    for (int iter = 0; iter < 40000; iter++) {
        int n = (int)(rnd32() % MAXN);
        int dstbias = (iter & 3) != 0;                      /* mostly opaque destinations, sometimes not */
        for (int i = 0; i < MAXN; i++) { src[i] = rpix(0); d1[i] = d2[i] = rpix(dstbias); }
        for (int mode = 0; mode < 3; mode++)
            for (unsigned g = 0; g < sizeof gas / sizeof gas[0]; g++) {
                memcpy(d1, d2, sizeof d1);
                uint32_t a[MAXN], b[MAXN];
                memcpy(a, d1, sizeof a); memcpy(b, d1, sizeof b);
                r_span_over(a, src, n, gas[g], mode); r_span_over_ref(b, src, n, gas[g], mode);
                tests++;
                if (memcmp(a, b, sizeof a)) { bad++; if (bad < 5) printf("MISMATCH over n=%d mode=%d ga=%d\n", n, mode, gas[g]); }
            }
        uint32_t c = rpix(0);
        { uint32_t a[MAXN], b[MAXN]; memcpy(a, d2, sizeof a); memcpy(b, d2, sizeof b);
          r_span_fill_over(a, n, c); r_span_fill_over_ref(b, n, c); tests++;
          if (memcmp(a, b, sizeof a)) { bad++; if (bad < 5) printf("MISMATCH fill_over n=%d c=%08x\n", n, c); } }
        for (int comp = COMP_MULTIPLY; comp <= COMP_SRC_ATOP; comp++) {
            if (comp == COMP_SRC_IN) continue;
            uint32_t a[MAXN], b[MAXN]; memcpy(a, d2, sizeof a); memcpy(b, d2, sizeof b);
            r_span_fill_comp(a, n, c, comp); r_span_fill_comp_ref(b, n, c, comp); tests++;
            for (int i = 0; i < n; i++) if (maxdiff(a[i], b[i]) > 2 || (a[i] >> 24) != (b[i] >> 24)) { bad++; if (bad < 5) printf("MISMATCH comp=%d i=%d %08x vs %08x\n", comp, i, a[i], b[i]); break; }
        }
        { uint32_t a[MAXN], b[MAXN]; memcpy(a, d2, sizeof a); memcpy(b, d2, sizeof b);
          r_span_comp(a, src, n, COMP_MULTIPLY); r_span_comp_ref(b, src, n, COMP_MULTIPLY); tests++;
          for (int i = 0; i < n; i++) if (maxdiff(a[i], b[i]) > 2 || (a[i] >> 24) != (b[i] >> 24)) { bad++; if (bad < 5) printf("MISMATCH span_comp i=%d %08x vs %08x\n", i, a[i], b[i]); break; } }
    }
    printf("%ld kernel comparisons, %ld mismatches\n", tests, bad);
    return bad ? 1 : 0;
}
