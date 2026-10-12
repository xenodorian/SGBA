/* SGBA: SDL2 is loaded at run time (dlopen / LoadLibrary); these headers supply types and constants only.
   The platform-generated configuration is replaced by this minimal one. */
#ifndef SDL_config_h_
#define SDL_config_h_
#include "SDL_platform.h"
#include <stdarg.h>
#define HAVE_STDINT_H 1
#define HAVE_STDDEF_H 1
#define HAVE_INTTYPES_H 1
#endif
