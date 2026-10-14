/* Diagnostics: see report.h. Files, all under <app>/reports/:
 *   sessions.log        one line per game session (frames, fps, late frames, memory); capped
 *   crash-<time>.txt    written from the signal handler on a crash (segfault, abort, ...) with a backtrace and the log tail
 *   unclean-<time>.txt  the previous run left no clean exit (killed, power loss, hang, loader failure)
 *   errors-<time>.txt   a game session that logged errors
 *   .running            marker, rewritten every ~10 s while running, removed on a clean exit */
#include <ctype.h>
#include <stdarg.h>
#include <time.h>
#include <sys/stat.h>
#include "sgba.h"
#include "report.h"
#ifdef _WIN32
#include <direct.h>
#define mkdir_(p) _mkdir(p)
#else
#include <dirent.h>
#include <dlfcn.h>
#include <execinfo.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/utsname.h>
#include <unistd.h>
#define mkdir_(p) mkdir((p), 0755)
#endif

#ifndef SGBA_BUILD
#define SGBA_BUILD "dev"
#endif
#define RING 48
#define BOOT 20
#define KEEP_REPORTS 20
#define SESSIONS_MAX 49152

static char g_appdir[2100], g_dir[2200], g_marker[2300], g_sessions[2300];
static char g_ring[RING][200];
static unsigned g_ring_total;
static char g_boot[BOOT][200];
static int g_nboot, g_booting = 1;
static int g_errors_seen, g_errors_session;
static char g_hdr[1400];                  /* prebuilt, so the crash handler only has to write() it */
#ifndef _WIN32
static char g_base[40] = "?";
static unsigned long g_base_ul;
#endif

static struct {
    int on;
    char game[160];
    time_t t0;
    double step_ms, sum, max;
    long loops;
    long band[5];                          /* loop interval vs the step: on time (<1.25x), late (<2x), slow (<4x), stalled (>=4x) ; band[4] unused */
    int rss_peak, js_errors;
    double fps10;
} S;

static void ts(char *o, size_t n, time_t t, int file) {
    struct tm tmv;
#ifdef _WIN32
    localtime_s(&tmv, &t);
#else
    localtime_r(&t, &tmv);
#endif
    strftime(o, n, file ? "%Y%m%d-%H%M%S" : "%Y-%m-%d %H:%M:%S", &tmv);
}

void report_log_line(const char *level, const char *msg) {
    char line[200];
    time_t t = time(NULL);
    char when[32];
    ts(when, sizeof when, t, 0);
    snprintf(line, sizeof line, "%s %s %s", when + 11, level, msg);
    for (char *p = line; *p; p++) if (*p == '\n' || *p == '\r') *p = ' ';
    if (g_booting && g_nboot < BOOT) memcpy(g_boot[g_nboot++], line, sizeof g_boot[0]);
    memcpy(g_ring[g_ring_total % RING], line, sizeof line);
    g_ring_total++;
    if (!strcmp(level, "error")) { g_errors_seen++; g_errors_session++; }
}

/* ---- small system probes, all optional ---- */
#ifndef _WIN32
static int read_line(const char *path, char *out, size_t n) {
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    int ok = fgets(out, (int)n, f) != NULL;
    fclose(f);
    if (ok) out[strcspn(out, "\r\n")] = 0;
    return ok;
}

#endif

