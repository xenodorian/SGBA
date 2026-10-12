/* SGBA: SDL2 is loaded at run time (dlopen / LoadLibrary); these headers supply types and constants only.
   The platform-generated configuration is replaced by this minimal one. */
#ifndef SDL_config_h_
#define SDL_config_h_
#include "SDL_platform.h"
#define SDL_MAIN_HANDLED 1      /* we keep our own main(); SDL is never linked */
#include <stdarg.h>
#define HAVE_STDINT_H 1
#define HAVE_STDDEF_H 1
#define HAVE_INTTYPES_H 1
#endif
