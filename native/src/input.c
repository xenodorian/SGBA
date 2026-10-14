/* Keyboard and pad state: the same mapping rules as the Python runtime (runtime.py InputState). */
#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include "sgba.h"
#include "input.h"
#include "json.h"
#include "platform.h"

#define MAX_SRC 8
#define MAX_BIND 96
#define MAX_JOY 8

typedef struct { int kind, inst, a, b; } Source;        /* kind: 1 key, 2 button, 3 hat (a = hat, b = direction), 4 axis (a = axis, b = +1/-1) */
typedef struct { int kind, a, b, action; } Bind;        /* a = button / hat / axis, b = hat direction (1 l,2 r,3 u,4 d) or sign */

struct Input {
    Source src[A_COUNT][MAX_SRC];
    int nsrc[A_COUNT];
    Bind bind[MAX_BIND];
    int nbind;
    int bound;
    SDL_Joystick *joy[MAX_JOY];
    int njoy;
    char raw[16][32];
    int raw_head, raw_count;
    double axisv[8];                 /* live analog axes of the pad */
    int amap[4];                     /* which axes are left x, left y, right x, right y (controls.json "axes"; default 0 1 2 3) */
};

Input *input_create(void) { Input *in = calloc(1, sizeof(Input)); in->amap[0] = 0; in->amap[1] = 1; in->amap[2] = 2; in->amap[3] = 3; return in; }
void input_free(Input *in) { if (!in) return; for (int i = 0; i < in->njoy; i++) if (in->joy[i]) sdl.JoystickClose(in->joy[i]); free(in); }
int input_has_bindings(const Input *in) { return in->bound; }
int input_joystick_count(const Input *in) { return in->njoy; }

static const char *ACTION_NAMES[A_COUNT] = { "a", "b", "x", "y", "l1", "r1", "l2", "r2", "select", "start", "l3", "r3", "up", "down", "left", "right",
                                             "rs_left", "rs_right", "rs_up", "rs_down", "guide" };

static int action_of(const char *name) { for (int i = 0; i < A_COUNT; i++) if (!strcmp(ACTION_NAMES[i], name)) return i; return -1; }

static void set_source(Input *in, int action, Source s, int down) {
    Source *list = in->src[action];
    int *n = &in->nsrc[action];
    for (int i = 0; i < *n; i++) {
        if (list[i].kind == s.kind && list[i].inst == s.inst && list[i].a == s.a && list[i].b == s.b) {
            if (!down) list[i] = list[--(*n)];
            return;
        }
    }
    if (down && *n < MAX_SRC) list[(*n)++] = s;
}

int input_load_controls(Input *in, const char *path) {
    char *text = (char *)sg_read_file(path, NULL);
    if (!text) return 0;
    JNode *root = json_parse(text);
    free(text);
    JNode *b = json_get(root, "bindings");
    int ok = 0;
    if (b && b->type == J_OBJ) {
        in->nbind = 0;
        for (int i = 0; i < b->count; i++) {
            int action = action_of(b->keys[i]);
            JNode *list = b->items[i];
            if (action < 0 || !list || list->type != J_ARR) continue;
            for (int k = 0; k < list->count && in->nbind < MAX_BIND; k++) {
                JNode *s = list->items[k];
                if (!s || s->type != J_ARR || s->count < 2) continue;
                const char *kind = json_str(s->items[0], "");
                Bind *bd = &in->bind[in->nbind];
                bd->action = action;
                if (!strcmp(kind, "button")) { bd->kind = 2; bd->a = (int)json_num(s->items[1], 0); bd->b = 0; in->nbind++; }
                else if (!strcmp(kind, "hat") && s->count >= 3) {
                    const char *dir = json_str(s->items[2], "");
                    bd->kind = 3; bd->a = (int)json_num(s->items[1], 0);
                    bd->b = !strcmp(dir, "left") ? 1 : !strcmp(dir, "right") ? 2 : !strcmp(dir, "up") ? 3 : !strcmp(dir, "down") ? 4 : 0;
                    if (bd->b) in->nbind++;
                } else if (!strcmp(kind, "axis") && s->count >= 3) {
                    bd->kind = 4; bd->a = (int)json_num(s->items[1], 0);
                    bd->b = !strcmp(json_str(s->items[2], "+"), "-") ? -1 : 1;
                    in->nbind++;
                }
            }
        }
        in->bound = in->nbind > 0;
        ok = 1;
    }
    JNode *ax = json_get(root, "axes");                               /* optional: {"lx":0,"ly":1,"rx":2,"ry":3} */
    if (ax && ax->type == J_OBJ) {
        static const char *names[4] = { "lx", "ly", "rx", "ry" };
        for (int k = 0; k < 4; k++) { JNode *v = json_get(ax, names[k]); if (v) { int n = (int)json_num(v, k); if (n >= 0 && n < 8) in->amap[k] = n; } }
    }
    json_free(root);
    return ok;
}