static void probes(FILE *f) {
#ifndef _WIN32
    struct utsname u;
    if (uname(&u) == 0) fprintf(f, "system: %s %s %s\n", u.sysname, u.release, u.machine);
    fprintf(f, "cpus: %ld\n", sysconf(_SC_NPROCESSORS_ONLN));
    char b[200];
    FILE *m = fopen("/proc/meminfo", "r");
    if (m) {
        long total = 0, avail = 0;
        while (fgets(b, sizeof b, m)) { sscanf(b, "MemTotal: %ld", &total); sscanf(b, "MemAvailable: %ld", &avail); }
        fclose(m);
        fprintf(f, "memory: %ld MB total, %ld MB available\n", total / 1024, avail / 1024);
    }
    if (read_line("/proc/loadavg", b, sizeof b)) fprintf(f, "load: %s\n", b);
    if (read_line("/sys/class/thermal/thermal_zone0/temp", b, sizeof b)) fprintf(f, "temperature: %.1f C\n", atof(b) / 1000.0);
    if (read_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", b, sizeof b)) fprintf(f, "cpu0 freq: %ld MHz\n", atol(b) / 1000);
    if (read_line("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", b, sizeof b)) fprintf(f, "governor: %s\n", b);
    static const char *bat[] = { "/sys/class/power_supply/battery/capacity", "/sys/class/power_supply/BAT0/capacity", NULL };
    for (int i = 0; bat[i]; i++) if (read_line(bat[i], b, sizeof b)) { fprintf(f, "battery: %s%%\n", b); break; }
#else
    fprintf(f, "system: Windows\n");
#endif
}

static void tail_of(FILE *out, const char *path, int lines, const char *title) {
    FILE *f = fopen(path, "rb");
    if (!f) return;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f), from = sz > 24000 ? sz - 24000 : 0;
    fseek(f, from, SEEK_SET);
    char *buf = malloc((size_t)(sz - from) + 1);
    if (!buf) { fclose(f); return; }
    size_t n = fread(buf, 1, (size_t)(sz - from), f);
    fclose(f);
    buf[n] = 0;
    char *start = buf + n;
    int seen = 0;
    while (start > buf && seen <= lines) { start--; if (*start == '\n' && start != buf + n - 1) seen++; }
    if (*start == '\n') start++;
    fprintf(out, "\n--- %s (last %d lines) ---\n", title, lines);
    for (char *p = start; *p; p++) fputc((unsigned char)*p >= 127 || ((unsigned char)*p < 32 && *p != '\n' && *p != '\t') ? '?' : *p, out);
    free(buf);
}

static void write_header(FILE *f, const char *kind) {
    char when[32];
    ts(when, sizeof when, time(NULL), 0);
    fprintf(f, "SGBA report: %s\nbuild: %s\ntime: %s\n", kind, SGBA_BUILD, when);
    if (S.on || S.game[0]) fprintf(f, "game: %s\n", S.game);
    probes(f);
}

static void write_ring(FILE *f) {
    fprintf(f, "\n--- start-up log ---\n");
    for (int i = 0; i < g_nboot; i++) fprintf(f, "%s\n", g_boot[i]);
    fprintf(f, "\n--- recent log ---\n");
    unsigned first = g_ring_total > RING ? g_ring_total - RING : 0;
    for (unsigned i = first; i < g_ring_total; i++) fprintf(f, "%s\n", g_ring[i % RING]);
}

static void stats_text(char *o, size_t n) {
    double secs = S.on ? difftime(time(NULL), S.t0) : 0;
    long total = S.loops ? S.loops : 1;
    snprintf(o, n, "secs=%.0f loops=%ld avg_ms=%.1f max_ms=%.0f ontime=%.1f%% late=%ld slow=%ld stalled=%ld rss=%dMB jserr=%d",
             secs, S.loops, S.loops ? S.sum / (double)S.loops : 0.0, S.max, 100.0 * (double)S.band[0] / (double)total,
             S.band[1], S.band[2], S.band[3], S.rss_peak, S.js_errors);
}

/* ---- files ---- */
#ifndef _WIN32
static int cmp_name_desc(const void *a, const void *b) { return strcmp((const char *)b, (const char *)a); }
#endif

static void prune(void) {
#ifndef _WIN32
    DIR *d = opendir(g_dir);
    if (!d) return;
    static char names[64][64];
    int n = 0;
    struct dirent *e;
    while ((e = readdir(d)) && n < 64) {
        size_t l = strlen(e->d_name);
        if (l > 4 && l < 63 && !strcmp(e->d_name + l - 4, ".txt")) { memcpy(names[n], e->d_name, l + 1); n++; }
    }
    closedir(d);
    qsort(names, (size_t)n, sizeof names[0], cmp_name_desc);                  /* names embed the time, so newest first */
    for (int i = KEEP_REPORTS; i < n; i++) { char p[2400]; snprintf(p, sizeof p, "%s/%s", g_dir, names[i]); remove(p); }
#endif
}

static void write_report(const char *kind, const char *note, int with_logfile) {
    char name[64], path[2400], when[32];
    ts(when, sizeof when, time(NULL), 1);
    snprintf(name, sizeof name, "%s-%s.txt", kind, when);
    snprintf(path, sizeof path, "%s/%s", g_dir, name);
    FILE *f = fopen(path, "w");
    if (!f) return;
    write_header(f, kind);
    if (note && note[0]) fprintf(f, "%s\n", note);
    char st[300];
    stats_text(st, sizeof st);
    if (S.on) fprintf(f, "session: %s\n", st);
    write_ring(f);
    if (with_logfile) {
        char lp[2400];
        snprintf(lp, sizeof lp, "%s/sgba.log", g_appdir);
        tail_of(f, lp, 40, "sgba.log");
    }
    fclose(f);
    sg_log("info", "wrote report %s", name);
    prune();
}

