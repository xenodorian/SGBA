#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#ifdef _WIN32
#include <windows.h>
#undef CreateWindow
#undef CreateThread
#define LIB_OPEN(p) ((void *)LoadLibraryA(p))
#define LIB_SYM(h, n) ((void *)GetProcAddress((HMODULE)(h), n))
#else
#include <dlfcn.h>
#define LIB_OPEN(p) dlopen(p, RTLD_NOW | RTLD_GLOBAL)
#define LIB_SYM(h, n) dlsym(h, n)
#endif
#include "sgba.h"
#include "platform.h"

SdlApi sdl;
static void *g_lib = NULL;

#define LOAD(field, name) do { sdl.field = (typeof(sdl.field))LIB_SYM(g_lib, name); if (!sdl.field) { sg_log("error", "SDL symbol missing: %s", name); return -1; } } while (0)

int sdl_load(const char *hint_dir) {
    if (g_lib) return 0;
    char path[1024];
    const char *names[] = {
#ifdef _WIN32
        "SDL2.dll", NULL
#else
        "libSDL2-2.0.so.0", "libSDL2.so", NULL
#endif
    };
    const char *forced = getenv("SGBA_SDL");                 /* the launcher can point at the SDL it ships, when the system one fails */
    if (forced && *forced) g_lib = LIB_OPEN(forced);
    for (int i = 0; names[i] && !g_lib; i++) {
        if (hint_dir) { snprintf(path, sizeof path, "%s/%s", hint_dir, names[i]); g_lib = LIB_OPEN(path); }
        if (!g_lib) g_lib = LIB_OPEN(names[i]);
    }
    if (!g_lib) { sg_log("error", "SDL2 library not found"); return -1; }
    LOAD(Init, "SDL_Init"); LOAD(InitSubSystem, "SDL_InitSubSystem"); LOAD(QuitSubSystem, "SDL_QuitSubSystem"); LOAD(Quit, "SDL_Quit");
    LOAD(GetError, "SDL_GetError"); LOAD(SetHint, "SDL_SetHint"); LOAD(CreateWindow, "SDL_CreateWindow"); LOAD(DestroyWindow, "SDL_DestroyWindow");
    LOAD(GetWindowSurface, "SDL_GetWindowSurface"); LOAD(UpdateWindowSurface, "SDL_UpdateWindowSurface");
    LOAD(UpdateWindowSurfaceRects, "SDL_UpdateWindowSurfaceRects"); LOAD(GetWindowSize, "SDL_GetWindowSize"); LOAD(PollEvent, "SDL_PollEvent");
    LOAD(Delay, "SDL_Delay"); LOAD(GetPerformanceCounter, "SDL_GetPerformanceCounter"); LOAD(GetPerformanceFrequency, "SDL_GetPerformanceFrequency");
    LOAD(ShowCursor, "SDL_ShowCursor"); LOAD(NumJoysticks, "SDL_NumJoysticks"); LOAD(JoystickOpen, "SDL_JoystickOpen");
    LOAD(JoystickClose, "SDL_JoystickClose"); LOAD(JoystickName, "SDL_JoystickName"); LOAD(JoystickInstanceID, "SDL_JoystickInstanceID");
    LOAD(GetCurrentVideoDriver, "SDL_GetCurrentVideoDriver"); LOAD(OpenAudioDevice, "SDL_OpenAudioDevice");
    LOAD(CloseAudioDevice, "SDL_CloseAudioDevice"); LOAD(PauseAudioDevice, "SDL_PauseAudioDevice"); LOAD(LockAudioDevice, "SDL_LockAudioDevice");
    LOAD(UnlockAudioDevice, "SDL_UnlockAudioDevice"); LOAD(CreateThread, "SDL_CreateThread"); LOAD(WaitThread, "SDL_WaitThread");
    LOAD(GetBasePath, "SDL_GetBasePath"); LOAD(free_, "SDL_free");
    return 0;
}

struct Display {
    SDL_Window *win;
    int lw, lh;
    int bpp;             /* bytes per pixel of the window surface */
    int swap_rb;
    int r565;
};

static const char *g_driver = "";
const char *display_driver(void) { return g_driver; }

Display *display_open(int lw, int lh, int fullscreen) {
    if (sdl.Init(SDL_INIT_VIDEO) != 0) { sg_log("error", "SDL video init failed: %s", sdl.GetError()); return NULL; }
    Display *d = calloc(1, sizeof *d);
    d->lw = lw; d->lh = lh;
    unsigned flags = SDL_WINDOW_SHOWN;
    SDL_Window *win = NULL;
    if (fullscreen) win = sdl.CreateWindow("SGBA", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, lw, lh, flags | SDL_WINDOW_FULLSCREEN_DESKTOP);
    if (!win) win = sdl.CreateWindow("SGBA", SDL_WINDOWPOS_UNDEFINED, SDL_WINDOWPOS_UNDEFINED, lw, lh, flags);
    if (!win) { sg_log("error", "cannot open a window: %s", sdl.GetError()); free(d); return NULL; }
    d->win = win;
    sdl.ShowCursor(0);
    const char *drv = sdl.GetCurrentVideoDriver();
    g_driver = drv ? drv : "";
    SDL_Surface *s = sdl.GetWindowSurface(win);
    if (!s) { sg_log("error", "no window surface: %s", sdl.GetError()); sdl.DestroyWindow(win); free(d); return NULL; }
    d->bpp = s->format->BytesPerPixel;
    d->swap_rb = (s->format->Rmask == 0x000000FFu);
    d->r565 = (d->bpp == 2);
    sg_log("info", "video driver %s window %dx%d %d bytes per pixel", g_driver, s->w, s->h, d->bpp);
    return d;
}