void input_open_joysticks(Input *in) {
    sdl.InitSubSystem(SDL_INIT_JOYSTICK);
    int n = sdl.NumJoysticks();
    for (int i = 0; i < n && in->njoy < MAX_JOY; i++) {
        SDL_Joystick *j = sdl.JoystickOpen(i);
        if (j) { in->joy[in->njoy++] = j; sg_log("info", "joystick %d: %s, %d axes", i, sdl.JoystickName(j) ? sdl.JoystickName(j) : "?", sdl.JoystickNumAxes(j)); }
    }
}

static int key_action(int key) {
    switch (key) {
    case SDLK_UP: case SDLK_w: return A_UP;
    case SDLK_DOWN: case SDLK_s: return A_DOWN;
    case SDLK_LEFT: case SDLK_a: return A_LEFT;
    case SDLK_RIGHT: case SDLK_d: return A_RIGHT;
    case SDLK_z: case SDLK_SPACE: return A_A;
    case SDLK_x: case SDLK_LCTRL: return A_B;
    case SDLK_q: return A_X;
    case SDLK_e: return A_Y;
    case SDLK_RETURN: return A_START;
    case SDLK_BACKSPACE: return A_SELECT;
    case SDLK_7: return A_GUIDE;
    case SDLK_1: return A_L1;
    case SDLK_2: return A_R1;
    case SDLK_3: return A_L2;
    case SDLK_4: return A_R2;
    case SDLK_5: return A_L3;
    case SDLK_6: return A_R3;
    case SDLK_j: return A_RS_LEFT;
    case SDLK_l: return A_RS_RIGHT;
    case SDLK_i: return A_RS_UP;
    case SDLK_k: return A_RS_DOWN;
    }
    return -1;
}

static int default_button(int b) {
    switch (b) {
    case 0: return A_A; case 1: return A_B; case 2: return A_X; case 3: return A_Y; case 4: return A_SELECT; case 5: return A_GUIDE;
    case 6: return A_START; case 7: return A_L3; case 8: return A_R3; case 9: return A_L1; case 10: return A_R1;
    }
    return -1;
}

static void raw_push(Input *in, const char *text) {
    if (in->raw_count >= 16) return;
    snprintf(in->raw[(in->raw_head + in->raw_count) % 16], 32, "%s", text);
    in->raw_count++;
}
const char *input_raw_next(Input *in) {
    static char out[32];
    if (!in->raw_count) return NULL;
    snprintf(out, sizeof out, "%s", in->raw[in->raw_head]);
    in->raw_head = (in->raw_head + 1) % 16;
    in->raw_count--;
    return out;
}
void input_raw_clear(Input *in) { in->raw_count = 0; in->raw_head = 0; }
void input_clear_bindings(Input *in) { in->nbind = 0; in->bound = 0; }

