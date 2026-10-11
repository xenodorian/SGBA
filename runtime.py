"""Small, resource-conscious Pygame runtime for SGBA. Device-agnostic: any SDL2 display and keyboard/gamepad."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

import pygame

from sgba_package import PackageError, resolve_game_path, validate_package

DEFAULT_LOGICAL_SIZE = (640, 480)
ALLOWED_LOGICAL_SIZES = {(240, 160), (320, 240), (640, 480)}
SIMULATION_HZ = 60
FIXED_DT = 1.0 / SIMULATION_HZ
MAX_CATCH_UP_STEPS = 5
MAX_FRAME_TIME = 0.25
DEFAULT_ASSET_BUDGET_MB = 16
MAX_IMAGE_DIMENSION = 512
LOGICAL_SIZE = DEFAULT_LOGICAL_SIZE
ACTIONS = ("up", "down", "left", "right", "a", "b", "x", "y", "start", "select", "guide", "l1", "r1", "l2", "r2", "l3", "r3", "rs_up", "rs_down", "rs_left", "rs_right")

KEY_MAP = {
    pygame.K_UP: "up", pygame.K_w: "up",
    pygame.K_DOWN: "down", pygame.K_s: "down",
    pygame.K_LEFT: "left", pygame.K_a: "left",
    pygame.K_RIGHT: "right", pygame.K_d: "right",
    pygame.K_z: "a", pygame.K_SPACE: "a",
    pygame.K_x: "b", pygame.K_LCTRL: "b",
    pygame.K_q: "x", pygame.K_e: "y",
    pygame.K_RETURN: "start", pygame.K_BACKSPACE: "select",
    pygame.K_7: "guide", pygame.K_1: "l1", pygame.K_2: "r1",
    pygame.K_3: "l2", pygame.K_4: "r2", pygame.K_5: "l3", pygame.K_6: "r3",
    pygame.K_j: "rs_left", pygame.K_l: "rs_right", pygame.K_i: "rs_up", pygame.K_k: "rs_down",
}
BUTTON_MAP = {
    0: "a", 1: "b", 2: "x", 3: "y", 4: "select", 5: "guide", 6: "start",
    7: "l3", 8: "r3", 9: "l1", 10: "r1",
}


def load_game_config(game_path: Path) -> dict:
    """Load and validate optional game.json next to a game's entry file."""
    config = {
        "logical_size": list(DEFAULT_LOGICAL_SIZE),
        "asset_budget_mb": DEFAULT_ASSET_BUDGET_MB,
        "max_image_dimension": MAX_IMAGE_DIMENSION,
        "target_fps": SIMULATION_HZ,
    }
    config_path = game_path.parent / "game.json"
    if config_path.is_file():
        try:
            supplied = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Invalid game manifest {config_path}: {exc}") from exc
        if not isinstance(supplied, dict):
            raise RuntimeError("game.json must contain a JSON object")
        unknown = set(supplied) - set(config)
        if unknown:
            raise RuntimeError(f"Unsupported game.json fields: {', '.join(sorted(unknown))}")
        config.update(supplied)
    try:
        size = tuple(int(value) for value in config["logical_size"])
    except (TypeError, ValueError):
        raise RuntimeError("logical_size must be a supported [width, height] pair")
    if size not in ALLOWED_LOGICAL_SIZES:
        raise RuntimeError(f"logical_size must be one of {sorted(ALLOWED_LOGICAL_SIZES)}")
    config["logical_size"] = size
    if config["target_fps"] != SIMULATION_HZ:
        raise RuntimeError("target_fps is fixed at 60")
    if not isinstance(config["asset_budget_mb"], int) or not 1 <= config["asset_budget_mb"] <= 32:
        raise RuntimeError("asset_budget_mb must be an integer from 1 to 32")
    if not isinstance(config["max_image_dimension"], int) or not 16 <= config["max_image_dimension"] <= 1024:
        raise RuntimeError("max_image_dimension must be an integer from 16 to 1024")
    return config