static void marker_write(void) {
    FILE *f = fopen(g_marker, "w");
    if (!f) return;
    char st[300];
    stats_text(st, sizeof st);
    fprintf(f, "build=%s\ngame=%s\nstate=%s\n%s\nfps=%.1f\n", SGBA_BUILD, S.game, S.on ? "playing" : "menu", st, S.fps10);
    fclose(f);
}

static void sessions_append(const char *line) {
    struct stat sb;
    if (stat(g_sessions, &sb) == 0 && sb.st_size > SESSIONS_MAX) {                 /* keep the newest half */
        FILE *f = fopen(g_sessions, "rb");
        if (f) {
            long keep = SESSIONS_MAX / 2;
            char *buf = malloc((size_t)keep + 1);
            if (buf) {
                fseek(f, -keep, SEEK_END);
                size_t n = fread(buf, 1, (size_t)keep, f);
                buf[n] = 0;
                char *nl = strchr(buf, '\n');
                fclose(f);
                f = fopen(g_sessions, "wb");
                if (f && nl) { fputs(nl + 1, f); fclose(f); f = NULL; }
                free(buf);
            }
            if (f) fclose(f);
        }
    }
    FILE *f = fopen(g_sessions, "a");
    if (f) { fputs(line, f); fputc('\n', f); fclose(f); }
}

#ifndef _WIN32
/* ---- crash handler: only async-signal-safe calls (open, write, backtrace, unlink) ---- */
static char g_crash_prefix[2300];
static void w(int fd, const char *s) { size_t n = 0; while (s[n]) n++; if (n && write(fd, s, n) < 0) {} }
static void wnum(int fd, unsigned long v, int base) {
    char b[24]; int i = 24; b[--i] = 0;
    if (!v) b[--i] = '0';
    while (v && i > 0) { b[--i] = "0123456789abcdef"[v % (unsigned)base]; v /= (unsigned)base; }
    w(fd, b + i);
}

static void on_crash(int sig, siginfo_t *si, void *uc) {
    static volatile sig_atomic_t busy;
    if (busy) _exit(128 + sig);
    busy = 1;
    char path[2400];
    size_t n = 0;
    while (g_crash_prefix[n] && n < sizeof path - 24) { path[n] = g_crash_prefix[n]; n++; }
    char num[24]; int i = 24; num[--i] = 0;
    unsigned long t = (unsigned long)time(NULL);
    if (!t) num[--i] = '0';
    while (t && i > 0) { num[--i] = (char)('0' + t % 10); t /= 10; }
    for (const char *p = num + i; *p; p++) path[n++] = *p;
    path[n++] = '.'; path[n++] = 't'; path[n++] = 'x'; path[n++] = 't'; path[n] = 0;
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        w(fd, "SGBA report: crash\n"); w(fd, g_hdr);
        w(fd, "signal: "); wnum(fd, (unsigned long)sig, 10);
        w(fd, sig == SIGSEGV ? " (segmentation fault)" : sig == SIGBUS ? " (bus error)" : sig == SIGILL ? " (illegal instruction)" : sig == SIGFPE ? " (arithmetic error)" : sig == SIGABRT ? " (abort)" : "");
        w(fd, "\nfault address: 0x"); wnum(fd, (unsigned long)(si ? si->si_addr : 0), 16);
        w(fd, "\nmodule base: 0x"); w(fd, g_base);
        w(fd, "\n\n--- backtrace (addresses; sgba+offset is the position inside this build) ---\n");
        void *bt[40];
        int cnt = backtrace(bt, 40);
        for (int k = 0; k < cnt; k++) { unsigned long a = (unsigned long)bt[k]; wnum(fd, (unsigned long)k, 10); w(fd, ": 0x"); wnum(fd, a, 16); if (a >= g_base_ul && a - g_base_ul < 0x4000000) { w(fd, "  sgba+0x"); wnum(fd, a - g_base_ul, 16); } w(fd, "\n"); }
        w(fd, "\n--- recent log ---\n");
        unsigned first = g_ring_total > RING ? g_ring_total - RING : 0;
        for (unsigned k = first; k < g_ring_total; k++) { w(fd, g_ring[k % RING]); w(fd, "\n"); }
        close(fd);
    }
    unlink(g_marker);
    (void)uc;
    signal(sig, SIG_DFL);
    raise(sig);
}

