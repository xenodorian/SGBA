/* .sgba game packages: a zip with manifest.json and the game's files. The runtime opens one directly: it unpacks to a cache folder
   once (again only when the file changes) and runs from there. Reading follows the limits of sgba_package.py. */
#include <ctype.h>
#include <errno.h>
#include <dirent.h>
#include <sys/stat.h>
#include <sys/types.h>
#ifdef _WIN32
#include <direct.h>
#include <io.h>
#define MKDIR(p) _mkdir(p)
#else
#include <unistd.h>
#define MKDIR(p) mkdir((p), 0755)
#endif
#include "sgba.h"
#include "pkg.h"
#include "stb_image.h"

#define MAX_ARCHIVE (64u * 1024 * 1024)
#define MAX_UNPACKED (192u * 1024 * 1024)
#define MAX_FILE (64u * 1024 * 1024)
#define MAX_FILES 4000

typedef struct { unsigned char *data; size_t size; } Zip;

static unsigned rd16(const unsigned char *p) { return (unsigned)p[0] | ((unsigned)p[1] << 8); }
static unsigned long rd32(const unsigned char *p) { return (unsigned long)p[0] | ((unsigned long)p[1] << 8) | ((unsigned long)p[2] << 16) | ((unsigned long)p[3] << 24); }

int pkg_is_package(const char *path) {
    size_t n = strlen(path);
    return n > 5 && !strcasecmp(path + n - 5, ".sgba");
}

static int zip_open(const char *path, Zip *z) {
    size_t size;
    z->data = sg_read_file(path, &size);
    z->size = size;
    if (!z->data) return -1;
    if (size > MAX_ARCHIVE || size < 22) { free(z->data); z->data = NULL; return -1; }
    return 0;
}

/* one central directory entry: returns the offset of the next one, or 0 */
typedef struct { char name[512]; unsigned method; unsigned long csize, usize, local; } Entry;

static int find_eocd(const Zip *z, size_t *cd_off, unsigned *count) {
    size_t lo = z->size > 65557 ? z->size - 65557 : 0;
    for (size_t i = z->size - 22 + 1; i-- > lo;) {
        if (rd32(z->data + i) == 0x06054b50UL) {
            *count = rd16(z->data + i + 10);
            *cd_off = rd32(z->data + i + 16);
            return *cd_off < z->size ? 0 : -1;
        }
    }
    return -1;
}

static size_t next_entry(const Zip *z, size_t off, Entry *e) {
    if (off + 46 > z->size || rd32(z->data + off) != 0x02014b50UL) return 0;
    unsigned nlen = rd16(z->data + off + 28), xlen = rd16(z->data + off + 30), clen = rd16(z->data + off + 32);
    if (off + 46 + nlen + xlen + clen > z->size) return 0;
    e->method = rd16(z->data + off + 10);
    e->csize = rd32(z->data + off + 20); e->usize = rd32(z->data + off + 24); e->local = rd32(z->data + off + 42);
    if (nlen >= sizeof e->name) nlen = sizeof e->name - 1;
    memcpy(e->name, z->data + off + 46, nlen); e->name[nlen] = 0;
    return off + 46 + rd16(z->data + off + 28) + xlen + clen;
}

/* the file's bytes (malloc'd, NUL-terminated); NULL when unreadable or over the limit */
static unsigned char *entry_data(const Zip *z, const Entry *e, size_t *outlen) {
    if (e->usize > MAX_FILE || e->local + 30 > z->size || rd32(z->data + e->local) != 0x04034b50UL) return NULL;
    size_t start = e->local + 30 + rd16(z->data + e->local + 26) + rd16(z->data + e->local + 28);
    if (start + e->csize > z->size) return NULL;
    unsigned char *out;
    if (e->method == 0) {
        out = malloc((size_t)e->usize + 1);
        if (!out) return NULL;
        memcpy(out, z->data + start, e->usize);
    } else if (e->method == 8) {
        int got = 0;
        unsigned char *padded = malloc((size_t)e->csize + 16);      /* the inflater treats running out of input as an error; zip streams have no trailer */
        if (!padded) return NULL;
        memcpy(padded, z->data + start, e->csize);
        memset(padded + e->csize, 0, 16);
        char *raw = stbi_zlib_decode_noheader_malloc((const char *)padded, (int)e->csize + 16, &got);
        free(padded);
        if (!raw || (unsigned long)got != e->usize) { free(raw); return NULL; }
        out = realloc(raw, (size_t)got + 1);
        if (!out) { free(raw); return NULL; }
    } else return NULL;
    out[e->usize] = 0;
    *outlen = e->usize;
    return out;
}

static int safe_name(const char *n) {
    if (!*n || n[0] == '/' || strchr(n, '\\') || strchr(n, ':')) return 0;
    for (const char *p = n; *p;) {
        const char *s = strchr(p, '/');
        size_t len = s ? (size_t)(s - p) : strlen(p);
        if (len == 0 && s && s[1]) return 0;
        if ((len == 1 && p[0] == '.') || (len == 2 && p[0] == '.' && p[1] == '.')) return 0;
        if (!s) break;
        p = s + 1;
    }
    return 1;
}

static void mkdirs(char *path) {
    for (char *p = path + 1; *p; p++) if (*p == '/') { *p = 0; MKDIR(path); *p = '/'; }
}

