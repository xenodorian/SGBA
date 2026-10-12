"""Run browser-style JavaScript canvas games inside SGBA.

A game is a folder with an index.html (plus scripts and assets) written for the browser. SGBA runs its
scripts in QuickJS, provides the browser features a 2D canvas game needs (see sgba_js_shim.js), and draws
the game's main canvas through the normal SGBA runtime, so menus, input and packaging work as for Python games.
"""
from __future__ import annotations

import base64
import binascii
import fnmatch
import gc
import html.parser
import io
import json
import math
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import pygame

import sgba_audio
import sgba_js_ui
from sgba_canvas import Canvas2D, gradient_surface, mult_ready
from sgba_gfx import ImageStore, LRUBytes, Stats, fade

try:
    import quickjs
except ImportError:  # the bundle always includes it; plain dev installs may not
    quickjs = None

SHIM_PATH = Path(__file__).resolve().parent / "sgba_js_shim.js"
IMAGE_ID_BASE = 1_000_000
STANDARD_BUTTONS = ("a", "b", "x", "y", "l1", "r1", "l2", "r2", "select", "start", "l3", "r3", "up", "down", "left", "right")


# ---------------------------------------------------------------- host

class _PageParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.items: list = []        # ("script", src or None, inline text) and ("element", tag, id)
        self._script_attrs = None
        self._script_text: list = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            self._script_attrs = a
            self._script_text = []
        elif a.get("id"):
            self.items.append(("element", tag, a["id"], a.get("width"), a.get("height")))

    def handle_data(self, data):
        if self._script_attrs is not None:
            self._script_text.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script_attrs is not None:
            a = self._script_attrs
            self.items.append(("script", a.get("src"), "".join(self._script_text), a.get("type", "")))
            self._script_attrs = None

    def unknown_decl(self, data):
        pass


def _default_log(level: str, message: str) -> None:
    """Console output goes to the launch log; the shell's sgba.log gets it too when running under the runtime."""
    print(f"[js {level}] {message}", file=sys.stderr)
    shell = sys.modules.get("sgba_shell")
    if shell is not None:
        shell.log(f"js {level}: {message[:400]}")


