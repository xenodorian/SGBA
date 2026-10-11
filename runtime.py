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

# Common SDL joystick button order. Device-specific mapping may be needed
# after checking the actual R36S firmware and controller event layout.
BUTTON_MAP = {
    0: "a", 1: "b", 2: "x", 3: "y", 4: "select", 6: "start",
}


class InputState:
    """Frame-based input that combines keyboard, buttons, hats, and stick axes."""

    def __init__(self) -> None:
        # Each action tracks its individual physical input sources. An action
        # remains held until every source contributing to it has been released.
        self._sources: dict[str, set[tuple]] = {action: set() for action in ACTIONS}
        self._pressed: set[str] = set()
        self._released: set[str] = set()
        self._joysticks: dict[int, object] = {}

    def begin_frame(self) -> None:
        self._pressed.clear()
        self._released.clear()

    def _set(self, action: str, source: tuple, is_down: bool) -> None:
        sources = self._sources[action]
        was_down = bool(sources)
        if is_down:
            sources.add(source)
        else:
            sources.discard(source)
        is_down_now = bool(sources)
        if is_down_now and not was_down:
            self._pressed.add(action)
        elif was_down and not is_down_now:
            self._released.add(action)

    @staticmethod
    def _instance_id(event: pygame.event.Event) -> int:
        # Pygame 2 supplies instance_id; joy is retained as a fallback.
        return int(getattr(event, "instance_id", getattr(event, "joy", -1)))

    def _remove_joystick_sources(self, instance_id: int) -> None:
        for action, sources in self._sources.items():
            for source in tuple(sources):
                if len(source) > 1 and source[0] in ("joybutton", "joyhat", "joyaxis") and source[1] == instance_id:
                    self._set(action, source, False)

    def process(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN and event.key in KEY_MAP:
            action = KEY_MAP[event.key]
            self._set(action, ("key", event.key), True)
        elif event.type == pygame.KEYUP and event.key in KEY_MAP:
            action = KEY_MAP[event.key]
            self._set(action, ("key", event.key), False)
        elif event.type == pygame.JOYDEVICEADDED:
            joystick = pygame.joystick.Joystick(event.device_index)
            joystick.init()
            self._joysticks[joystick.get_instance_id()] = joystick
        elif event.type == pygame.JOYDEVICEREMOVED:
            instance_id = self._instance_id(event)
            self._remove_joystick_sources(instance_id)
            self._joysticks.pop(instance_id, None)
        elif event.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
            action = BUTTON_MAP.get(event.button)
            if action is not None:
                source = ("joybutton", self._instance_id(event), event.button)
                self._set(action, source, event.type == pygame.JOYBUTTONDOWN)
        elif event.type == pygame.JOYHATMOTION:
            instance_id = self._instance_id(event)
            hat = getattr(event, "hat", 0)
            x, y = event.value
            for action, down in (("left", x < 0), ("right", x > 0),
                                 ("up", y > 0), ("down", y < 0)):
                source = ("joyhat", instance_id, hat, action)
                self._set(action, source, down)
        elif event.type == pygame.JOYAXISMOTION and event.axis in (0, 1):
            instance_id = self._instance_id(event)
            value = event.value
            if event.axis == 0:
                states = (("left", value < -0.5), ("right", value > 0.5))
            else:
                states = (("up", value < -0.5), ("down", value > 0.5))
            for action, down in states:
                source = ("joyaxis", instance_id, event.axis, action)
                self._set(action, source, down)

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
        # Initialize already-connected controllers as well as handling hotplug.
        inputs = InputState()
        for device_index in range(pygame.joystick.get_count()):
            joystick = pygame.joystick.Joystick(device_index)
            joystick.init()
            inputs._joysticks[joystick.get_instance_id()] = joystick

        flags = pygame.FULLSCREEN if args.fullscreen else 0
        window_size = (960, 720) if not args.fullscreen else (0, 0)
        display = pygame.display.set_mode(window_size, flags)
        pygame.display.set_caption("R36S PyGame Runtime")
        logical = pygame.Surface(LOGICAL_SIZE)
        clock = pygame.time.Clock()
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
