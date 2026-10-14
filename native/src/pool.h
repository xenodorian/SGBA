/* A small thread pool for splitting a repaint into strips. Jobs are indexed: fn(ctx, i) runs once for every i in [0, count),
 * on the workers and on the calling thread, which returns when all are done. SGBA_THREADS overrides the count (1 = no threads);
 * the default is the number of CPUs, at most 4. Windows builds run everything on the caller. */
#ifndef POOL_H
#define POOL_H
void pool_init(void);
int pool_threads(void);
void pool_run(void (*fn)(void *ctx, int i), void *ctx, int count);
void pool_shutdown(void);
#endif
