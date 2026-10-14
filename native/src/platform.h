/* Window, joystick and audio device through SDL2, which is loaded at run time (nothing to link against). */
#ifndef PLATFORM_H
#define PLATFORM_H
#include "SDL.h"
#include "raster.h"

typedef struct {
    typeof(&SDL_Init) Init;
    typeof(&SDL_InitSubSystem) InitSubSystem;
    typeof(&SDL_QuitSubSystem) QuitSubSystem;
    typeof(&SDL_Quit) Quit;
    typeof(&SDL_GetError) GetError;
    typeof(&SDL_SetHint) SetHint;
    typeof(&SDL_CreateWindow) CreateWindow;
    typeof(&SDL_DestroyWindow) DestroyWindow;
    typeof(&SDL_GetWindowSurface) GetWindowSurface;
    typeof(&SDL_UpdateWindowSurface) UpdateWindowSurface;
    typeof(&SDL_UpdateWindowSurfaceRects) UpdateWindowSurfaceRects;
    typeof(&SDL_GetWindowSize) GetWindowSize;
    typeof(&SDL_PollEvent) PollEvent;
    typeof(&SDL_Delay) Delay;
    typeof(&SDL_GetPerformanceCounter) GetPerformanceCounter;
    typeof(&SDL_GetPerformanceFrequency) GetPerformanceFrequency;
    typeof(&SDL_ShowCursor) ShowCursor;
    typeof(&SDL_NumJoysticks) NumJoysticks;
    typeof(&SDL_JoystickOpen) JoystickOpen;
    typeof(&SDL_JoystickClose) JoystickClose;
    typeof(&SDL_JoystickName) JoystickName;
    typeof(&SDL_JoystickNumAxes) JoystickNumAxes;
    typeof(&SDL_JoystickInstanceID) JoystickInstanceID;
    typeof(&SDL_GetCurrentVideoDriver) GetCurrentVideoDriver;
    typeof(&SDL_OpenAudioDevice) OpenAudioDevice;
    typeof(&SDL_CloseAudioDevice) CloseAudioDevice;
    typeof(&SDL_PauseAudioDevice) PauseAudioDevice;
    typeof(&SDL_LockAudioDevice) LockAudioDevice;
    typeof(&SDL_UnlockAudioDevice) UnlockAudioDevice;
    typeof(&SDL_CreateThread) CreateThread;
    typeof(&SDL_WaitThread) WaitThread;
    typeof(&SDL_GetBasePath) GetBasePath;
    typeof(&SDL_free) free_;
} SdlApi;

extern SdlApi sdl;
int sdl_load(const char *hint_dir);                 /* 0 on success */

typedef struct Display Display;
Display *display_open(int logical_w, int logical_h, int fullscreen);
void display_present(Display *d, const Surf *logical, const IRect *rects, int n, int full);
void display_close(Display *d);
const char *display_driver(void);
#endif
