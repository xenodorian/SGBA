import os
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame
import runtime

ROOT = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_logical_resolution_is_640_by_480(self):
        self.assertEqual(runtime.LOGICAL_SIZE, (640, 480))

    def test_keyboard_input_has_pressed_held_and_released_states(self):
        inputs = runtime.InputState()
        inputs.begin_frame()
        inputs.process(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT))
        self.assertTrue(inputs.down("left"))
        self.assertTrue(inputs.pressed("left"))
        self.assertFalse(inputs.released("left"))

        inputs.begin_frame()
        self.assertTrue(inputs.down("left"))
        self.assertFalse(inputs.pressed("left"))

        inputs.process(pygame.event.Event(pygame.KEYUP, key=pygame.K_LEFT))
        self.assertFalse(inputs.down("left"))
        self.assertTrue(inputs.released("left"))

    def test_demo_game_loads_and_draws(self):
        game = runtime.load_game(ROOT / "games" / "demo" / "main.py")
        surface = pygame.Surface(runtime.LOGICAL_SIZE)
        game.draw(surface)
        self.assertEqual(surface.get_size(), (640, 480))


if __name__ == "__main__":
    unittest.main()
