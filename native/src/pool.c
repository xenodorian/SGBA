#include <stdlib.h>
#include "pool.h"

#ifdef _WIN32
void pool_init(void) {}
int pool_threads(void) { return 1; }
void pool_run(void (*fn)(void *, int), void *ctx, int count) { for (int i = 0; i < count; i++) fn(ctx, i); }
void pool_shutdown(void) {}
#else
#include <pthread.h>
#include <unistd.h>

#define MAX_THREADS 4
static pthread_t g_thr[MAX_THREADS];
static int g_nworkers = 0, g_total = 1, g_quit = 0;
static pthread_mutex_t g_mu = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_wake = PTHREAD_COND_INITIALIZER, g_idle = PTHREAD_COND_INITIALIZER;
static void (*g_fn)(void *, int);
static void *g_ctx;
static int g_count, g_next, g_finished, g_gen;

static void work(void) {
    for (;;) {
        int i = __atomic_fetch_add(&g_next, 1, __ATOMIC_ACQ_REL);
        if (i >= g_count) return;
        g_fn(g_ctx, i);
        if (__atomic_add_fetch(&g_finished, 1, __ATOMIC_ACQ_REL) == g_count) {
            pthread_mutex_lock(&g_mu);
            pthread_cond_broadcast(&g_idle);
            pthread_mutex_unlock(&g_mu);
        }
    }
}

static void *worker(void *arg) {
    (void)arg;
    int seen = 0;
    pthread_mutex_lock(&g_mu);
    for (;;) {
        while (!g_quit && g_gen == seen) pthread_cond_wait(&g_wake, &g_mu);
        if (g_quit) break;
        seen = g_gen;
        pthread_mutex_unlock(&g_mu);
        work();
        pthread_mutex_lock(&g_mu);
    }
    pthread_mutex_unlock(&g_mu);
    return NULL;
}

void pool_init(void) {
    if (g_nworkers) return;
    int n = (int)sysconf(_SC_NPROCESSORS_ONLN);
    const char *e = getenv("SGBA_THREADS");
    if (e && atoi(e) > 0) n = atoi(e);
    if (n > MAX_THREADS) n = MAX_THREADS;
    if (n < 1) n = 1;
    g_total = n;
    for (int i = 1; i < n; i++) if (pthread_create(&g_thr[g_nworkers], NULL, worker, NULL) == 0) g_nworkers++;
    g_total = g_nworkers + 1;
}

int pool_threads(void) { return g_total; }

void pool_run(void (*fn)(void *, int), void *ctx, int count) {
    if (g_nworkers == 0 || count < 2) { for (int i = 0; i < count; i++) fn(ctx, i); return; }
    pthread_mutex_lock(&g_mu);
    g_fn = fn; g_ctx = ctx; g_count = count;
    __atomic_store_n(&g_next, 0, __ATOMIC_RELEASE);
    __atomic_store_n(&g_finished, 0, __ATOMIC_RELEASE);
    g_gen++;
    pthread_cond_broadcast(&g_wake);
    pthread_mutex_unlock(&g_mu);
    work();
    pthread_mutex_lock(&g_mu);
    while (__atomic_load_n(&g_finished, __ATOMIC_ACQUIRE) < count) pthread_cond_wait(&g_idle, &g_mu);
    pthread_mutex_unlock(&g_mu);
}

void pool_shutdown(void) {
    if (!g_nworkers) return;
    pthread_mutex_lock(&g_mu);
    g_quit = 1;
    pthread_cond_broadcast(&g_wake);
    pthread_mutex_unlock(&g_mu);
    for (int i = 0; i < g_nworkers; i++) pthread_join(g_thr[i], NULL);
    g_nworkers = 0;
}
#endif
