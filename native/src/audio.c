/* Sound: Ogg/WAV decoded with stb_vorbis, mixed at 22.05 kHz stereo in the SDL audio callback.
 *
 * Long sounds (music) are streamed: a helper thread decodes them a little ahead into a ring buffer, so decoding never costs
 * frame time. Short sounds are decoded once to memory and kept in a small LRU cache. Commands come from the shim as
 * register / start / gain / stop / busy, exactly like the Python engine (sgba_audio.py).
 */
#include <math.h>
#include <stdatomic.h>
#ifdef _WIN32
#include <process.h>
#endif
#include "stb_vorbis.c"
#include "natives.h"
#include "platform.h"

#define NATIVE(name) static JSValue name(JSContext *ctx, JSValueConst this_val, int argc, JSValueConst *argv)
#define NAT() host_natives((Host *)JS_GetContextOpaque(ctx))

#define RATE 22050
#define MAXV 12
#define RING_FRAMES 32768
#define STREAM_MIN_BYTES (96 * 1024)
#define SFX_BUDGET (6u * 1024 * 1024)

typedef struct { int16_t *pcm; size_t frames; char *path; unsigned long used; } Sfx;

typedef struct {
    int active;
    int sid;
    int stream, loop, done;
    float gain;
    Sfx *sfx;
    size_t pos;
    stb_vorbis *vb;
    int srate, ch;
    int16_t *ring;
    atomic_size_t rd, wr;
    double frac;
    int eof;
    atomic_int busy;
    int16_t carry[2];
    int have_carry;
} Voice;

typedef struct { char *path; size_t size; double duration; } Buffer;

struct Audio {
    int ready;                 /* 0 untried, 1 running, -1 silent */
    SDL_AudioDeviceID dev;
    Voice voices[MAXV];
    Buffer *bufs; int nbufs, bufcap;
    Sfx **sfx; int nsfx; size_t sfx_bytes; unsigned long tick;
    SDL_Thread *thread;
    atomic_int quit;
    Host *host;
    const char *root;
    unsigned underruns, logged;   /* music ring ran dry (audio thread counts, main thread reports) */
    int polls;
};

static Audio *g_audio = NULL;

static void clear_voice(Voice *v) {
    if (v->vb) { stb_vorbis_close(v->vb); v->vb = NULL; }
    free(v->ring); v->ring = NULL;
    v->active = 0; v->sfx = NULL;
}

/* ---------------------------------------------------------------- mixing (audio thread) */

static void mix_cb(void *user, Uint8 *out, int len) {
    Audio *a = user;
    int frames = len / 4;
    int32_t acc[2048 * 2];
    if (frames > 2048) frames = 2048;
    memset(acc, 0, sizeof(int32_t) * (size_t)frames * 2);
    for (int i = 0; i < MAXV; i++) {
        Voice *v = &a->voices[i];
        if (!v->active || v->done) continue;
        int gain = (int)(v->gain * 256.0f);
        if (v->stream) {
            size_t rd = atomic_load(&v->rd), wr = atomic_load(&v->wr);
            for (int f = 0; f < frames; f++) {
                if (rd == wr) { if (v->eof) v->done = 1; else a->underruns++; break; }
                const int16_t *p = v->ring + (rd % RING_FRAMES) * 2;
                acc[f * 2] += p[0] * gain / 256; acc[f * 2 + 1] += p[1] * gain / 256;
                rd++;
            }
            atomic_store(&v->rd, rd);
        } else if (v->sfx) {
            for (int f = 0; f < frames; f++) {
                if (v->pos >= v->sfx->frames) { if (v->loop && v->sfx->frames) v->pos = 0; else { v->done = 1; break; } }
                const int16_t *p = v->sfx->pcm + v->pos * 2;
                acc[f * 2] += p[0] * gain / 256; acc[f * 2 + 1] += p[1] * gain / 256;
                v->pos++;
            }
        }
    }
    int16_t *o = (int16_t *)out;
    for (int k = 0; k < frames * 2; k++) { int32_t s = acc[k]; o[k] = (int16_t)(s > 32767 ? 32767 : s < -32768 ? -32768 : s); }
    if (frames * 4 < len) memset(out + frames * 4, 0, (size_t)(len - frames * 4));
}