static void install_handlers(void) {
    static char altstack[65536];
    stack_t ss = { .ss_sp = altstack, .ss_size = sizeof altstack, .ss_flags = 0 };
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_crash;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_RESETHAND;
    sigemptyset(&sa.sa_mask);
    int sigs[] = { SIGSEGV, SIGBUS, SIGILL, SIGFPE, SIGABRT };
    for (unsigned k = 0; k < sizeof sigs / sizeof *sigs; k++) sigaction(sigs[k], &sa, NULL);
    void *warm[2];
    backtrace(warm, 2);                                    /* loads the unwinder now, not inside the handler */
    Dl_info info;
    if (dladdr((void *)on_crash, &info) && info.dli_fbase) { g_base_ul = (unsigned long)info.dli_fbase; snprintf(g_base, sizeof g_base, "%lx", g_base_ul); }
}
#endif

static void build_header(void) {
    char when[32];
    ts(when, sizeof when, time(NULL), 0);
    char st[300];
    stats_text(st, sizeof st);
    snprintf(g_hdr, sizeof g_hdr, "build: %s\ngame: %s\nlast status: %s\n(state at last update, %s)\n", SGBA_BUILD, S.game, st, when);
}

void report_init(const char *app_dir) {
    snprintf(g_appdir, sizeof g_appdir, "%s", app_dir);
    snprintf(g_dir, sizeof g_dir, "%s/reports", app_dir);
    mkdir_(g_dir);
    snprintf(g_marker, sizeof g_marker, "%s/.running", g_dir);
    snprintf(g_sessions, sizeof g_sessions, "%s/sessions.log", g_dir);
    char sentdir[2300];
    snprintf(sentdir, sizeof sentdir, "%s/sent", g_dir);
    mkdir_(sentdir);
    FILE *m = fopen(g_marker, "r");                                 /* the last run did not finish cleanly */
    if (m) {
        char note[600] = "The previous run did not exit cleanly (killed, power loss, hang, or it stopped before the crash handler ran).\nlast marker:\n";
        size_t l = strlen(note);
        size_t got = fread(note + l, 1, sizeof note - l - 1, m);
        note[l + got] = 0;
        fclose(m);
        write_report("unclean", note, 1);
    }
#ifndef _WIN32
    snprintf(g_crash_prefix, sizeof g_crash_prefix, "%s/crash-", g_dir);
    install_handlers();
#endif
    if (getenv("SGBA_TEST_CRASH_AT_START")) { volatile int *p = NULL; *p = 1; }      /* test hook */
    marker_write();
    build_header();
    sg_log("info", "SGBA build %s, reports in %s", SGBA_BUILD, g_dir);
}

void report_session_begin(const char *game, double step_ms) {
    const char *base = strrchr(game, '/');
    memset(&S, 0, sizeof S);
    S.on = 1;
    snprintf(S.game, sizeof S.game, "%s", base ? base + 1 : game);
    S.t0 = time(NULL);
    S.step_ms = step_ms > 1 ? step_ms : 16.7;
    g_booting = 0;
    g_errors_session = 0;
    marker_write();
    build_header();
}

void report_frame(double loop_ms) {
    if (!S.on) return;
    S.loops++;
    S.sum += loop_ms;
    if (loop_ms > S.max) S.max = loop_ms;
    double r = loop_ms / S.step_ms;
    S.band[r < 1.25 ? 0 : r < 2.0 ? 1 : r < 4.0 ? 2 : 3]++;
}

void report_tick(int rss, int js_errors) {
    if (!S.on) return;
    if (rss > S.rss_peak) S.rss_peak = rss;
    S.js_errors = js_errors;
    double secs = difftime(time(NULL), S.t0);
    S.fps10 = secs > 0 ? (double)S.loops / secs : 0;
    marker_write();
    build_header();
}

void report_session_end(const char *how) {
    if (!S.on) return;
    char st[300], when[32], line[700];
    stats_text(st, sizeof st);
    ts(when, sizeof when, time(NULL), 0);
    double secs = difftime(time(NULL), S.t0);
    snprintf(line, sizeof line, "%s game=%s %s fps=%.1f build=%s end=%s", when, S.game, st, secs > 0 ? (double)S.loops / secs : 0.0, SGBA_BUILD, how);
    sessions_append(line);
    if (g_errors_session > 0) write_report("errors", "the game logged errors during this session (see the log below)", 0);
    S.on = 0;
    marker_write();
}

void report_shutdown(int rc) {
    if (rc != 0) {
        char note[100];
        snprintf(note, sizeof note, "SGBA exited with code %d before or outside a game.", rc);
        write_report("failed", note, 1);
    }
    remove(g_marker);
}