class JSHost:
    """One JavaScript page: QuickJS context, canvases, images, storage, audio."""

    def __init__(self, root: Path, save_path: Path | None = None, log=None, depth: int = 16,
                 image_budget_mb: int = 96, alpha_mode: str = "threshold", image_depth: int = 16, rle: bool = False) -> None:
        if quickjs is None:
            raise RuntimeError("The quickjs-ng package is required to run JavaScript games")
        self.root = Path(root).resolve()
        self.log_fn = log or _default_log
        self.save_path = save_path
        self.depth = 16 if depth == 16 else 32
        self.stats = Stats()
        self.canvases: dict = {}
        self.images = ImageStore(IMAGE_ID_BASE, image_budget_mb * 1024 * 1024, alpha_mode, compact=True, log=self.log_fn,
                                 depth=16 if image_depth == 16 else 32, rle=rle)
        self.variants = LRUBytes(12 * 1024 * 1024)      # flipped / scaled sprite copies
        self.texts = LRUBytes(2 * 1024 * 1024)          # rendered text
        self.tiles = LRUBytes(4 * 1024 * 1024)          # translucent fills and gradients
        self.fonts: dict = {}
        self.storage = self._load_storage()
        self.clock_ms = 0.0
        self.main_id = None
        self.errors = 0
        self.pad_json = ""
        self.dirty: list = []
        self.first_frame = True
        self.audio = sgba_audio.AudioEngine(self.root, self.log_fn)
        self.preload_queue: list = []
        self.ctx = quickjs.Context()
        self.ctx.set_memory_limit(96 * 1024 * 1024)
        self.ctx.set_max_stack_size(2 * 1024 * 1024)
        self._register()
        self.run_script(SHIM_PATH.read_text(encoding="utf-8"), "sgba_js_shim.js")
        self._tick = self.ctx.get("__sgba_tick")
        self._key = self.ctx.get("__sgba_key")
        self._set_pads = self.ctx.get("__sgba_set_pads")

    # -- python callables exposed to JS
    def _register(self) -> None:
        add = self.ctx.add_callable
        add("__py_log", lambda level, msg: self._js_log(level, msg))
        add("__py_new_canvas", self._new_canvas)
        add("__py_resize", self._resize_canvas)
        add("__py_set", lambda cid, name, value: self.canvases[int(cid)].set_prop(str(name), value))
        add("__py_call", self._call)
        add("__py_measure", lambda cid, text: self.canvases[int(cid)].measure(str(text)))
        add("__py_getimage", lambda cid, x, y, w, h: self.canvases[int(cid)].get_image_hex(int(x), int(y), int(w), int(h)))
        add("__py_putimage", lambda cid, x, y, w, h, s: self.canvases[int(cid)].put_image_hex(int(x), int(y), int(w), int(h), str(s)))
        add("__py_load_image", self._load_image)
        add("__py_preload", self._queue_preload)
        add("__py_storage", self._storage_op)
        add("__py_read_text", self._read_text)
        add("__py_stat", self._stat)
        add("__py_audio_register", lambda url: self.audio.register(self._resolve(str(url))))
        add("__py_audio_cmd", lambda cmd, sid, a, b: self.audio.command(str(cmd), int(sid), a, b))
        add("__py_audio_busy", lambda sid: bool(self.audio.busy(int(sid))))

    def _js_log(self, level, message) -> None:
        if level == "error":
            self.errors += 1
        self.log_fn(level, str(message))

    # -- canvases
    def _new_canvas(self, cid, w, h) -> None:
        cid = int(cid)
        surface = pygame.Surface((max(1, min(int(w), 4096)), max(1, min(int(h), 4096))), pygame.SRCALPHA)
        self.canvases[cid] = Canvas2D(self, cid, surface)

    def _resize_canvas(self, cid, w, h) -> None:
        canvas = self.canvases[int(cid)]
        w, h = max(1, min(int(w), 4096)), max(1, min(int(h), 4096))
        if (w, h) == canvas.surface.get_size():
            return
        if canvas.retained:
            canvas.surface = self._opaque_surface((w, h))
            canvas.prev = None
            canvas.pending = []
            canvas.stale = None
            self.first_frame = True
        else:
            canvas.surface = pygame.Surface((w, h), pygame.SRCALPHA)
        canvas.reset_state()
        canvas.version += 1

    def _opaque_surface(self, size) -> pygame.Surface:
        return pygame.Surface(size, 0, 16 if self.depth == 16 else 32)

    def _call(self, cid, name, *args):
        try:
            getattr(self.canvases[int(cid)], "m_" + str(name))(*args)
        except (pygame.error, ValueError, TypeError, IndexError, ZeroDivisionError, KeyError) as exc:
            self.errors += 1
            if self.errors < 30:
                self.log_fn("error", f"canvas {name}{args[:6]} failed: {type(exc).__name__}: {exc}")

    # -- drawing sources: images and canvases share one id space
    def source_size(self, sid: int):
        if sid >= IMAGE_ID_BASE:
            return self.images.size(sid)
        canvas = self.canvases.get(sid)
        return canvas.surface.get_size() if canvas else None

    def source_surface(self, sid: int):
        if sid >= IMAGE_ID_BASE:
            return self.images.get(sid)
        canvas = self.canvases.get(sid)
        return canvas.surface if canvas else None

    def source_kind(self, sid: int):
        if sid >= IMAGE_ID_BASE:
            return self.images.kind(sid) or "key"
        return "alpha"

    def source_version(self, sid: int) -> int:
        canvas = self.canvases.get(sid) if sid < IMAGE_ID_BASE else None
        return canvas.version if canvas else 0

    def is_retained(self, sid: int) -> bool:
        canvas = self.canvases.get(sid) if sid < IMAGE_ID_BASE else None
        return bool(canvas and canvas.retained)

    # -- caches used while drawing
    def variant(self, sid, image, rect, size, flip_x, flip_y, alpha: int = 255):
        """Part of a source, scaled, flipped and/or faded. Plain same-size crops are free views; the rest are cached."""
        sx, sy, sw, sh = rect
        size = tuple(size)
        if alpha < 255:
            alpha = max(0, alpha) >> 3 << 3 or 8        # 32 fade levels is plenty and keeps the cache small
        plain = (sw, sh) == size and not flip_x and not flip_y
        if plain and alpha >= 255:
            return image.subsurface(pygame.Rect(sx, sy, sw, sh))
        key = (sid, self.source_version(sid), sx, sy, sw, sh, size, flip_x, flip_y, alpha)
        piece = self.variants.get(key)
        if piece is None:
            piece = image.subsurface(pygame.Rect(sx, sy, sw, sh))
            if not plain:
                if (sw, sh) != size:
                    piece = pygame.transform.scale(piece, size)
                if flip_x or flip_y:
                    piece = pygame.transform.flip(piece, flip_x, flip_y)
            if alpha < 255:
                piece = fade(piece, alpha)
            elif piece.get_colorkey() is None and image.get_colorkey() is not None:
                piece.set_colorkey(image.get_colorkey())
            piece = self.variants.put(key, piece)
        return piece

    def rotated(self, key, build):
        """Cached rotated sprite; `build()` makes it on a miss."""
        piece = self.variants.get(key)
        if piece is None:
            piece = self.variants.put(key, build())
        return piece

    def text_image(self, text: str, font_key, rgb, alpha: int = 255) -> pygame.Surface:
        key = (text, font_key, rgb, alpha >> 3)
        image = self.texts.get(key)
        if image is None:
            image = self.font(*font_key).render(text, True, rgb)
            if alpha < 255:
                image = fade(image, alpha)
            image = self.texts.put(key, image)
        return image

    def gradient_text(self, text: str, font_key, origin, style, alpha: int) -> pygame.Surface:
        """Text filled with a gradient: a gradient tile cut to the shape of the letters."""
        key = ("gtext", text, font_key, tuple(origin), style, alpha >> 3)
        image = self.tiles.get(key)
        if image is None:
            mask = self.font(*font_key).render(text, True, (255, 255, 255))
            image = gradient_surface(mask.get_size(), tuple(origin), style).convert_alpha()
            image.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            if alpha < 255:
                image = fade(image, alpha)
            image = self.tiles.put(key, image)
        return image

    def solid_tile(self, size, rgba, mult: bool = False) -> pygame.Surface:
        key = ("solid", tuple(size), rgba, mult)
        tile = self.tiles.get(key)
        if tile is None:
            tile = pygame.Surface(size, pygame.SRCALPHA)
            tile.fill(rgba)
            if mult:
                tile = mult_ready(tile)
            tile = self.tiles.put(key, tile)
        return tile

    def gradient_tile(self, size, origin, style, alpha: float = 1.0, mult: bool = False) -> pygame.Surface:
        level = int(round(alpha * 255)) >> 3 << 3
        key = ("grad", tuple(size), tuple(origin), style, level, mult)
        tile = self.tiles.get(key)
        if tile is None:
            tile = gradient_surface(tuple(size), tuple(origin), style).convert_alpha()
            if level < 248:
                tile = fade(tile, level or 8)
            if mult:
                tile = mult_ready(tile)
            tile = self.tiles.put(key, tile)
        return tile

    def font(self, size: int, bold: bool, italic: bool) -> pygame.font.Font:
        key = (size, bold, italic)
        font = self.fonts.get(key)
        if font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            font = pygame.font.Font(None, size)
            font.set_bold(bold)
            font.set_italic(italic)
            self.fonts[key] = font
        return font

    # -- files
    def _resolve(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            return None
        path = unquote(parsed.path)
        candidate = (self.root / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _read_text(self, url):
        path = self._resolve(str(url))
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _stat(self, url):
        path = self._resolve(str(url))
        try:
            return path.stat().st_size if path is not None else -1
        except OSError:
            return -1

    def _load_image(self, src):
        src = str(src)
        try:
            if src.startswith("data:"):
                head, _, payload = src.partition(",")
                data = base64.b64decode(payload) if ";base64" in head else unquote(payload).encode("latin-1")
                result = self.images.register_surface(pygame.image.load(io.BytesIO(data)))
            else:
                path = self._resolve(src)
                result = self.images.register_file(path) if path is not None else None
            if result is None:
                return "-1,0,0"
            return f"{result[0]},{result[1]},{result[2]}"
        except (pygame.error, OSError, ValueError, binascii.Error):
            self.log_fn("warn", f"image failed: {src[:80]}")
            return "-1,0,0"

    def _queue_preload(self, urls_json) -> None:
        """A game announces images it is about to need; they are decoded one per frame, ahead of the first draw."""
        try:
            urls = json.loads(str(urls_json))
        except ValueError:
            return
        for url in urls:
            path = self._resolve(str(url))
            result = self.images.register_file(path) if path is not None else None
            if result is not None and result[0] not in self.preload_queue:
                self.preload_queue.append(result[0])

    def _preload_step(self) -> None:
        while self.preload_queue:
            sid = self.preload_queue.pop(0)
            entry = self.images.entries.get(sid)
            if entry is not None and entry["surface"] is None:
                self.images.get(sid)
                return

    # -- storage
    def _load_storage(self) -> dict:
        if self.save_path and self.save_path.is_file():
            try:
                data = json.loads(self.save_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except (OSError, ValueError):
                pass
        return {}

    def flush_storage(self) -> None:
        if not self.save_path:
            return
        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(self.storage, separators=(",", ":"))
            if len(encoded) <= 1024 * 1024:
                temp = self.save_path.with_suffix(".tmp")
                temp.write_text(encoded, encoding="utf-8")
                temp.replace(self.save_path)
        except OSError:
            pass

    def _storage_op(self, op, key, value):
        op, key = str(op), str(key)
        if op == "get":
            return self.storage.get(key)
        if op == "set":
            self.storage[key] = str(value)
            self._storage_dirty = True
        elif op == "remove":
            self.storage.pop(key, None)
            self._storage_dirty = True
        elif op == "clear":
            self.storage.clear()
            self._storage_dirty = True
        elif op == "key":
            keys = list(self.storage)
            index = int(key) if key.isdigit() else -1
            return keys[index] if 0 <= index < len(keys) else None
        elif op == "length":
            return str(len(self.storage))
        return None

    _storage_dirty = False

    # -- scripts
    def run_script(self, code: str, name: str = "script") -> bool:
        try:
            self.ctx.eval(code)
            self.pump()
            return True
        except quickjs.JSException as exc:
            self.errors += 1
            self.log_fn("error", f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let a script bring down the runtime
            self.errors += 1
            self.log_fn("error", f"{name}: {type(exc).__name__}: {exc}")
        return False

    def pump(self) -> None:
        for _ in range(10000):
            try:
                if not self.ctx.execute_pending_job():
                    break
            except quickjs.JSException as exc:
                self.errors += 1
                self.log_fn("error", f"promise job: {exc}")

    def load_page(self, index: str = "index.html") -> None:
        text = (self.root / index).read_text(encoding="utf-8")
        parser = _PageParser()
        parser.feed(text)
        self.ctx.get("__sgba_load_html")(text)
        self.pump()
        main = self.ctx.eval("(() => { const c = document.querySelector('canvas'); return c ? c.__id : -1; })()")
        self.main_id = int(main) if int(main) >= 0 else None
        if self.main_id is not None:
            canvas = self.canvases[self.main_id]
            canvas.make_retained(self._opaque_surface(canvas.surface.get_size()))
        for item in parser.items:
            if item[0] != "script":
                continue
            _kind, src, inline, stype = item
            if stype == "module":
                self.log_fn("warn", "module scripts are not supported yet")
                continue
            if src:
                if urlparse(src).scheme in ("http", "https"):
                    self.log_fn("warn", f"skipped remote script {src}")
                    continue
                path = self._resolve(src)
                if path is None:
                    self.log_fn("warn", f"missing script {src}")
                    continue
                self.run_script(path.read_text(encoding="utf-8"), src)
            elif inline.strip():
                self.run_script(inline, "inline script")
        for event in ("DOMContentLoaded", "load"):
            self.run_script(f"document.dispatchEvent(new Event('{event}')); window.dispatchEvent(new Event('{event}'));", event)

    # -- per frame
    def tick(self, dt: float) -> None:
        self.clock_ms += dt * 1000.0
        if self.main_id is not None:
            self.canvases[self.main_id].begin_frame()
        try:
            self._tick(self.clock_ms)
        except quickjs.JSException as exc:
            self.errors += 1
            self.log_fn("error", f"frame: {exc}")
        self.pump()
        self._preload_step()
        self.audio.update()
        self.stats.frames += 1
        if self._storage_dirty:
            self._storage_dirty = False
            self.flush_storage()

    def take_dirty(self) -> list:
        """Rectangles of the main canvas that changed since the last call (the whole canvas on first use)."""
        canvas = self.canvases.get(self.main_id)
        if canvas is None:
            return []
        rects = canvas.end_frame()           # composite the newest frame (earlier unshown frames were dropped)
        if self.first_frame:
            self.first_frame = False
            return [canvas.surface.get_rect()]
        return rects

    def key_event(self, kind: str, key: int) -> None:
        name, code, key_code = js_key(key)
        try:
            self._key(kind, name, code, key_code)
        except quickjs.JSException as exc:
            self.log_fn("error", f"key: {exc}")
        self.pump()

    def set_pads(self, pad_json: str) -> None:
        if pad_json != self.pad_json:
            self.pad_json = pad_json
            self.ctx.eval(f"__sgba_set_pads(JSON.parse({json.dumps(pad_json)}))")

    @property
    def main_surface(self):
        canvas = self.canvases.get(self.main_id) if self.main_id is not None else None
        return canvas.surface if canvas else None


_KEY_NAMES = {
    pygame.K_UP: ("ArrowUp", "ArrowUp", 38), pygame.K_DOWN: ("ArrowDown", "ArrowDown", 40),
    pygame.K_LEFT: ("ArrowLeft", "ArrowLeft", 37), pygame.K_RIGHT: ("ArrowRight", "ArrowRight", 39),
    pygame.K_SPACE: (" ", "Space", 32), pygame.K_RETURN: ("Enter", "Enter", 13), pygame.K_ESCAPE: ("Escape", "Escape", 27),
    pygame.K_BACKSPACE: ("Backspace", "Backspace", 8), pygame.K_TAB: ("Tab", "Tab", 9),
    pygame.K_LSHIFT: ("Shift", "ShiftLeft", 16), pygame.K_RSHIFT: ("Shift", "ShiftRight", 16),
    pygame.K_LCTRL: ("Control", "ControlLeft", 17), pygame.K_RCTRL: ("Control", "ControlRight", 17),
    pygame.K_LALT: ("Alt", "AltLeft", 18), pygame.K_RALT: ("Alt", "AltRight", 18),
}


def js_key(key: int):
    """pygame key -> (KeyboardEvent.key, .code, .keyCode)."""
    if key in _KEY_NAMES:
        return _KEY_NAMES[key]
    name = pygame.key.name(key) or ""
    if len(name) == 1 and name.isalpha():
        return (name, "Key" + name.upper(), ord(name.upper()))
    if len(name) == 1 and name.isdigit():
        return (name, "Digit" + name, ord(name))
    if name.startswith("[") and name.endswith("]"):
        return (name[1:-1], "Numpad" + name[1:-1], 96 + int(name[1:-1])) if name[1:-1].isdigit() else (name, name, key & 255)
    return (name or "Unidentified", name or "Unidentified", key & 255)


# ---------------------------------------------------------------- SGBA game adapter

class JSGame:
    """SGBA game object that hosts a browser-style JavaScript game.

    HTML the game shows over its canvas (menus, loading bars) is drawn by the engine, and every button in it is
    reachable with the pad. Optional sgba_ui.json in the game folder tunes this:
      {"navigation": "engine" | "game" | "off",   "engine" (default): D-pad moves focus, A presses, B goes back;
                                                  "game": the game already reads the pad and moves focus itself
       "hotkeys": {"select": "btn-hud-start"}}    pad action -> id of an on-screen button to press when no menu is open
    """

    REPEAT_DELAY = 0.40
    REPEAT_RATE = 0.12

    def __init__(self, root: Path, index: str = "index.html", save_dir: Path | None = None, log=None) -> None:
        root = Path(root)
        save_path = (save_dir or root) / "localStorage.json" if save_dir is not None else None
        self.config = self._load_config(root)
        try:
            self.step_hz = max(10, min(60, int(self.config.get("fps", 60))))     # simulation rate: 30 halves the work on a slow handheld
        except (TypeError, ValueError):
            self.step_hz = 60
        self.host = JSHost(root, save_path=save_path, log=log, depth=self.config["color_depth"],
                           image_budget_mb=self.config["image_budget_mb"], alpha_mode=self.config["alpha"],
                           image_depth=self.config["image_depth"], rle=self.config["rle"])
        self.host.load_page(index)
        if self.host.main_surface is None:
            raise RuntimeError("index.html has no <canvas> element")
        hide = [h for h in self.config.get("hide", []) if isinstance(h, str)]
        if hide:
            self.host.ctx.eval(f"__sgba_set_hide({json.dumps(hide)})")
        self.ui = sgba_js_ui.DomOverlay(self.host)
        self._held: dict = {}
        self._perf = {"update": 0.0, "draw": 0.0, "frame": 0.0, "worst": 0.0, "n": 0, "updates": 0, "present": 0.0}
        self._preload(root)
        gc.collect()
        gc.freeze()          # everything built so far is permanent: keep it out of later garbage-collection passes

    def _preload(self, root: Path) -> None:
        """Decode images matching the config's `preload` patterns now, so they do not stall the first frames that use them."""
        patterns = [p for p in self.config.get("preload", []) if isinstance(p, str)]
        if not patterns:
            return
        for entry in list(self.host.images.entries.values()):
            if entry["path"] is None:
                continue
            try:
                relative = Path(entry["path"]).relative_to(root.resolve()).as_posix()
            except ValueError:
                continue
            if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                self.host.images.get(next(sid for sid, e in self.host.images.entries.items() if e is entry))

    @staticmethod
    def _load_config(root: Path) -> dict:
        config = {"navigation": "engine", "hotkeys": {}, "color_depth": 32, "image_depth": 32, "rle": True,
                  "image_budget_mb": 96, "alpha": "threshold", "preload": [], "hide": [], "title": None, "fps": 60}
        path = root / "sgba_ui.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update({k: v for k, v in data.items() if k in config})
            except (OSError, ValueError):
                pass
        return config

    @property
    def logical_size(self):
        return self.host.main_surface.get_size()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            self.host.key_event("keydown", event.key)
        elif event.type == pygame.KEYUP:
            self.host.key_event("keyup", event.key)

    def resume(self) -> None:
        """Back from an engine menu drawn over the game: repaint everything and forget held buttons."""
        self.host.first_frame = True
        self._pad_state = None

    _pad_state = None
    _pad_text = ""
    _keys_flag = False

    def _keys_listened(self) -> bool:
        if not self._keys_flag:
            try:
                self._keys_flag = bool(self.host.ctx.eval("!!globalThis.__sgba_keys"))
            except Exception:
                return False
        return self._keys_flag

    def _pad_json(self, inputs, neutral: bool) -> str:
        """The standard-layout gamepad as JSON; rebuilt only when a button or stick actually changes."""
        down = inputs.down
        if self._keys_listened():           # the page reads the keyboard itself: do not also send those keys as pad buttons
            down = getattr(inputs, "down_device", down)
        state = (False,) * 20 if neutral else tuple(bool(down(a)) for a in STANDARD_BUTTONS) + (
            bool(down("rs_left")), bool(down("rs_right")), bool(down("rs_up")), bool(down("rs_down")))
        if state != self._pad_state:
            self._pad_state = state
            buttons = [{"pressed": v, "touched": v, "value": 1 if v else 0} for v in state[:16]]
            axes = [0.0, 0.0, -1.0 if state[16] else 1.0 if state[17] else 0.0, -1.0 if state[18] else 1.0 if state[19] else 0.0]
            self._pad_text = json.dumps([{"id": "SGBA standard gamepad", "index": 0, "connected": True, "mapping": "standard",
                                          "timestamp": 0, "buttons": buttons, "axes": axes}])
        return self._pad_text

    def _repeat(self, inputs, action: str, dt: float) -> bool:
        """True on the press and then at a steady rate while the action stays held."""
        if inputs.pressed(action):
            self._held[action] = -self.REPEAT_DELAY
            return True
        if inputs.down(action):
            t = self._held.get(action)
            if t is None:
                return False
            t += dt
            if t >= self.REPEAT_RATE - 1e-9:
                self._held[action] = t - self.REPEAT_RATE
                return True
            self._held[action] = t
            return False
        self._held.pop(action, None)
        return False

    _perf_every = 600          # frames between performance log lines (about ten seconds)

    def note_present(self, seconds: float) -> None:
        self._perf["present"] += seconds

    def _perf_note(self, kind: str, started: float) -> None:
        elapsed = time.perf_counter() - started
        perf = self._perf
        perf[kind] += elapsed
        if kind == "update":
            perf["updates"] += 1
        if kind == "draw":
            perf["n"] += 1
            perf["worst"] = max(perf["worst"], perf["frame"] + elapsed)
            perf["frame"] = 0.0
            if perf["n"] >= self._perf_every:
                self._perf_report()
        else:
            perf["frame"] += elapsed

    def _perf_report(self) -> None:
        perf, host = self._perf, self.host
        n = max(1, perf["n"])
        rss = ""
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    rss = f" rss {int(line.split()[1]) // 1024} MB"
        except OSError:
            pass
        store = host.images
        steps = max(1, perf["updates"])
        host.log_fn("info", f"perf: {n} frames, {perf['updates'] / n:.2f} updates/frame, update {perf['update'] / steps * 1000:.1f} ms each "
                            f"({perf['update'] / n * 1000:.1f} per frame), draw {perf['draw'] / n * 1000:.1f} ms, "
                            f"present {perf['present'] / n * 1000:.1f} ms, "
                            f"worst {perf['worst'] * 1000:.0f} ms,{rss}, images {store.decoded_count}/{len(store.entries)} "
                            f"({store.bytes // 1048576} MB, {store.evictions} evictions), composited {host.stats.replayed // n} ops/frame, "
                            f"js errors {host.errors}")
        self._perf = {"update": 0.0, "draw": 0.0, "frame": 0.0, "worst": 0.0, "n": 0, "updates": 0, "present": 0.0}

    def update(self, dt: float, inputs) -> None:
        started = time.perf_counter()
        try:
            self._update(dt, inputs)
        except Exception as exc:        # a bad frame must not end the game: log it and carry on
            self._survive("update", exc)
        finally:
            self._perf_note("update", started)

    _survived = 0

    def _survive(self, where: str, exc: Exception) -> None:
        self._survived += 1
        self.host.errors += 1
        if self._survived <= 20:
            import traceback
            self.host.log_fn("error", f"{where} failed ({type(exc).__name__}): " + "".join(traceback.format_exception(exc)).strip())
        if self._survived > 600:        # failing every frame: give up rather than spin
            raise exc

    def _update(self, dt: float, inputs) -> None:
        mode = self.config["navigation"]
        engine = mode == "engine"
        self.host.set_pads(self._pad_json(inputs, neutral=engine and self.ui.modal))
        self.host.tick(dt)
        self.ui.refresh()
        if not engine:
            return
        if self.ui.modal:
            self.ui.ensure_focus()
            if self._repeat(inputs, "up", dt):
                self.ui.move(-1)
            elif self._repeat(inputs, "down", dt):
                self.ui.move(1)
            elif inputs.pressed("a"):
                self.ui.activate()
            elif inputs.pressed("b"):
                self.ui.back()
            self.ui.refresh()
        else:
            for action, element_id in self.config["hotkeys"].items():
                if inputs.pressed(action):
                    self.ui.hotkey(element_id)

    retained = True     # the surface passed to draw() keeps its pixels between frames; draw() returns what changed

    @property
    def surface_depth(self) -> int:
        return self.host.depth

    _last_surface = None

    def draw(self, surface: pygame.Surface):
        started = time.perf_counter()
        try:
            return self._draw(surface)
        except Exception as exc:
            self._survive("draw", exc)
            return []
        finally:
            self._perf_note("draw", started)

    def _draw(self, surface: pygame.Surface):
        """Copy what changed on the game's canvas to `surface`. Returns the changed rectangles."""
        main = self.host.main_surface
        full = surface.get_rect()
        rects = self.host.take_dirty()
        if main.get_size() != surface.get_size():       # unusual: canvas and screen differ in size
            self.ui.take_changed()
            surface.blit(pygame.transform.scale(main, surface.get_size()), (0, 0))
            self.ui.draw(surface)
            return [full]
        if self.ui.take_changed() or surface is not self._last_surface:
            rects = [full]
        self._last_surface = surface
        ui_visible = self.ui.visible
        for r in rects:
            surface.blit(main, r.topleft, r)
            if ui_visible:
                self.ui.draw(surface, r)
        return rects


def create_game_for(entry_file: str, index: str = "index.html", save_dir: str | None = None) -> JSGame:
    """Helper for a package's main.py: `def create_game(): return create_game_for(__file__)`."""
    return JSGame(Path(entry_file).resolve().parent, index, Path(save_dir) if save_dir else None)