static void remove_tree(const char *path) {
    DIR *d = opendir(path);
    struct dirent *e;
    while (d && (e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, "..")) continue;
        char child[2800]; snprintf(child, sizeof child, "%s/%s", path, e->d_name);
        struct stat st;
        if (stat(child, &st) == 0 && S_ISDIR(st.st_mode)) remove_tree(child); else remove(child);
    }
    if (d) closedir(d);
    rmdir(path);
}

/* an updated package leaves its old unpacked copies behind: delete them (same name, other size or date) */
static void drop_old_copies(const char *cache_root, const char *stem, const char *keep) {
    DIR *d = opendir(cache_root);
    struct dirent *e;
    size_t sl = strlen(stem);
    while (d && (e = readdir(d))) {
        if (strncmp(e->d_name, stem, sl) || e->d_name[sl] != '-') continue;
        char full[2600]; snprintf(full, sizeof full, "%s/%s", cache_root, e->d_name);
        if (strcmp(full, keep)) remove_tree(full);
    }
    if (d) closedir(d);
}

int pkg_title(const char *path, char *out, size_t n) {
    Zip z; size_t off, got; unsigned count;
    if (zip_open(path, &z) != 0) return -1;
    int ok = -1;
    if (find_eocd(&z, &off, &count) == 0) {
        Entry e;
        for (unsigned i = 0; i < count && (off = next_entry(&z, off, &e)); i++) {
            if (strcmp(e.name, "manifest.json")) continue;
            unsigned char *m = entry_data(&z, &e, &got);
            if (m) {
                const char *t = strstr((const char *)m, "\"title\"");
                if (t && (t = strchr(t + 7, ':')) && (t = strchr(t, '"'))) {
                    size_t k = 0;
                    for (t++; *t && *t != '"' && k + 1 < n; t++) out[k++] = *t;
                    out[k] = 0; ok = k ? 0 : -1;
                }
                free(m);
            }
            break;
        }
    }
    free(z.data);
    return ok;
}

int pkg_resolve(const char *path, const char *cache_root, char *folder, size_t n) {
    struct stat st;
    if (stat(path, &st) != 0) return -1;
    if (S_ISDIR(st.st_mode) || !pkg_is_package(path)) { snprintf(folder, n, "%s", path); return 0; }
    const char *base = strrchr(path, '/');
#ifdef _WIN32
    const char *b2 = strrchr(path, '\\'); if (b2 && (!base || b2 > base)) base = b2;
#endif
    base = base ? base + 1 : path;
    char stem[200]; snprintf(stem, sizeof stem, "%.*s", (int)(strlen(base) - 5), base);
    snprintf(folder, n, "%s/%s-%lx-%lx", cache_root, stem, (unsigned long)st.st_size, (unsigned long)st.st_mtime);
    char marker[2600]; snprintf(marker, sizeof marker, "%s/.unpacked", folder);
    struct stat ms;
    if (stat(marker, &ms) == 0) return 0;                         /* unpacked earlier, same file */

    Zip z; size_t off, got; unsigned count;
    if (zip_open(path, &z) != 0) { sg_log("error", "cannot read package %s", path); return -1; }
    if (find_eocd(&z, &off, &count) != 0 || count > MAX_FILES) { sg_log("error", "%s is not a valid .sgba", path); free(z.data); return -1; }
    char tmp[2600]; snprintf(tmp, sizeof tmp, "%s/x", cache_root); mkdirs(tmp); MKDIR(cache_root);
    snprintf(tmp, sizeof tmp, "%s/x", folder); mkdirs(tmp); MKDIR(folder);
    unsigned long total = 0;
    int has_manifest = 0, has_index = 0, ok = 1;
    Entry e;
    for (unsigned i = 0; i < count && ok; i++) {
        off = next_entry(&z, off, &e);
        if (!off) { ok = 0; break; }
        size_t nl = strlen(e.name);
        if (nl && e.name[nl - 1] == '/') continue;                  /* a folder entry */
        if (!safe_name(e.name)) { sg_log("error", "unsafe path in package: %s", e.name); ok = 0; break; }
        total += e.usize;
        if (total > MAX_UNPACKED) { sg_log("error", "package unpacks too large"); ok = 0; break; }
        if (!strcmp(e.name, "manifest.json")) has_manifest = 1;
        if (!strcmp(e.name, "index.html")) has_index = 1;
        unsigned char *data = entry_data(&z, &e, &got);
        if (!data) { sg_log("error", "cannot unpack %s (method %u, %lu -> %lu, at %lu)", e.name, e.method, e.csize, e.usize, e.local); ok = 0; break; }
        char dest[2700]; snprintf(dest, sizeof dest, "%s/%s", folder, e.name);
        mkdirs(dest);
        FILE *f = fopen(dest, "wb");
        if (!f || fwrite(data, 1, got, f) != got) ok = 0;
        if (f) fclose(f);
        free(data);
    }
    free(z.data);
    if (!ok || !has_manifest || !has_index) { sg_log("error", "package %s could not be unpacked", path); return -1; }
    FILE *m = fopen(marker, "wb");
    if (m) fclose(m);
    drop_old_copies(cache_root, stem, folder);
    sg_log("info", "unpacked %s to %s", path, folder);
    return 0;
}