class AssetStore:
    """Per-game asset cache with decoded-image memory and dimension limits."""
    def __init__(self, game_dir: Path, budget_mb: int = DEFAULT_ASSET_BUDGET_MB,
                 max_image_dimension: int = MAX_IMAGE_DIMENSION) -> None:
        self.game_dir = Path(game_dir).resolve()
        self.budget_bytes = budget_mb * 1024 * 1024
        self.max_image_dimension = max_image_dimension
        self._images: dict[Path, pygame.Surface] = {}
        self._fonts: dict[tuple[str | None, int], pygame.font.Font] = {}
        self._sounds: dict[Path, pygame.mixer.Sound] = {}
        self._image_bytes = 0

    def _path(self, relative_path: str | Path) -> Path:
        path = (self.game_dir / relative_path).resolve()
        if path != self.game_dir and self.game_dir not in path.parents:
            raise ValueError("Asset paths must remain inside the game folder")
        if not path.is_file():
            raise FileNotFoundError(f"Game asset not found: {path}")
        return path

    def image(self, relative_path: str | Path, *, alpha: bool = True) -> pygame.Surface:
        path = self._path(relative_path)
        if path in self._images:
            return self._images[path]
        image = pygame.image.load(str(path))
        width, height = image.get_size()
        if max(width, height) > self.max_image_dimension:
            raise ValueError(f"Image {path.name} is {width}x{height}; limit is {self.max_image_dimension}x{self.max_image_dimension}")
        if pygame.display.get_surface():
            image = image.convert_alpha() if alpha else image.convert()
        else:
            image = image.copy()
        estimate = image.get_width() * image.get_height() * max(1, image.get_bytesize())
        if self._image_bytes + estimate > self.budget_bytes:
            raise MemoryError(f"Decoded image budget exceeded ({self.budget_bytes // (1024 * 1024)} MiB)")
        self._images[path] = image
        self._image_bytes += estimate
        return image

    def font(self, size: int, relative_path: str | Path | None = None) -> pygame.font.Font:
        if not 8 <= int(size) <= 96:
            raise ValueError("Font size must be between 8 and 96")
        path = str(self._path(relative_path)) if relative_path is not None else None
        key = (path, int(size))
        if key not in self._fonts:
            self._fonts[key] = pygame.font.Font(path, int(size))
        return self._fonts[key]

    def sound(self, relative_path: str | Path) -> pygame.mixer.Sound | None:
        """Return a cached sound, or None if optional audio cannot initialize."""
        path = self._path(relative_path)
        if path in self._sounds:
            return self._sounds[path]
        if not pygame.mixer.get_init():
            try:
                pygame.mixer.init()
            except pygame.error:
                return None
        sound = pygame.mixer.Sound(str(path))
        self._sounds[path] = sound
        return sound

    @property
    def image_memory_bytes(self) -> int:
        return self._image_bytes



class SpriteSheet:
    """Row-major sprite-sheet view with optional camera offset and clipping."""
    def __init__(self, image: pygame.Surface, frame_size: tuple[int, int],
                 margin: int = 0, spacing: int = 0) -> None:
        self.image = image
        self.frame_width, self.frame_height = frame_size
        if self.frame_width <= 0 or self.frame_height <= 0 or margin < 0 or spacing < 0:
            raise ValueError("Frame sizes must be positive; margin and spacing cannot be negative")
        self.frames: list[pygame.Surface] = []
        y = margin
        while y + self.frame_height <= image.get_height() - margin:
            x = margin
            while x + self.frame_width <= image.get_width() - margin:
                self.frames.append(image.subsurface((x, y, self.frame_width, self.frame_height)))
                x += self.frame_width + spacing
            y += self.frame_height + spacing
        if not self.frames:
            raise ValueError("Sprite sheet contains no complete frames")

    def frame(self, index: int) -> pygame.Surface:
        try:
            return self.frames[index]
        except IndexError as exc:
            raise IndexError(f"Sprite frame index out of range: {index}") from exc

    def draw(self, surface: pygame.Surface, index: int, position: tuple[float, float],
             camera: tuple[float, float] = (0, 0),
             clip: pygame.Rect | None = None) -> None:
        old_clip = surface.get_clip()
        if clip is not None:
            surface.set_clip(old_clip.clip(clip))
        surface.blit(self.frame(index), (round(position[0] - camera[0]),
                                         round(position[1] - camera[1])))
        surface.set_clip(old_clip)



