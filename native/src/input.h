#ifndef INPUT_H
#define INPUT_H
#include "SDL.h"

enum { A_A, A_B, A_X, A_Y, A_L1, A_R1, A_L2, A_R2, A_SELECT, A_START, A_L3, A_R3, A_UP, A_DOWN, A_LEFT, A_RIGHT,
       A_RS_LEFT, A_RS_RIGHT, A_RS_UP, A_RS_DOWN, A_GUIDE, A_COUNT };

typedef struct Input Input;
Input *input_create(void);
void input_free(Input *in);
int input_load_controls(Input *in, const char *path);          /* controls.json: custom pad bindings; 1 if loaded */
void input_open_joysticks(Input *in);
void input_event(Input *in, const SDL_Event *e);
int input_down(const Input *in, int action, int pad_only);
int input_has_bindings(const Input *in);
const char *input_raw_next(Input *in);                       /* next deliberate raw press as text, or NULL */
void input_raw_clear(Input *in);
void input_clear_bindings(Input *in);
int input_joystick_count(const Input *in);
void input_pad_json(const Input *in, int pad_only, char *buf, size_t size);
void input_key_names(int sdl_key, char *key, size_t ks, char *code, size_t cs, int *key_code);
#endif