/* ---------------------------------------------------------------- stream decoding (helper thread) */

static int decode_thread(void *arg) {
    Audio *a = arg;
    short *tmp = malloc(sizeof(short) * 2 * 4096 * 4);
    while (!atomic_load(&a->quit)) {
        int worked = 0;
        for (int i = 0; i < MAXV; i++) {
            Voice *v = &a->voices[i];
            if (!v->active || !v->stream || !v->vb || v->eof) continue;
            atomic_store(&v->busy, 1);
            if (!v->active || !v->vb) { atomic_store(&v->busy, 0); continue; }
            size_t rd = atomic_load(&v->rd), wr = atomic_load(&v->wr);
            if (RING_FRAMES - (wr - rd) < 4096 + 8) { atomic_store(&v->busy, 0); continue; }
            double step = (double)v->srate / RATE;                      /* input frames per output frame */
            int want_out = 2048;
            int want_in = (int)(want_out * step) + 2;
            if (want_in > 4096 * 2) want_in = 4096 * 2;
            int cf = v->have_carry ? 1 : 0;                              /* last frame of the previous chunk, so interpolation never skips across the seam */
            if (cf) memcpy(tmp, v->carry, sizeof(short) * (size_t)v->ch);
            int got = stb_vorbis_get_samples_short_interleaved(v->vb, v->ch, tmp + cf * v->ch, want_in * v->ch);
            if (got <= 0) {
                if (v->loop) { stb_vorbis_seek_start(v->vb); v->frac = 0; v->have_carry = 0; }
                else v->eof = 1;
                atomic_store(&v->busy, 0);
                continue;
            }
            int n = got + cf;
            /* linear resample to RATE; position 0 is the carried frame */
            double pos = v->frac;
            while (pos + 1 < n && RING_FRAMES - (wr - rd) > 1) {
                int i0 = (int)pos;
                double t = pos - i0;
                int16_t l0, r0, l1, r1;
                if (v->ch == 1) { l0 = r0 = tmp[i0]; l1 = r1 = tmp[i0 + 1]; }
                else { l0 = tmp[i0 * 2]; r0 = tmp[i0 * 2 + 1]; l1 = tmp[(i0 + 1) * 2]; r1 = tmp[(i0 + 1) * 2 + 1]; }
                int16_t *dst = v->ring + (wr % RING_FRAMES) * 2;
                dst[0] = (int16_t)(l0 + (l1 - l0) * t);
                dst[1] = (int16_t)(r0 + (r1 - r0) * t);
                wr++;
                pos += step;
            }
            memcpy(v->carry, tmp + (size_t)(n - 1) * v->ch, sizeof(short) * (size_t)v->ch);
            v->have_carry = 1;
            v->frac = pos - (n - 1);
            if (v->frac < 0) v->frac = 0;
            atomic_store(&v->wr, wr);
            atomic_store(&v->busy, 0);
            worked = 1;
        }
        if (!worked) sdl.Delay(8);
    }
    free(tmp);
    return 0;
}

/* ---------------------------------------------------------------- loading */

static double ogg_seconds(const char *path) {
    int err;
    stb_vorbis *v = stb_vorbis_open_filename(path, &err, NULL);
    if (!v) return 0;
    double d = stb_vorbis_stream_length_in_seconds(v);
    stb_vorbis_close(v);
    return d;
}

static int16_t *to_stereo22k(const short *in, int frames, int ch, int srate, size_t *out_frames) {
    double step = (double)srate / RATE;
    size_t n = (size_t)(frames / step);
    int16_t *out = malloc((n + 1) * 4);
    for (size_t i = 0; i < n; i++) {
        double pos = i * step;
        int i0 = (int)pos, i1 = i0 + 1 < frames ? i0 + 1 : i0;
        double t = pos - i0;
        int l0 = ch == 1 ? in[i0] : in[i0 * 2], r0 = ch == 1 ? in[i0] : in[i0 * 2 + 1];
        int l1 = ch == 1 ? in[i1] : in[i1 * 2], r1 = ch == 1 ? in[i1] : in[i1 * 2 + 1];
        out[i * 2] = (int16_t)(l0 + (l1 - l0) * t);
        out[i * 2 + 1] = (int16_t)(r0 + (r1 - r0) * t);
    }
    *out_frames = n;
    return out;
}