void input_event(Input *in, const SDL_Event *e) {
    char t[32];
    switch (e->type) {
    case SDL_JOYBUTTONDOWN: snprintf(t, sizeof t, "button:%d", e->jbutton.button); raw_push(in, t); break;
    case SDL_JOYHATMOTION: { int v = e->jhat.value; const char *d = (v & SDL_HAT_LEFT) ? "left" : (v & SDL_HAT_RIGHT) ? "right" : (v & SDL_HAT_UP) ? "up" : (v & SDL_HAT_DOWN) ? "down" : NULL; if (d) { snprintf(t, sizeof t, "hat:%d:%s", e->jhat.hat, d); raw_push(in, t); } break; }
    case SDL_JOYAXISMOTION: if (abs(e->jaxis.value) > 26000) { snprintf(t, sizeof t, "axis:%d:%c", e->jaxis.axis, e->jaxis.value > 0 ? '+' : '-'); raw_push(in, t); } break;
    case SDL_KEYDOWN: if (e->key.keysym.sym != SDLK_ESCAPE) raw_push(in, "key"); break;
    default: break;
    }
    switch (e->type) {
    case SDL_KEYDOWN: case SDL_KEYUP: {
        int a = key_action(e->key.keysym.sym);
        if (a >= 0) { Source s = { 1, 0, (int)e->key.keysym.sym, 0 }; set_source(in, a, s, e->type == SDL_KEYDOWN); }
        break;
    }
    case SDL_JOYBUTTONDOWN: case SDL_JOYBUTTONUP: {
        int down = e->type == SDL_JOYBUTTONDOWN, inst = e->jbutton.which;
        if (in->bound) { for (int i = 0; i < in->nbind; i++) if (in->bind[i].kind == 2 && in->bind[i].a == e->jbutton.button) { Source s = { 2, inst, e->jbutton.button, 0 }; set_source(in, in->bind[i].action, s, down); } }
        else { int a = default_button(e->jbutton.button); if (a >= 0) { Source s = { 2, inst, e->jbutton.button, 0 }; set_source(in, a, s, down); } }
        break;
    }
    case SDL_JOYHATMOTION: {
        int v = e->jhat.value, hat = e->jhat.hat, inst = e->jhat.which;
        int state[5] = { 0, (v & SDL_HAT_LEFT) != 0, (v & SDL_HAT_RIGHT) != 0, (v & SDL_HAT_UP) != 0, (v & SDL_HAT_DOWN) != 0 };
        if (in->bound) { for (int i = 0; i < in->nbind; i++) if (in->bind[i].kind == 3 && in->bind[i].a == hat) { Source s = { 3, inst, hat, in->bind[i].b }; set_source(in, in->bind[i].action, s, state[in->bind[i].b]); } }
        else {
            static const int act[5] = { 0, A_LEFT, A_RIGHT, A_UP, A_DOWN };
            for (int d = 1; d <= 4; d++) { Source s = { 3, inst, hat, d }; set_source(in, act[d], s, state[d]); }
        }
        break;
    }
    case SDL_JOYAXISMOTION: {
        double v = e->jaxis.value / 32767.0;
        int axis = e->jaxis.axis, inst = e->jaxis.which;
        if (axis >= 0 && axis < 8) {
            in->axisv[axis] = v;
            static int logged[8];
            if (!logged[axis] && (v > 0.6 || v < -0.6)) { logged[axis] = 1; sg_log("info", "axis %d first moved (%.2f)", axis, v); }   /* tells a report which axis is which stick */
        }
        if (in->bound) {
            for (int i = 0; i < in->nbind; i++) if (in->bind[i].kind == 4 && in->bind[i].a == axis) { Source s = { 4, inst, axis, in->bind[i].b }; set_source(in, in->bind[i].action, s, in->bind[i].b > 0 ? v > 0.5 : v < -0.5); }
        } else if (axis <= 5) {
            int neg = -1, pos = -1;
            switch (axis) { case 0: neg = A_LEFT; pos = A_RIGHT; break; case 1: neg = A_UP; pos = A_DOWN; break; case 2: neg = A_RS_LEFT; pos = A_RS_RIGHT; break; case 3: neg = A_RS_UP; pos = A_RS_DOWN; break; case 4: pos = A_L2; break; default: pos = A_R2; }
            if (neg >= 0) { Source s = { 4, inst, axis, -1 }; set_source(in, neg, s, v < -0.5); }
            if (pos >= 0) { Source s = { 4, inst, axis, 1 }; set_source(in, pos, s, v > 0.5); }
        }
        break;
    }
    }
}

void input_set_axis(Input *in, int index, double v) { if (index >= 0 && index < 8) in->axisv[index] = v; }