void display_close(Display *d) {
    if (!d) return;
    if (d->win) sdl.DestroyWindow(d->win);
    free(d);
}

static inline uint32_t px_out(const Display *d, uint32_t p) {
    if (d->swap_rb) return (p & 0xFF00FF00u) | ((p & 0xFF) << 16) | ((p >> 16) & 0xFF);
    return p;
}

/* Copy (scaling to fit, centred) the changed parts of the logical canvas into the window. */
void display_present(Display *d, const Surf *logical, const IRect *rects, int n, int full) {
    SDL_Surface *ws = sdl.GetWindowSurface(d->win);
    if (!ws) return;
    int ww = ws->w, wh = ws->h, lw = logical->w, lh = logical->h;
    int scale = 1;
    int scaled = !(ww == lw && wh == lh);
    int ox = 0, oy = 0, dw = lw, dh = lh;
    if (scaled) {
        double sx = (double)ww / lw, sy = (double)wh / lh, s = sx < sy ? sx : sy;
        dw = (int)(lw * s); dh = (int)(lh * s);
        if (dw < 1) dw = 1; if (dh < 1) dh = 1;
        ox = (ww - dw) / 2; oy = (wh - dh) / 2;
        full = 1;
        (void)scale;
    }
    SDL_Rect up[16];
    int nup = 0;
    int count = full ? 1 : n;
    IRect whole = irect(0, 0, lw, lh);
    for (int k = 0; k < count; k++) {
        IRect r = full ? whole : irect_clip(rects[k], whole);
        if (irect_empty(r)) continue;
        for (int y = r.y; y < r.y + r.h; y++) {
            const uint32_t *src = logical->px + (size_t)y * lw;
            if (!scaled) {
                if (d->bpp == 4) {
                    uint32_t *dst = (uint32_t *)((uint8_t *)ws->pixels + (size_t)y * ws->pitch);
                    if (!d->swap_rb) memcpy(dst + r.x, src + r.x, (size_t)r.w * 4);
                    else for (int x = r.x; x < r.x + r.w; x++) dst[x] = px_out(d, src[x]);
                } else if (d->bpp == 2) {
                    uint16_t *dst = (uint16_t *)((uint8_t *)ws->pixels + (size_t)y * ws->pitch);
                    for (int x = r.x; x < r.x + r.w; x++) { uint32_t p = src[x]; dst[x] = (uint16_t)(((PR(p) >> 3) << 11) | ((PG(p) >> 2) << 5) | (PB(p) >> 3)); }
                }
            }
        }
        if (nup < 16 && !scaled) { up[nup].x = r.x; up[nup].y = r.y; up[nup].w = r.w; up[nup].h = r.h; nup++; }
    }
    if (scaled) {
        static int *xmap; static int xmap_w, xmap_ox, xmap_dw, xmap_lw;
        if (!xmap || xmap_w != ww || xmap_ox != ox || xmap_dw != dw || xmap_lw != lw) {          /* column map once, not a division per pixel */
            free(xmap);
            xmap = malloc(sizeof(int) * (size_t)ww);
            for (int x = 0; x < ww; x++) xmap[x] = (x < ox || x >= ox + dw) ? -1 : (int)((int64_t)(x - ox) * lw / dw);
            xmap_w = ww; xmap_ox = ox; xmap_dw = dw; xmap_lw = lw;
        }
        for (int y = 0; y < wh; y++) {
            uint8_t *row = (uint8_t *)ws->pixels + (size_t)y * ws->pitch;
            if (y < oy || y >= oy + dh) { memset(row, 0, (size_t)ww * d->bpp); continue; }
            int sy = (int)((int64_t)(y - oy) * lh / dh);
            const uint32_t *src = logical->px + (size_t)sy * lw;
            if (d->bpp == 4) {
                uint32_t *out = (uint32_t *)row;
                for (int x = 0; x < ww; x++) out[x] = xmap[x] < 0 ? 0 : px_out(d, src[xmap[x]]);
            } else {
                uint16_t *out = (uint16_t *)row;
                for (int x = 0; x < ww; x++) {
                    if (xmap[x] < 0) { out[x] = 0; continue; }
                    uint32_t p = src[xmap[x]];
                    out[x] = (uint16_t)(((PR(p) >> 3) << 11) | ((PG(p) >> 2) << 5) | (PB(p) >> 3));
                }
            }
        }
        sdl.UpdateWindowSurface(d->win);
    } else if (nup) sdl.UpdateWindowSurfaceRects(d->win, up, nup);
}