static int16_t *decode_wav(const char *path, size_t *frames) {
    size_t len;
    unsigned char *d = sg_read_file(path, &len);
    if (!d || len < 44 || memcmp(d, "RIFF", 4) || memcmp(d + 8, "WAVE", 4)) { free(d); return NULL; }
    int ch = 2, rate = 44100, bits = 16;
    size_t pos = 12, data = 0, dlen = 0;
    while (pos + 8 <= len) {
        unsigned csz = d[pos + 4] | (d[pos + 5] << 8) | (d[pos + 6] << 16) | ((unsigned)d[pos + 7] << 24);
        if (!memcmp(d + pos, "fmt ", 4) && pos + 24 <= len) { ch = d[pos + 10] | (d[pos + 11] << 8); rate = d[pos + 12] | (d[pos + 13] << 8) | (d[pos + 14] << 16) | (d[pos + 15] << 24); bits = d[pos + 22] | (d[pos + 23] << 8); }
        if (!memcmp(d + pos, "data", 4)) { data = pos + 8; dlen = csz; if (data + dlen > len) dlen = len - data; break; }
        pos += 8 + csz + (csz & 1);
    }
    if (!data || bits != 16 || (ch != 1 && ch != 2)) { free(d); return NULL; }
    int frames_in = (int)(dlen / (2 * (size_t)ch));
    int16_t *out = to_stereo22k((const short *)(d + data), frames_in, ch, rate, frames);
    free(d);
    return out;
}

static Sfx *get_sfx(Audio *a, const char *path) {
    for (int i = 0; i < a->nsfx; i++) if (!strcmp(a->sfx[i]->path, path)) { a->sfx[i]->used = ++a->tick; return a->sfx[i]; }
    size_t frames = 0;
    int16_t *pcm = NULL;
    size_t pl = strlen(path);
    if (pl > 4 && !strcasecmp(path + pl - 4, ".wav")) pcm = decode_wav(path, &frames);
    else {
        int ch, rate;
        short *raw;
        int n = stb_vorbis_decode_filename(path, &ch, &rate, &raw);
        if (n > 0 && raw) { pcm = to_stereo22k(raw, n, ch, rate, &frames); free(raw); }
    }
    if (!pcm) return NULL;
    size_t bytes = frames * 4;
    while (a->sfx_bytes + bytes > SFX_BUDGET && a->nsfx > 0) {          /* evict the least recently used one nobody is playing */
        int oldest = -1;
        for (int i = 0; i < a->nsfx; i++) {
            int playing = 0;
            for (int k = 0; k < MAXV; k++) if (a->voices[k].active && a->voices[k].sfx == a->sfx[i]) playing = 1;
            if (!playing && (oldest < 0 || a->sfx[i]->used < a->sfx[oldest]->used)) oldest = i;
        }
        if (oldest < 0) break;
        a->sfx_bytes -= a->sfx[oldest]->frames * 4;
        free(a->sfx[oldest]->pcm); free(a->sfx[oldest]->path); free(a->sfx[oldest]);
        a->sfx[oldest] = a->sfx[--a->nsfx];
    }
    a->sfx = realloc(a->sfx, (size_t)(a->nsfx + 1) * sizeof(Sfx *));
    Sfx *e = calloc(1, sizeof *e);
    e->pcm = pcm; e->frames = frames; e->path = strdup(path); e->used = ++a->tick;
    a->sfx[a->nsfx++] = e;
    a->sfx_bytes += bytes;
    return e;
}

