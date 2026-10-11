"""Small shared Pygame runtime for R36S PyGame Runtime."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pygame

LOGICAL_SIZE = (640, 480)
ACTIONS = ("up", "down", "left", "right", "a", "b", "x", "y", "start", "select")

KEY_MAP = {
    pygame.K_UP: "up", pygame.K_w: "up",
    pygame.K_DOWN: "down", pygame.K_s: "down",
    pygame.K_LEFT: "left", pygame.K_a: "left",
    pygame.K_RIGHT: "right", pygame.K_d: "right",
    pygame.K_z: "a", pygame.K_SPACE: "a",
    pygame.K_x: "b", pygame.K_LCTRL: "b",
    pygame.K_q: "x", pygame.K_e: "y",
    pygame.K_RETURN: "start", pygame.K_BACKSPACE: "select",
}

# Common SDL gamepad button order: A, B, X, Y, Back, Guide, Start.
BUTTON_MAP = {
    0: "a", 1: "b", 2: "x", 3: "y", 4: "select", 6: "start",
}


class InputState:
    """Frame-based keyboard and controller input shared by every game."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._pressed: set[str] = set()
        self._released: set[str] = set()
        self._joysticks: dict[int, pygame.joystick.JoystickType] = {}

    def begin_frame(self) -> None:
        self._pressed.clear()
        self._released.clear()

    def _set(self, action: str, is_down: bool) -> None:
        if is_down and action not in self._held:
            self._held.add(action)
            self._pressed.add(action)
        elif not is_down and action in self._held:
            self._held.remove(action)
            self._released.add(action)

    def process(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key in KEY_MAP:
            self._set(KEY_MAP[event.key], True)
        elif event.type == pygame.KEYUP and event.key in KEY_MAP:
            self._set(KEY_MAP[event.key], False)
        elif event.type == pygame.JOYDEVICEADDED:
            joystick = pygame.joystick.Joystick(event.device_index)
            joystick.init()
            self._joysticks[joystick.get_instance_id()] = joystick
        elif event.type == pygame.JOYDEVICEREMOVED:
            self._joysticks.pop(event.instance_id, None)
        elif event.type == pygame.JOYBUTTONDOWN and event.button in BUTTON_MAP:
            self._set(BUTTON_MAP[event.button], True)
        elif event.type == pygame.JOYBUTTONUP and event.button in BUTTON_MAP:
            self._set(BUTTON_MAP[event.button], False)
        elif event.type == pygame.JOYHATMOTION:
            x, y = event.value
            for action, down in (("left", x < 0), ("right", x > 0),
                                 ("up", y > 0), ("down", y < 0)):
                self._set(action, down)

    def down(self, action: str) -> bool:
        return action in self._held

    def pressed(self, action: str) -> bool:
        return action in self._pressed

    def released(self, action: str) -> bool:
        return action in self._released


def load_game(path: Path):
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


def main() -> int:
    parser = argparse.ArgumentParser(description="R36S PyGame Runtime")
    parser.add_argument("game", nargs="?", default="games/demo/main.py",
                        help="Path to a game Python file")
    parser.add_argument("--fullscreen", action="store_true",
                        help="Run fullscreen while preserving 4:3 aspect ratio")
    args = parser.parse_args()

    game_path = Path(args.game).resolve()
    if not game_path.is_file():
        parser.error(f"game file not found: {game_path}")

    pygame.init()
    try:
        flags = pygame.FULLSCREEN if args.fullscreen else 0
        window_size = (960, 720) if not args.fullscreen else (0, 0)
        display = pygame.display.set_mode(window_size, flags)
        pygame.display.set_caption("R36S PyGame Runtime")
        logical = pygame.Surface(LOGICAL_SIZE)
        clock = pygame.time.Clock()
        inputs = InputState()
        running = True
        game = load_game(game_path)

        while running:
            inputs.begin_frame()
            events = pygame.event.get()
            for event in events:
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    running = False
                inputs.process(event)
                handler = getattr(game, "handle_event", None)
                if callable(handler):
                    handler(event)

            dt = min(clock.tick(60) / 1000.0, 0.05)
            update = getattr(game, "update", None)
            if callable(update):
                update(dt, inputs)
            logical.fill((0, 0, 0))
            game.draw(logical)

            display.fill((0, 0, 0))
            scale = min(display.get_width() / LOGICAL_SIZE[0],
                        display.get_height() / LOGICAL_SIZE[1])
            scaled_size = (max(1, round(LOGICAL_SIZE[0] * scale)),
                           max(1, round(LOGICAL_SIZE[1] * scale)))
            frame = pygame.transform.scale(logical, scaled_size)
            display.blit(frame, ((display.get_width() - scaled_size[0]) // 2,
                                 (display.get_height() - scaled_size[1]) // 2))
            pygame.display.flip()
    finally:
        pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
