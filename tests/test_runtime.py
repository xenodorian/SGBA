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

    def test_keyboard_and_controller_sources_do_not_cancel_each_other(self):
        inputs = runtime.InputState()
        inputs.begin_frame()
        inputs.process(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_z))
        inputs.process(pygame.event.Event(
            pygame.JOYBUTTONDOWN, instance_id=7, button=0
        ))
        self.assertTrue(inputs.down("a"))

        inputs.begin_frame()
        inputs.process(pygame.event.Event(pygame.KEYUP, key=pygame.K_z))
        self.assertTrue(inputs.down("a"))
        self.assertFalse(inputs.released("a"))

        inputs.process(pygame.event.Event(
            pygame.JOYBUTTONUP, instance_id=7, button=0
        ))
        self.assertFalse(inputs.down("a"))
        self.assertTrue(inputs.released("a"))

    def test_keyboard_and_hat_direction_combine(self):
        inputs = runtime.InputState()
        inputs.begin_frame()
        inputs.process(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT))
        inputs.process(pygame.event.Event(
            pygame.JOYHATMOTION, instance_id=7, hat=0, value=(-1, 0)
        ))
        inputs.begin_frame()
        inputs.process(pygame.event.Event(pygame.KEYUP, key=pygame.K_LEFT))
        self.assertTrue(inputs.down("left"))
        self.assertFalse(inputs.released("left"))
        inputs.process(pygame.event.Event(
            pygame.JOYHATMOTION, instance_id=7, hat=0, value=(0, 0)
        ))
        self.assertFalse(inputs.down("left"))
        self.assertTrue(inputs.released("left"))

    def test_controller_removal_clears_held_input(self):
        inputs = runtime.InputState()
        inputs.begin_frame()
        inputs.process(pygame.event.Event(
            pygame.JOYHATMOTION, instance_id=9, hat=0, value=(1, 0)
        ))
        self.assertTrue(inputs.down("right"))
        inputs.begin_frame()
        inputs.process(pygame.event.Event(
            pygame.JOYDEVICEREMOVED, instance_id=9
        ))
        self.assertFalse(inputs.down("right"))
        self.assertTrue(inputs.released("right"))

    def test_analog_stick_has_deadzone_and_directional_input(self):
        inputs = runtime.InputState()
        inputs.begin_frame()
        inputs.process(pygame.event.Event(
            pygame.JOYAXISMOTION, instance_id=3, axis=0, value=0.2
        ))
        self.assertFalse(inputs.down("right"))
        inputs.process(pygame.event.Event(
            pygame.JOYAXISMOTION, instance_id=3, axis=0, value=0.8
        ))
        self.assertTrue(inputs.down("right"))
        inputs.process(pygame.event.Event(
            pygame.JOYAXISMOTION, instance_id=3, axis=0, value=0.0
        ))
        self.assertFalse(inputs.down("right"))

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            runtime.InputState().down("jump")

    def test_demo_game_loads_and_draws(self):
        game = runtime.load_game(ROOT / "games" / "demo" / "main.py")
        surface = pygame.Surface(runtime.LOGICAL_SIZE)
        game.draw(surface)
        self.assertEqual(surface.get_size(), (640, 480))


if __name__ == "__main__":
    unittest.main()