static int audio_init(Audio *a) {
    if (a->ready) return a->ready > 0;
    a->ready = -1;
    if (getenv("SGBA_NOAUDIO") || !sdl.InitSubSystem) return 0;
    if (sdl.InitSubSystem(SDL_INIT_AUDIO) != 0) { sg_log("warn", "audio unavailable, running silent: %s", sdl.GetError()); return 0; }
    SDL_AudioSpec want, have;
    memset(&want, 0, sizeof want);
    want.freq = RATE; want.format = AUDIO_S16SYS; want.channels = 2; want.samples = 1024; want.callback = mix_cb; want.userdata = a;
    a->dev = sdl.OpenAudioDevice(NULL, 0, &want, &have, 0);
    if (!a->dev) { sg_log("warn", "audio unavailable, running silent: %s", sdl.GetError()); return 0; }
    sdl.PauseAudioDevice(a->dev, 0);
    sg_log("info", "audio on: %d Hz, %d ch, %d samples per buffer", have.freq, have.channels, have.samples);
#ifdef _WIN32       /* SDL on Windows wants the caller's thread start functions */
    a->thread = sdl.CreateThread(decode_thread, "sgba-audio", a, (pfnSDL_CurrentBeginThread)_beginthreadex, (pfnSDL_CurrentEndThread)_endthreadex);
#else
    a->thread = sdl.CreateThread(decode_thread, "sgba-audio", a);
#endif
    a->ready = 1;
    return 1;
}

/* ---------------------------------------------------------------- JS side */

static Voice *voice_of(Audio *a, int sid) { for (int i = 0; i < MAXV; i++) if (a->voices[i].active && a->voices[i].sid == sid) return &a->voices[i]; return NULL; }

static void stop_voice(Audio *a, Voice *v) {
    if (!v) return;
    sdl.LockAudioDevice(a->dev);
    v->active = 0;
    sdl.UnlockAudioDevice(a->dev);
    while (atomic_load(&v->busy)) sdl.Delay(1);
    clear_voice(v);
}

NATIVE(n_audio_register) {
    Natives *n = NAT();
    Audio *a = n->audio;
    const char *url = JS_ToCString(ctx, argv[0]);
    char *path = url ? sg_resolve(host_root((Host *)JS_GetContextOpaque(ctx)), url) : NULL;
    if (url) JS_FreeCString(ctx, url);
    if (!path) return JS_NewString(ctx, "-1,0");
    int id = -1;
    for (int i = 0; i < a->nbufs; i++) if (!strcmp(a->bufs[i].path, path)) { id = i; break; }
    if (id < 0) {
        if (a->nbufs == a->bufcap) { a->bufcap = a->bufcap ? a->bufcap * 2 : 32; a->bufs = realloc(a->bufs, a->bufcap * sizeof(Buffer)); }
        Buffer *b = &a->bufs[a->nbufs];
        b->path = strdup(path);
        size_t len; unsigned char *d = sg_read_file(path, &len);
        b->size = d ? len : 0;
        free(d);
        size_t pl = strlen(path);
        b->duration = (pl > 4 && !strcasecmp(path + pl - 4, ".ogg")) ? ogg_seconds(path) : (double)b->size * 8 / 128000.0;
        id = a->nbufs++;
    }
    free(path);
    char buf[64];
    snprintf(buf, sizeof buf, "%d,%.3f", id + 1, a->bufs[id].duration);
    return JS_NewString(ctx, buf);
}

