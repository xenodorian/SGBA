/* Diagnostics: a rolling log tail, per-game frame statistics, crash reports and "did not exit cleanly" detection.
 * Everything lands in <app>/reports/ as plain text; GitROMs uploads those files. */
#ifndef REPORT_H
#define REPORT_H

void report_init(const char *app_dir);                       /* once, after the log is open */
void report_log_line(const char *level, const char *msg);    /* every sg_log line, kept for the report */
void report_session_begin(const char *game, double step_ms); /* a game starts */
void report_frame(double loop_ms);                           /* once per loop iteration while a game runs */
void report_tick(int rss_mb, int js_errors);                 /* about every 10 s while a game runs */
void report_session_end(const char *how);                    /* the game was left; writes the session line (and an errors report) */
void report_shutdown(int exit_code);                         /* process exit: removes the "running" marker; a non-zero code also writes a report */
#endif
