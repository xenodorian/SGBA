#ifndef PKG_H
#define PKG_H
#include <stddef.h>
int pkg_is_package(const char *path);                       /* ends in .sgba */
/* A game given as a folder or a .sgba file: fills `folder` with a directory to run from (a package is unpacked into
   cache_root once). Returns 0 on success. */
int pkg_resolve(const char *path, const char *cache_root, char *folder, size_t n);
int pkg_title(const char *path, char *out, size_t n);       /* manifest.json title of a package, 0 if found */
#endif