NATIVE(n_audio_cmd) {
    Natives *n = NAT();
    Audio *a = n->audio;
    const char *cmd = JS_ToCString(ctx, argv[0]);
    int sid = js_arg_i(ctx, argv, 1);
    if (!strcmp(cmd, "start")) {
        int bid = js_arg_i(ctx, argv, 2) - 1;
        const char *spec = JS_ToCString(ctx, argv[3]);
        if (bid >= 0 && bid < a->nbufs && audio_init(a)) {
            float gain = spec ? (float)atof(spec) : 1.0f;
            const char *comma = spec ? strchr(spec, ',') : NULL;
            int loop = comma && comma[1] == '1';
            if (gain < 0) gain = 0; if (gain > 1) gain = 1;
            stop_voice(a, voice_of(a, sid));
            Voice *v = NULL;
            for (int i = 0; i < MAXV; i++) if (!a->voices[i].active) { v = &a->voices[i]; break; }
            if (v) {
                Buffer *b = &a->bufs[bid];
                size_t pl = strlen(b->path);
                int is_ogg = pl > 4 && !strcasecmp(b->path + pl - 4, ".ogg");
                memset(v, 0, sizeof *v);
                v->sid = sid; v->gain = gain; v->loop = loop;
                if (is_ogg && b->size > STREAM_MIN_BYTES) {
                    int err;
                    stb_vorbis *vb = stb_vorbis_open_filename(b->path, &err, NULL);
                    if (vb) {
                        stb_vorbis_info info = stb_vorbis_get_info(vb);
                        for (int k = 0; k < MAXV; k++) if (a->voices[k].active && a->voices[k].stream) stop_voice(a, &a->voices[k]);   /* one music track at a time */
                        v->vb = vb; v->srate = (int)info.sample_rate; v->ch = info.channels > 1 ? 2 : 1; v->stream = 1;
                        v->ring = calloc(RING_FRAMES, 4);
                        sg_log("info", "music start: %s (%d Hz, %d ch, loop %d)", b->path, v->srate, v->ch, loop);
                        atomic_store(&v->rd, 0); atomic_store(&v->wr, 0);
                        sdl.LockAudioDevice(a->dev); v->active = 1; sdl.UnlockAudioDevice(a->dev);
                    }
                } else {
                    Sfx *s = get_sfx(a, b->path);
                    if (s) { sdl.LockAudioDevice(a->dev); v->sfx = s; v->active = 1; sdl.UnlockAudioDevice(a->dev); }
                }
            }
        }
        if (spec) JS_FreeCString(ctx, spec);
    } else if (!strcmp(cmd, "gain")) {
        Voice *v = voice_of(a, sid);
        if (v) { double g = js_arg_d(ctx, argv, 2); v->gain = (float)(g < 0 ? 0 : g > 1 ? 1 : g); }
    } else if (!strcmp(cmd, "stop")) {
        if (a->ready > 0) stop_voice(a, voice_of(a, sid));
    }
    JS_FreeCString(ctx, cmd);
    return JS_UNDEFINED;
}

NATIVE(n_audio_busy) {
    Audio *a = NAT()->audio;
    Voice *v = voice_of(a, js_arg_i(ctx, argv, 0));
    return JS_NewBool(ctx, v && !v->done);
}

void audio_update(Natives *n) {
    Audio *a = n->audio;
    if (!a || a->ready <= 0) return;
    if (++a->polls % 120 == 0 && a->underruns != a->logged) { sg_log("warn", "music underrun: %u buffers ran dry so far", a->underruns); a->logged = a->underruns; }
    for (int i = 0; i < MAXV; i++) {
        Voice *v = &a->voices[i];
        if (v->active && v->done && !v->loop) stop_voice(a, v);
    }
}

void audio_register(Host *h, Natives *n, JSContext *ctx) {
    n->audio = calloc(1, sizeof(Audio));
    n->audio->host = h;
    g_audio = n->audio;
    JSValue g = JS_GetGlobalObject(ctx);
    JS_SetPropertyStr(ctx, g, "__py_audio_register", JS_NewCFunction(ctx, n_audio_register, "__py_audio_register", 1));
    JS_SetPropertyStr(ctx, g, "__py_audio_cmd", JS_NewCFunction(ctx, n_audio_cmd, "__py_audio_cmd", 4));
    JS_SetPropertyStr(ctx, g, "__py_audio_busy", JS_NewCFunction(ctx, n_audio_busy, "__py_audio_busy", 1));
    JS_FreeValue(ctx, g);
}

void audio_shutdown(Natives *n) {
    Audio *a = n->audio;
    if (!a) return;
    if (a->ready > 0) {
        atomic_store(&a->quit, 1);
        if (a->thread) sdl.WaitThread(a->thread, NULL);
        sdl.CloseAudioDevice(a->dev);
        if (a->underruns) sg_log("warn", "music underruns this session: %u", a->underruns);
    }
    for (int i = 0; i < MAXV; i++) clear_voice(&a->voices[i]);
    for (int i = 0; i < a->nsfx; i++) { free(a->sfx[i]->pcm); free(a->sfx[i]->path); free(a->sfx[i]); }
    free(a->sfx);
    for (int i = 0; i < a->nbufs; i++) free(a->bufs[i].path);
    free(a->bufs);
    free(a);
    n->audio = NULL;
}