class Animation:
    """Small frame-index animation advanced by fixed simulation time."""
    def __init__(self, frames: list[int] | tuple[int, ...],
                 frame_duration: float = 0.1, loop: bool = True) -> None:
        if not frames or frame_duration <= 0:
            raise ValueError("Animation needs frames and a positive frame duration")
        self.frames = tuple(int(frame) for frame in frames)
        self.frame_duration = float(frame_duration)
        self.loop = bool(loop)
        self.elapsed = 0.0
        self.position = 0
        self.finished = False

    @property
    def frame(self) -> int:
        return self.frames[self.position]

    def reset(self) -> None:
        self.elapsed = 0.0
        self.position = 0
        self.finished = False

    def update(self, dt: float) -> None:
        if self.finished or dt <= 0:
            return
        self.elapsed += dt
        steps = int(self.elapsed / self.frame_duration)
        if steps:
            self.elapsed -= steps * self.frame_duration
            next_position = self.position + steps
            if self.loop:
                self.position = next_position % len(self.frames)
            elif next_position >= len(self.frames):
                self.position = len(self.frames) - 1
                self.finished = True
            else:
                self.position = next_position


class TileMap:
    """Compact integer tile grid that draws only the visible camera region.

    Tile index -1 is empty. All other indices refer to SpriteSheet frames.
    """
    def __init__(self, rows: list[list[int]] | tuple[tuple[int, ...], ...],
                 tileset: SpriteSheet) -> None:
        if not rows or not rows[0]:
            raise ValueError("Tile map must contain at least one tile")
        width = len(rows[0])
        if any(len(row) != width for row in rows):
            raise ValueError("Tile map rows must have equal width")
        for row in rows:
            for tile in row:
                if tile != -1 and not 0 <= tile < len(tileset.frames):
                    raise ValueError(f"Invalid tile index: {tile}")
        self.rows = tuple(tuple(int(tile) for tile in row) for row in rows)
        self.tileset = tileset
        self.tile_width = tileset.frame_width
        self.tile_height = tileset.frame_height
        self.width = width
        self.height = len(rows)
        self.pixel_width = self.width * self.tile_width
        self.pixel_height = self.height * self.tile_height

    def draw(self, surface: pygame.Surface, camera: tuple[float, float] = (0, 0),
             clip: pygame.Rect | None = None) -> None:
        old_clip = surface.get_clip()
        visible = old_clip if clip is None else old_clip.clip(clip)
        surface.set_clip(visible)
        world_left = camera[0] + visible.left
        world_top = camera[1] + visible.top
        world_right = camera[0] + visible.right
        world_bottom = camera[1] + visible.bottom
        left = max(0, int(world_left // self.tile_width))
        top = max(0, int(world_top // self.tile_height))
        right = min(self.width, int((world_right - 1) // self.tile_width) + 1)
        bottom = min(self.height, int((world_bottom - 1) // self.tile_height) + 1)
        for row_index in range(top, bottom):
            row = self.rows[row_index]
            for column in range(left, right):
                tile = row[column]
                if tile >= 0:
                    surface.blit(self.tileset.frame(tile),
                                 (round(column * self.tile_width - camera[0]),
                                  round(row_index * self.tile_height - camera[1])))
        surface.set_clip(old_clip)


class SceneManager:
    """Small explicit scene registry; scenes implement update/draw and optionally handle_event."""
    def __init__(self) -> None:
        self._scenes: dict[str, object] = {}
        self.current_name: str | None = None

    @property
    def current(self):
        return self._scenes.get(self.current_name) if self.current_name is not None else None

    def add(self, name: str, scene: object) -> None:
        if not name or name in self._scenes:
            raise ValueError(f"Scene name must be non-empty and unique: {name!r}")
        if not callable(getattr(scene, "draw", None)):
            raise ValueError("Scene must implement draw(surface)")
        self._scenes[name] = scene
        if self.current_name is None:
            self.current_name = name

    def switch(self, name: str) -> None:
        if name not in self._scenes:
            raise KeyError(f"Unknown scene: {name}")
        self.current_name = name

    def handle_event(self, event: pygame.event.Event) -> None:
        handler = getattr(self.current, "handle_event", None)
        if callable(handler):
            handler(event)

    def update(self, dt: float, inputs: "InputState") -> None:
        updater = getattr(self.current, "update", None)
        if callable(updater):
            updater(dt, inputs)

    def draw(self, surface: pygame.Surface) -> None:
        if self.current is None:
            raise RuntimeError("SceneManager has no current scene")
        self.current.draw(surface)


class SaveStore:
    """Small JSON save file at an explicit caller-chosen path, capped at 256 KiB."""
    MAX_BYTES = 256 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def load(self, default: dict | None = None) -> dict:
        if not self.path.exists():
            return dict(default or {})
        if self.path.stat().st_size > self.MAX_BYTES:
            raise ValueError("Save file exceeds 256 KiB")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read save file: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Save data must be a JSON object")
        return data

    def save(self, data: dict) -> None:
        if not isinstance(data, dict):
            raise TypeError("Save data must be a dictionary")
        encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(encoded) > self.MAX_BYTES:
            raise ValueError("Save data exceeds 256 KiB")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(encoded)
        temporary.replace(self.path)


def draw_sprite(surface: pygame.Surface, image: pygame.Surface,
                position: tuple[float, float],
                camera: tuple[float, float] = (0, 0),
                clip: pygame.Rect | None = None) -> None:
    """Blit one image with an optional camera offset and destination clip."""
    old_clip = surface.get_clip()
    if clip is not None:
        surface.set_clip(old_clip.clip(clip))
    surface.blit(image, (round(position[0] - camera[0]), round(position[1] - camera[1])))
    surface.set_clip(old_clip)


class InputState:
    """Source-aware logical input; edges persist until a simulation tick consumes them."""
    def __init__(self) -> None:
        self._sources: dict[str, set[tuple]] = {action: set() for action in ACTIONS}
        self._pressed: set[str] = set()
        self._released: set[str] = set()
        self._joysticks: dict[int, object] = {}
        self._bound: dict[str, dict] | None = None  # custom pad bindings, see set_bindings

    def set_bindings(self, bindings: dict | None) -> None:
        """Use custom pad bindings {action: [source, ...]} in place of the default pad map.

        A source is ("button", n), ("hat", index, "up|down|left|right") or
        ("axis", index, "+" or "-"). None restores the defaults. Keyboard is unaffected.
        """
        if bindings is None:
            self._bound = None
            return
        lookup: dict[str, dict] = {"button": {}, "hat": {}, "axis": {}}
        for action, sources in bindings.items():
            self._validate_action(action)
            for source in sources:
                kind = source[0]
                if kind not in lookup:
                    raise ValueError(f"Unknown binding type: {kind}")
                lookup[kind].setdefault(tuple(source[1:]), set()).add(action)
        self._bound = lookup

    def _process_bound(self, event: pygame.event.Event) -> bool:
        """Handle a pad event with custom bindings. Returns True if the event type was pad input."""
        assert self._bound is not None
        instance_id = self._instance_id(event)
        if event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            down = event.type == pygame.JOYBUTTONDOWN
            for action in self._bound["button"].get((event.button,), ()):
                self._set(action, ("joybutton", instance_id, event.button), down)
        elif event.type == pygame.JOYHATMOTION:
            hat = getattr(event, "hat", 0)
            x, y = event.value
            states = {"left": x < 0, "right": x > 0, "up": y > 0, "down": y < 0}
            for (bound_hat, direction), actions in self._bound["hat"].items():
                if bound_hat == hat:
                    for action in actions:
                        self._set(action, ("joyhat", instance_id, hat, direction), states[direction])
        elif event.type == pygame.JOYAXISMOTION:
            for (axis, sign), actions in self._bound["axis"].items():
                if axis == event.axis:
                    down = event.value > 0.5 if sign == "+" else event.value < -0.5
                    for action in actions:
                        self._set(action, ("joyaxis", instance_id, axis, sign), down)
        else:
            return False
        return True

    def begin_frame(self) -> None:
        # Do not clear edges here: a render frame can occur without a simulation tick.
        pass

    def consume_edges(self) -> None:
        self._pressed.clear()
        self._released.clear()

    def _set(self, action: str, source: tuple, is_down: bool) -> None:
        sources = self._sources[action]
        was_down = bool(sources)
        if is_down:
            sources.add(source)
        else:
            sources.discard(source)
        now_down = bool(sources)
        if now_down and not was_down:
            self._pressed.add(action)
        elif was_down and not now_down:
            self._released.add(action)

    @staticmethod
    def _joystick_id(joystick: pygame.joystick.Joystick) -> int:
        getter = getattr(joystick, "get_instance_id", None) or joystick.get_id
        return int(getter())

    @staticmethod
    def _instance_id(event: pygame.event.Event) -> int:
        return int(getattr(event, "instance_id", getattr(event, "joy", -1)))

    def _remove_joystick_sources(self, instance_id: int) -> None:
        for action, sources in self._sources.items():
            for source in tuple(sources):
                if len(source) > 1 and source[0] in ("joybutton", "joyhat", "joyaxis") and source[1] == instance_id:
                    self._set(action, source, False)

    def process(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key in KEY_MAP:
            self._set(KEY_MAP[event.key], ("key", event.key), True)
        elif event.type == pygame.KEYUP and event.key in KEY_MAP:
            self._set(KEY_MAP[event.key], ("key", event.key), False)
        elif event.type == getattr(pygame, "JOYDEVICEADDED", None):
            joystick = pygame.joystick.Joystick(event.device_index)
            joystick.init()
            self._joysticks[self._joystick_id(joystick)] = joystick
        elif event.type == getattr(pygame, "JOYDEVICEREMOVED", None):
            instance_id = self._instance_id(event)
            self._remove_joystick_sources(instance_id)
            self._joysticks.pop(instance_id, None)
        elif self._bound is not None and self._process_bound(event):
            return
        elif event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            action = BUTTON_MAP.get(event.button)
            if action is not None:
                source = ("joybutton", self._instance_id(event), event.button)
                self._set(action, source, event.type == pygame.JOYBUTTONDOWN)
        elif event.type == pygame.JOYHATMOTION:
            instance_id = self._instance_id(event)
            hat = getattr(event, "hat", 0)
            x, y = event.value
            for action, down in (("left", x < 0), ("right", x > 0), ("up", y > 0), ("down", y < 0)):
                self._set(action, ("joyhat", instance_id, hat, action), down)
        elif event.type == pygame.JOYAXISMOTION and event.axis in (0, 1, 2, 3, 4, 5):
            instance_id = self._instance_id(event)
            value = event.value
            if event.axis == 0:
                states = (("left", value < -0.5), ("right", value > 0.5))
            elif event.axis == 1:
                states = (("up", value < -0.5), ("down", value > 0.5))
            elif event.axis == 2:
                states = (("rs_left", value < -0.5), ("rs_right", value > 0.5))
            elif event.axis == 3:
                states = (("rs_up", value < -0.5), ("rs_down", value > 0.5))
            elif event.axis == 4:
                states = (("l2", value > 0.5),)
            else:
                states = (("r2", value > 0.5),)
            for action, down in states:
                self._set(action, ("joyaxis", instance_id, event.axis, action), down)

    def down(self, action: str) -> bool:
        self._validate_action(action)
        return bool(self._sources[action])

    def pressed(self, action: str) -> bool:
        self._validate_action(action)
        return action in self._pressed

    def released(self, action: str) -> bool:
        self._validate_action(action)
        return action in self._released

    @staticmethod
    def _validate_action(action: str) -> None:
        if action not in ACTIONS:
            raise ValueError(f"Unknown input action: {action}")


def load_game(path: Path):
    game_dir = str(Path(path).resolve().parent)
    if game_dir not in sys.path:
        sys.path.insert(0, game_dir)
    spec = importlib.util.spec_from_file_location("sgba_game", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load game file: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    factory = getattr(module, "create_game", None)
    if not callable(factory):
        raise RuntimeError("Game file must define create_game()")
    game = factory()
    if not callable(getattr(game, "draw", None)):
        raise RuntimeError("Game object must implement draw(surface)")
    return game


def run_scene(display: pygame.Surface, inputs: "InputState", game, logical_size: tuple,
              exit_on_combo: bool = True) -> str:
    """Run one game or shell scene until it ends. Returns "closed" (window closed) or "done".

    A scene ends when it sets a truthy `finished` attribute, on Esc, or on Start+Select.
    """
    update = getattr(game, "update", None)
    logical = pygame.Surface(logical_size, depth=32)
    clock = pygame.time.Clock()
    accumulator = 0.0
    inputs.consume_edges()  # presses from the previous scene (e.g. Start in Start+Select) must not leak in
    combo_armed = False  # a held Start+Select from the previous scene must be released first
    while True:
        inputs.begin_frame()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "closed"
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return "done"
            inputs.process(event)
            handler = getattr(game, "handle_event", None)
            if callable(handler):
                handler(event)
        combo = inputs.down("start") and inputs.down("select")
        if not combo:
            combo_armed = True
        elif exit_on_combo and combo_armed:
            return "done"

        elapsed = min(clock.tick(SIMULATION_HZ) / 1000.0, MAX_FRAME_TIME)
        accumulator = min(accumulator + elapsed, FIXED_DT * MAX_CATCH_UP_STEPS)
        first_update = True
        steps = 0
        while accumulator >= FIXED_DT and steps < MAX_CATCH_UP_STEPS:
            if callable(update):
                update(FIXED_DT, inputs)
            if first_update:
                inputs.consume_edges()
                first_update = False
            accumulator -= FIXED_DT
            steps += 1
        if steps == MAX_CATCH_UP_STEPS and accumulator >= FIXED_DT:
            accumulator %= FIXED_DT
        if getattr(game, "finished", False):
            return "done"

        logical.fill((0, 0, 0))
        game.draw(logical)
        display.fill((0, 0, 0))
        scale = min(display.get_width() / logical_size[0], display.get_height() / logical_size[1])
        scaled_size = (max(1, round(logical_size[0] * scale)), max(1, round(logical_size[1] * scale)))
        frame = pygame.transform.scale(logical, scaled_size)
        display.blit(frame, ((display.get_width() - scaled_size[0]) // 2,
                             (display.get_height() - scaled_size[1]) // 2))
        pygame.display.flip()


def open_joysticks(inputs: "InputState") -> None:
    for device_index in range(pygame.joystick.get_count()):
        try:
            joystick = pygame.joystick.Joystick(device_index)
            joystick.init()
            inputs._joysticks[inputs._joystick_id(joystick)] = joystick
        except pygame.error:
            continue


VIDEO_DRIVER_FALLBACKS = ("kmsdrm", "x11", "wayland", "fbcon", "directfb")


def open_display(fullscreen: bool, log=lambda message: None) -> pygame.Surface:
    """Open the window. Let SDL choose its driver first; if that fails, try the others in turn.

    Handhelds run without a desktop, so the right driver varies by device and SDL build.
    A driver the user set with SDL_VIDEODRIVER is respected and tried alone.
    """
    flags = pygame.FULLSCREEN if fullscreen else 0
    size = (0, 0) if fullscreen else (960, 720)
    explicit = os.environ.get("SDL_VIDEODRIVER")
    candidates = [explicit] if explicit else [None] + list(VIDEO_DRIVER_FALLBACKS)
    last_error = None
    for driver in candidates:
        try:
            pygame.display.quit()
            if driver is None:
                os.environ.pop("SDL_VIDEODRIVER", None)
            else:
                os.environ["SDL_VIDEODRIVER"] = driver
            pygame.display.init()
            return pygame.display.set_mode(size, flags)
        except pygame.error as exc:
            last_error = exc
            log(f"video driver {driver or 'auto'} failed: {exc}")
    raise RuntimeError(f"No working video driver ({last_error})")


def main() -> int:
    parser = argparse.ArgumentParser(description="SGBA lightweight Pygame runtime")
    parser.add_argument("game", nargs="?", default=None,
                        help="Path to a .sgba package or a development game .py file. "
                             "Without it the on-screen game menu starts.")
    parser.add_argument("--fullscreen", action="store_true", help="Run fullscreen while preserving aspect ratio")
    args = parser.parse_args()
    game_title = "SGBA"
    direct = None
    if args.game is not None:
        try:
            if Path(args.game).suffix.lower() == ".sgba":
                game_title = validate_package(args.game)["title"]
            direct = resolve_game_path(args.game)
            config = load_game_config(direct)
        except (OSError, PackageError, RuntimeError) as exc:
            parser.error(str(exc))

    import sgba_shell
    sgba_shell.log(f"start python {sys.version.split()[0]} pygame {pygame.version.ver}")
    pygame.init()
    try:
        inputs = InputState()
        open_joysticks(inputs)
        sgba_shell.apply_saved_controls(inputs)
        display = open_display(args.fullscreen, sgba_shell.log)
        sgba_shell.log(f"video driver {pygame.display.get_driver()} size {display.get_size()} "
                       f"joysticks {pygame.joystick.get_count()}")
        pygame.display.set_caption(f"SGBA: {game_title}")
        if direct is not None:
            game = load_game(direct)
            run_scene(display, inputs, game, config["logical_size"])
        else:
            sgba_shell.shell_main(display, inputs, sys.modules[__name__])
    finally:
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
