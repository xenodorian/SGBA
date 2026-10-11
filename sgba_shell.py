"""On-screen shell for SGBA: game menu, controller setup, error screen, and log file.

Handhelds have no keyboard or terminal, so everything a person needs to start games,
map their controller, and see what went wrong is drawn on screen and driven by the pad.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

import pygame

from sgba_package import PackageError, validate_package

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "sgba.log"
CONTROLS_PATH = ROOT / "controls.json"
GAMES_DIR = ROOT / "games"
MENU_SIZE = (640, 480)
LOG_MAX_BYTES = 128 * 1024
WIZARD_IDLE_SECONDS = 120.0

# (action, prompt, optional)
WIZARD_STEPS = (
    ("up", "D-pad UP", False), ("down", "D-pad DOWN", False),
    ("left", "D-pad LEFT", False), ("right", "D-pad RIGHT", False),
    ("a", "button A (confirm)", False), ("b", "button B (back)", False),
    ("x", "button X", False), ("y", "button Y", False),
    ("l1", "L1 shoulder", False), ("r1", "R1 shoulder", False),
    ("start", "START", False), ("select", "SELECT", False),
    ("l2", "L2 trigger", True), ("r2", "R2 trigger", True),
)


def log(message: str) -> None:
    """Append a line to sgba.log next to the runtime. Never raises."""
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > LOG_MAX_BYTES:
            LOG_PATH.unlink()
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


# ---------------------------------------------------------------- controls file

def capture_source(event: pygame.event.Event):
    """Turn a pad event into a binding source, or None if it is not a deliberate press."""
    if event.type == pygame.JOYBUTTONDOWN:
        return ("button", int(event.button))
    if event.type == pygame.JOYHATMOTION:
        x, y = event.value
        hat = int(getattr(event, "hat", 0))
        if x < 0:
            return ("hat", hat, "left")
        if x > 0:
            return ("hat", hat, "right")
        if y > 0:
            return ("hat", hat, "up")
        if y < 0:
            return ("hat", hat, "down")
    if event.type == pygame.JOYAXISMOTION and abs(event.value) > 0.8:
        return ("axis", int(event.axis), "+" if event.value > 0 else "-")
    return None


def _parse_bindings(raw) -> dict:
    bindings = {}
    for action, sources in raw.items():
        parsed = []
        for source in sources:
            kind = source[0]
            if kind == "button" and len(source) == 2:
                parsed.append(("button", int(source[1])))
            elif kind == "hat" and len(source) == 3 and source[2] in ("up", "down", "left", "right"):
                parsed.append(("hat", int(source[1]), source[2]))
            elif kind == "axis" and len(source) == 3 and source[2] in ("+", "-"):
                parsed.append(("axis", int(source[1]), source[2]))
            else:
                raise ValueError(f"bad binding {source!r}")
        bindings[action] = parsed
    return bindings


def load_controls(path: Path = CONTROLS_PATH):
    """Return (exists, bindings). bindings is None when the file records 'use defaults'."""
    if not path.is_file():
        return False, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("bindings")
        return True, (None if raw is None else _parse_bindings(raw))
    except (OSError, ValueError, TypeError, AttributeError, IndexError) as exc:
        log(f"ignoring bad controls file: {exc}")
        return False, None


def save_controls(bindings, path: Path = CONTROLS_PATH) -> bool:
    data = {"version": 1, "bindings": None if bindings is None else
            {action: [list(source) for source in sources] for action, sources in bindings.items()}}
    try:
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return True
    except OSError as exc:
        log(f"cannot save controls: {exc}")
        return False


def apply_saved_controls(inputs, path: Path = CONTROLS_PATH) -> bool:
    """Apply controls.json if present and valid. Returns True when a file was found."""
    exists, bindings = load_controls(path)
    if bindings is not None:
        try:
            inputs.set_bindings(bindings)
        except ValueError as exc:
            log(f"ignoring invalid bindings: {exc}")
    return exists


# ---------------------------------------------------------------- drawing helpers

_fonts: dict = {}


def _font(size: int) -> pygame.font.Font:
    if size not in _fonts:
        if not pygame.font.get_init():
            pygame.font.init()
        _fonts[size] = pygame.font.Font(None, size)
    return _fonts[size]


def _text(surface, message, size, color, position, center=False):
    image = _font(size).render(message, True, color)
    rect = image.get_rect()
    if center:
        rect.midtop = position
    else:
        rect.topleft = position
    surface.blit(image, rect)


def _wrap(message: str, width: int) -> list:
    lines = []
    for raw in message.splitlines() or [""]:
        while len(raw) > width:
            lines.append(raw[:width])
            raw = raw[width:]
        lines.append(raw)
    return lines


# ---------------------------------------------------------------- scenes

class ControllerWizard:
    """Asks for each control in turn and records whatever the pad sends."""

    def __init__(self) -> None:
        self.step = 0
        self.bindings: dict = {}
        self.finished = False
        self.completed = False      # True only when bindings were captured
        self.keyboard_seen = False
        self.message = ""
        self._cooldown = 0.5
        self._idle = 0.0

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key != pygame.K_ESCAPE:
            self.keyboard_seen = True
            self.finished = True
            return
        if self._cooldown > 0 or self.finished:
            return
        source = capture_source(event)
        if source is None:
            return
        self._idle = 0.0
        action, _prompt, optional = WIZARD_STEPS[self.step]
        if optional and source in self.bindings.get("b", []):
            self._advance(None)     # pressing the B button skips an optional step
            return
        for used_action, sources in self.bindings.items():
            if source in sources:
                self.message = f"Already used for {used_action.upper()}. Press a different one."
                self._cooldown = 0.4
                return
        self._advance(source)

    def _advance(self, source) -> None:
        action = WIZARD_STEPS[self.step][0]
        if source is not None:
            self.bindings[action] = [source]
        self.message = ""
        self.step += 1
        self._cooldown = 0.45        # ignore the release / bounce of the press just captured
        if self.step >= len(WIZARD_STEPS):
            self.completed = True
            self.finished = True

    def update(self, dt: float, inputs) -> None:
        self._cooldown = max(0.0, self._cooldown - dt)
        self._idle += dt
        if self._idle > WIZARD_IDLE_SECONDS:
            self.finished = True

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill((12, 16, 28))
        _text(surface, "Controller setup", 56, (255, 255, 255), (320, 24), center=True)
        _action, prompt, optional = WIZARD_STEPS[min(self.step, len(WIZARD_STEPS) - 1)]
        _text(surface, f"Step {self.step + 1} of {len(WIZARD_STEPS)}", 28, (150, 160, 190), (320, 90), center=True)
        _text(surface, "Press", 44, (200, 210, 240), (320, 170), center=True)
        _text(surface, prompt, 72, (255, 220, 90), (320, 220), center=True)
        if optional:
            _text(surface, "Not on your device? Press your B button to skip.", 28, (150, 160, 190), (320, 320), center=True)
        if self.message:
            _text(surface, self.message, 30, (255, 120, 120), (320, 360), center=True)
        _text(surface, "No pad buttons respond? Using a keyboard? Press any key to skip.", 24,
              (120, 130, 160), (320, 440), center=True)


class Menu:
    """Game list. Up/Down moves, A starts, B or Start+Select quits."""

    def __init__(self, entries: list) -> None:
        self.entries = entries          # (label, kind, payload, enabled)
        self.index = 0
        self.choice = None
        self.finished = False
        self._hold = 0.0

    def update(self, dt: float, inputs) -> None:
        last = len(self.entries) - 1
        if inputs.pressed("up"):
            self.index = last if self.index == 0 else self.index - 1
        elif inputs.pressed("down"):
            self.index = 0 if self.index == last else self.index + 1
        elif inputs.pressed("left"):
            self.index = max(0, self.index - 8)
        elif inputs.pressed("right"):
            self.index = min(last, self.index + 8)
        elif inputs.pressed("a") or inputs.pressed("start"):
            label, kind, payload, enabled = self.entries[self.index]
            if enabled:
                self.choice = (kind, payload)
                self.finished = True

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill((10, 12, 24))
        _text(surface, "SGBA", 64, (255, 255, 255), (320, 18), center=True)
        visible = 9
        top = max(0, min(self.index - visible // 2, len(self.entries) - visible))
        y = 100
        for row in range(top, min(top + visible, len(self.entries))):
            label, _kind, _payload, enabled = self.entries[row]
            selected = row == self.index
            if selected:
                pygame.draw.rect(surface, (40, 60, 120), (40, y - 4, 560, 38), border_radius=6)
            color = (255, 255, 255) if selected else (170, 180, 210)
            if not enabled:
                color = (110, 110, 120)
            _text(surface, label[:40], 36, color, (60, y))
            y += 40
        if len(self.entries) > visible:
            _text(surface, f"{self.index + 1} / {len(self.entries)}", 24, (120, 130, 160), (600, 100), center=True)
        _text(surface, "Up/Down: choose    A: start    In a game: Start+Select returns here", 24,
              (120, 130, 160), (320, 450), center=True)


class ErrorScreen:
    def __init__(self, title: str, detail: str) -> None:
        self.title = title
        self.lines = _wrap(detail, 62)[-14:]
        self.finished = False
        self._age = 0.0

    def update(self, dt: float, inputs) -> None:
        self._age += dt
        if self._age > 0.6 and (inputs.pressed("a") or inputs.pressed("b") or inputs.pressed("start")):
            self.finished = True

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill((40, 8, 8))
        _text(surface, self.title, 44, (255, 200, 200), (320, 16), center=True)
        y = 80
        for line in self.lines:
            _text(surface, line, 24, (255, 235, 235), (16, y))
            y += 25
        _text(surface, "Press A to go back. Details are saved in sgba.log", 26, (255, 180, 180), (320, 446), center=True)


# ---------------------------------------------------------------- shell

def discover_games(root: Path = None) -> list:
    """Menu entries for packaged games and development game folders."""
    root = root or ROOT
    entries = []
    games_dir = root / "games"
    if games_dir.is_dir():
        for package in sorted(games_dir.glob("*.sgba")):
            try:
                entries.append((validate_package(package)["title"], "game", package, True))
            except PackageError as exc:
                log(f"invalid package {package.name}: {exc}")
                entries.append((f"{package.stem} (invalid)", "game", package, False))
        for folder in sorted(p for p in games_dir.iterdir() if p.is_dir() and p.name != "template"):
            if (folder / "main.py").is_file():
                entries.append((f"{folder.name} (dev)", "game", folder / "main.py", True))
    return entries


def _unload_game(path_added: str, modules_before: set) -> None:
    if path_added in sys.path:
        sys.path.remove(path_added)
    for name in set(sys.modules) - modules_before:
        module = sys.modules.get(name)
        module_file = getattr(module, "__file__", None) or ""
        if name == "sgba_game" or module_file.startswith(path_added):
            sys.modules.pop(name, None)


def launch_game(display, inputs, rt, path: Path) -> str:
    """Run one game from the menu. Returns "closed" if the window was closed."""
    modules_before = set(sys.modules)
    path_added = ""
    try:
        game_path = rt.resolve_game_path(path)
        path_added = str(game_path.resolve().parent)
        config = rt.load_game_config(game_path)
        game = rt.load_game(game_path)
        log(f"running {path.name}")
        return rt.run_scene(display, inputs, game, config["logical_size"])
    except (Exception, SystemExit) as exc:
        detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        log(f"game error in {path.name}:\n{detail}")
        if isinstance(exc, SystemExit):
            return "done"
        return rt.run_scene(display, inputs, ErrorScreen(f"{path.stem} stopped", detail), MENU_SIZE,
                            exit_on_combo=False)
    finally:
        if path_added:
            _unload_game(path_added, modules_before)


def run_wizard(display, inputs, rt) -> str:
    wizard = ControllerWizard()
    result = rt.run_scene(display, inputs, wizard, MENU_SIZE, exit_on_combo=False)
    if wizard.completed:
        inputs.set_bindings(wizard.bindings)
        save_controls(wizard.bindings)
        log("controller setup saved")
    elif wizard.keyboard_seen:
        save_controls(None)    # keyboard (or keyboard-emulating pad) works; do not ask again
        log("controller setup skipped: keyboard input detected")
    return result


def shell_main(display, inputs, rt) -> None:
    """Menu loop. `rt` is the runtime module (passed in to avoid importing it twice)."""
    if not load_controls()[0] and pygame.joystick.get_count() > 0:
        if run_wizard(display, inputs, rt) == "closed":
            return
    while True:
        entries = discover_games()
        if not entries:
            entries = [("No games found. Put .sgba files in the games folder.", "none", None, False)]
        entries += [("Controller setup", "setup", None, True), ("Quit", "quit", None, True)]
        menu = Menu(entries)
        if rt.run_scene(display, inputs, menu, MENU_SIZE) == "closed" or menu.choice is None:
            return
        kind, payload = menu.choice
        if kind == "quit":
            return
        if kind == "setup":
            if run_wizard(display, inputs, rt) == "closed":
                return
        elif kind == "game":
            if launch_game(display, inputs, rt, payload) == "closed":
                return