/* The sticks as a page sees them: a circular dead zone, then rescaled so a full push still reads 1. */
void input_stick_axes(const Input *in, double out[4]) {
    for (int s = 0; s < 2; s++) {
        double x = in->axisv[in->amap[s * 2]], y = in->axisv[in->amap[s * 2 + 1]];
        double m = sqrt(x * x + y * y);
        if (m < 0.22) { x = y = 0; }
        else { double k = (m - 0.22) / (1.0 - 0.22); if (k > 1) k = 1; x = x / m * k; y = y / m * k; }
        out[s * 2] = x; out[s * 2 + 1] = y;
    }
}

int input_down(const Input *in, int action, int pad_only) {
    for (int i = 0; i < in->nsrc[action]; i++) if (!pad_only || in->src[action][i].kind != 1) return 1;
    return 0;
}

void input_pad_json(const Input *in, int pad_only, char *buf, size_t size) {
    static const int order[16] = { A_A, A_B, A_X, A_Y, A_L1, A_R1, A_L2, A_R2, A_SELECT, A_START, A_L3, A_R3, A_UP, A_DOWN, A_LEFT, A_RIGHT };
    size_t o = (size_t)snprintf(buf, size, "[{\"id\":\"SGBA standard gamepad\",\"index\":0,\"connected\":true,\"mapping\":\"standard\",\"timestamp\":0,\"buttons\":[");
    for (int i = 0; i < 16; i++) {
        int v = input_down(in, order[i], pad_only);
        o += (size_t)snprintf(buf + o, size - o, "%s{\"pressed\":%s,\"touched\":%s,\"value\":%d}", i ? "," : "", v ? "true" : "false", v ? "true" : "false", v);
    }
    double st[4];
    input_stick_axes(in, st);
    if (st[2] == 0 && st[3] == 0) {                                  /* no analog right stick: the digital one (keys or bound buttons) */
        st[2] = input_down(in, A_RS_LEFT, pad_only) ? -1 : input_down(in, A_RS_RIGHT, pad_only) ? 1 : 0;
        st[3] = input_down(in, A_RS_UP, pad_only) ? -1 : input_down(in, A_RS_DOWN, pad_only) ? 1 : 0;
    }
    snprintf(buf + o, size - o, "],\"axes\":[%.3f,%.3f,%.3f,%.3f]}]", st[0], st[1], st[2], st[3]);
}

/* SDL key -> (KeyboardEvent.key, .code, .keyCode) */
void input_key_names(int k, char *key, size_t ks, char *code, size_t cs, int *kc) {
    struct { int k; const char *key, *code; int kc; } T[] = {
        { SDLK_UP, "ArrowUp", "ArrowUp", 38 }, { SDLK_DOWN, "ArrowDown", "ArrowDown", 40 }, { SDLK_LEFT, "ArrowLeft", "ArrowLeft", 37 },
        { SDLK_RIGHT, "ArrowRight", "ArrowRight", 39 }, { SDLK_SPACE, " ", "Space", 32 }, { SDLK_RETURN, "Enter", "Enter", 13 },
        { SDLK_ESCAPE, "Escape", "Escape", 27 }, { SDLK_BACKSPACE, "Backspace", "Backspace", 8 }, { SDLK_TAB, "Tab", "Tab", 9 },
        { SDLK_LSHIFT, "Shift", "ShiftLeft", 16 }, { SDLK_RSHIFT, "Shift", "ShiftRight", 16 }, { SDLK_LCTRL, "Control", "ControlLeft", 17 },
        { SDLK_RCTRL, "Control", "ControlRight", 17 }, { SDLK_LALT, "Alt", "AltLeft", 18 }, { SDLK_RALT, "Alt", "AltRight", 18 },
    };
    for (size_t i = 0; i < sizeof T / sizeof T[0]; i++) if (T[i].k == k) { snprintf(key, ks, "%s", T[i].key); snprintf(code, cs, "%s", T[i].code); *kc = T[i].kc; return; }
    if (k >= 'a' && k <= 'z') { snprintf(key, ks, "%c", k); snprintf(code, cs, "Key%c", toupper(k)); *kc = toupper(k); return; }
    if (k >= '0' && k <= '9') { snprintf(key, ks, "%c", k); snprintf(code, cs, "Digit%c", k); *kc = k; return; }
    snprintf(key, ks, "Unidentified"); snprintf(code, cs, "Unidentified"); *kc = k & 255;
}
