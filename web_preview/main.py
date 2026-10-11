"""Browser-only wrapper for the shared runtime and game files.

This file belongs to the web preview container. It does not change the native
runtime loop or game API. The Pages workflow stages this file beside the
repository's runtime.py and games/ directory, then packages it with pygbag.
"""
import asyncio
from pathlib import Path

import pygame
import runtime

SIZE = runtime.LOGICAL_SIZE
BG = (9, 12, 22)
INK = (245, 248, 255)
MUTED = (170, 184, 205)
CYAN = (42, 225, 220)
PINK = (255, 75, 160)
ORANGE = (255, 155, 65)

# Wrapper-only touchscreen controls. These are drawn over the display, never
# into the game's 640x480 logical surface.
CONTROLS = {
    "L2": (18, 12, 62, 26, "l2"), "L1": (86, 12, 62, 26, "l1"),
    "R1": (492, 12, 62, 26, "r1"), "R2": (560, 12, 62, 26, "r2"),
    "UP": (58, 326, 42, 36, "up"),
    "LEFT": (14, 365, 42, 36, "left"),
    "RIGHT": (102, 365, 42, 36, "right"),
    "DOWN": (58, 404, 42, 36, "down"),
    "SELECT": (220, 426, 72, 30, "select"),
    "START": (300, 426, 72, 30, "start"),
    "RS-UP": (416, 326, 34, 30, "rs_up"),
    "RS-LEFT": (380, 358, 34, 30, "rs_left"),
    "RS-RIGHT": (452, 358, 34, 30, "rs_right"),
    "RS-DOWN": (416, 390, 34, 30, "rs_down"),
    "L3": (382, 426, 50, 30, "l3"),
    "R3": (440, 426, 50, 30, "r3"),
}
FACE = {
    "Y": (570, 330, 30, 30, "y", (255, 220, 70)),
    "X": (536, 364, 30, 30, "x", PINK),
    "B": (604, 364, 30, 30, "b", ORANGE),
    "A": (570, 398, 30, 30, "a", (90, 245, 130)),
}


def available_games():
    paths = sorted(path for path in Path("games").glob("*/main.py")
                   if path.parent.name != "template")
    return paths or [Path("games/demo/main.py")]


def control_at(pos):
    x, y = pos
    for label, (bx, by, bw, bh, action) in CONTROLS.items():
        if bx <= x < bx + bw and by <= y < by + bh:
            return action
    for label, (bx, by, bw, bh, action, _color) in FACE.items():
        if bx <= x < bx + bw and by <= y < by + bh:
            return action
    return None


def draw_controls(display, font, small_font, active_actions, game_path, game_index, game_count):
    # A dim translucent panel distinguishes wrapper controls from game pixels.
    overlay = pygame.Surface(SIZE, pygame.SRCALPHA)
    pygame.draw.rect(overlay, (4, 8, 18, 188), (0, 0, 640, 46))
    pygame.draw.rect(overlay, (4, 8, 18, 188), (0, 316, 640, 164))
    display.blit(overlay, (0, 0))

    title = font.render("WEB PREVIEW", True, CYAN)
    display.blit(title, (166, 5))
    game_name = game_path.parent.name
    name = small_font.render(f"GAME {game_index + 1}/{game_count}: {game_name}", True, INK)
    display.blit(name, (166, 28))
    for x, label in ((135, "<"), (460, ">")):
        pygame.draw.rect(display, (32, 46, 68), (x, 8, 25, 30), border_radius=4)
        pygame.draw.rect(display, (104, 128, 158), (x, 8, 25, 30), 1, border_radius=4)
        image = font.render(label, True, INK)
        display.blit(image, image.get_rect(center=(x + 12, 23)))

    for label, (x, y, w, h, action) in CONTROLS.items():
        active = action in active_actions
        color = PINK if active else (32, 46, 68)
        pygame.draw.rect(display, color, (x, y, w, h), border_radius=5)
        pygame.draw.rect(display, INK if active else (104, 128, 158), (x, y, w, h), 1, border_radius=5)
        image = small_font.render(label, True, INK)
        display.blit(image, image.get_rect(center=(x + w // 2, y + h // 2)))

    for label, (x, y, w, h, action, base_color) in FACE.items():
        active = action in active_actions
        color = base_color if active else (36, 48, 67)
        pygame.draw.circle(display, color, (x + w // 2, y + h // 2), w // 2)
        pygame.draw.circle(display, INK if active else (104, 128, 158),
                           (x + w // 2, y + h // 2), w // 2, 1)
        image = small_font.render(label, True, BG if active else base_color)
        display.blit(image, image.get_rect(center=(x + w // 2, y + h // 2)))

    hint = small_font.render("D-pad + ABXY + shoulders/triggers + both stick directions + L3/R3", True, MUTED)
    display.blit(hint, (154, 382))


async def main():
    pygame.init()
    pygame.display.set_caption("R36S PyGame Runtime | Web Preview")
    display = pygame.display.set_mode(SIZE)
    logical = pygame.Surface(SIZE)
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 22)
    small_font = pygame.font.Font(None, 15)
    inputs = runtime.InputState()
    games = available_games()
    game_index = 0
    game_path = games[game_index]
    game = runtime.load_game(game_path)
    running = True
    pointers = {}
    active_actions = set()
    controls_visible = True

    def set_pointer(pointer_id, pos):
        old_action = pointers.get(pointer_id)
        new_action = control_at(pos) if controls_visible else None
        if old_action and old_action != new_action:
            inputs._set(old_action, ("touch", pointer_id), False)
            if not any(value == old_action for key, value in pointers.items() if key != pointer_id):
                active_actions.discard(old_action)
        if new_action:
            inputs._set(new_action, ("touch", pointer_id), True)
            active_actions.add(new_action)
            pointers[pointer_id] = new_action
        else:
            pointers.pop(pointer_id, None)

    def release_pointer(pointer_id):
        action = pointers.pop(pointer_id, None)
        if action:
            inputs._set(action, ("touch", pointer_id), False)
            if not any(value == action for value in pointers.values()):
                active_actions.discard(action)

    while running:
        inputs.begin_frame()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                set_pointer("mouse", event.pos)
                if event.pos[1] < 46 and 135 <= event.pos[0] <= 160:
                    release_pointer("mouse")
                    game_index = (game_index - 1) % len(games)
                    game_path = games[game_index]
                    game = runtime.load_game(game_path)
                elif event.pos[1] < 46 and 460 <= event.pos[0] <= 486:
                    release_pointer("mouse")
                    game_index = (game_index + 1) % len(games)
                    game_path = games[game_index]
                    game = runtime.load_game(game_path)
            elif event.type == pygame.MOUSEMOTION and "mouse" in pointers:
                set_pointer("mouse", event.pos)
            elif event.type == pygame.MOUSEBUTTONUP:
                release_pointer("mouse")
            elif event.type == pygame.FINGERDOWN:
                pos = (round(event.x * SIZE[0]), round(event.y * SIZE[1]))
                set_pointer(("finger", getattr(event, "finger_id", 0)), pos)
            elif event.type == pygame.FINGERMOTION:
                pos = (round(event.x * SIZE[0]), round(event.y * SIZE[1]))
                set_pointer(("finger", getattr(event, "finger_id", 0)), pos)
            elif event.type == pygame.FINGERUP:
                release_pointer(("finger", getattr(event, "finger_id", 0)))

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
        display.blit(logical, (0, 0))
        if controls_visible:
            draw_controls(display, font, small_font, active_actions, game_path, game_index, len(games))
        pygame.display.flip()
        await asyncio.sleep(0)

    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
