/* Temporary no-op canvas, image and audio hooks, replaced module by module. */
#include "natives.h"
static JSValue noop(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv) { (void)ctx; (void)t; (void)argc; (void)argv; return JS_UNDEFINED; }
static JSValue ret_img(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv) { (void)t; (void)argc; (void)argv; return JS_NewString(ctx, "-1,0,0"); }
static JSValue ret_audio(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv) { (void)t; (void)argc; (void)argv; return JS_NewString(ctx, "-1,0"); }
static JSValue ret_zero(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv) { (void)t; (void)argc; (void)argv; return JS_NewInt32(ctx, 0); }
static JSValue ret_str(JSContext *ctx, JSValueConst t, int argc, JSValueConst *argv) { (void)t; (void)argc; (void)argv; return JS_NewString(ctx, ""); }
void audio_register(Host *h, Natives *n, JSContext *ctx) {
    (void)h; (void)n;
    JSValue g = JS_GetGlobalObject(ctx);
    JS_SetPropertyStr(ctx, g, "__py_audio_register", JS_NewCFunction(ctx, ret_audio, "__py_audio_register", 1));
    JS_SetPropertyStr(ctx, g, "__py_audio_cmd", JS_NewCFunction(ctx, noop, "__py_audio_cmd", 4));
    JS_SetPropertyStr(ctx, g, "__py_audio_busy", JS_NewCFunction(ctx, ret_zero, "__py_audio_busy", 1));
    JS_FreeValue(ctx, g);
}
void audio_shutdown(Natives *n) { (void)n; }
void audio_update(Natives *n) { (void)n; }
