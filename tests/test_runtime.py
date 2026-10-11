import json
import os
import tempfile
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
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_default_resolution_is_640_by_480(self):
        self.assertEqual(runtime.LOGICAL_SIZE, (640, 480))
        self.assertEqual(runtime.DEFAULT_LOGICAL_SIZE, (640, 480))

    def test_manifest_accepts_supported_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / "main.py"
            game.touch()
            for size in ([240, 160], [320, 240], [640, 480]):
                (root / "game.json").write_text(json.dumps({"logical_size": size}), encoding="utf-8")
                self.assertEqual(runtime.load_game_config(game)["logical_size"], tuple(size))

    def test_manifest_rejects_unsupported_resolution_and_fps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / "main.py"
            game.touch()
            for manifest in ({"logical_size": [800, 600]}, {"target_fps": 30}):
                (root / "game.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    runtime.load_game_config(game)

    def test_input_edges_persist_until_consumed(self):
        inputs = runtime.InputState()
        inputs.begin_frame()
        inputs.process(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_LEFT))
        self.assertTrue(inputs.down("left"))
        self.assertTrue(inputs.pressed("left"))
        inputs.consume_edges()
        self.assertFalse(inputs.pressed("left"))
        inputs.process(pygame.event.Event(pygame.KEYUP, key=pygame.K_LEFT))
        self.assertFalse(inputs.down("left"))
        self.assertTrue(inputs.released("left"))
        inputs.consume_edges()
        self.assertFalse(inputs.released("left"))

    def test_keyboard_and_controller_sources_do_not_cancel_each_other(self):
        inputs = runtime.InputState()
        inputs.process(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_z))
        inputs.process(pygame.event.Event(pygame.JOYBUTTONDOWN, instance_id=7, button=0))
        inputs.process(pygame.event.Event(pygame.KEYUP, key=pygame.K_z))
        self.assertTrue(inputs.down("a"))
        self.assertFalse(inputs.released("a"))
        inputs.process(pygame.event.Event(pygame.JOYBUTTONUP, instance_id=7, button=0))
        self.assertFalse(inputs.down("a"))
        self.assertTrue(inputs.released("a"))

    def test_controller_removal_clears_held_input(self):
        inputs = runtime.InputState()
        inputs.process(pygame.event.Event(pygame.JOYHATMOTION, instance_id=9, hat=0, value=(1, 0)))
        self.assertTrue(inputs.down("right"))
        inputs.process(pygame.event.Event(pygame.JOYDEVICEREMOVED, instance_id=9))
        self.assertFalse(inputs.down("right"))
        self.assertTrue(inputs.released("right"))

    def test_analog_stick_has_deadzone(self):
        inputs = runtime.InputState()
        inputs.process(pygame.event.Event(pygame.JOYAXISMOTION, instance_id=3, axis=0, value=0.2))
        self.assertFalse(inputs.down("right"))
        inputs.process(pygame.event.Event(pygame.JOYAXISMOTION, instance_id=3, axis=0, value=0.8))
        self.assertTrue(inputs.down("right"))

    def test_asset_store_caches_images_and_enforces_dimensions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pygame.image.save(pygame.Surface((8, 8)), root / "tiny.png")
            store = runtime.AssetStore(root, budget_mb=1, max_image_dimension=16)
            self.assertIs(store.image("tiny.png"), store.image("tiny.png"))
            self.assertGreater(store.image_memory_bytes, 0)
            pygame.image.save(pygame.Surface((32, 8)), root / "wide.png")
            with self.assertRaises(ValueError):
                store.image("wide.png")

    def test_asset_store_blocks_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game"
            root.mkdir()
            outside = Path(temp) / "outside.png"
            pygame.image.save(pygame.Surface((8, 8)), outside)
            store = runtime.AssetStore(root)
            with self.assertRaises(ValueError):
                store.image("../outside.png")

    def test_asset_store_enforces_decoded_memory_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pygame.image.save(pygame.Surface((64, 64)), root / "large.png")
            store = runtime.AssetStore(root, budget_mb=1, max_image_dimension=128)
            store.budget_bytes = 100
            with self.assertRaises(MemoryError):
                store.image("large.png")

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(ValueError):
            runtime.InputState().down("jump")

    def test_demo_and_template_load_and_draw(self):
        for relative in ("games/demo/main.py", "games/template/main.py"):
            game = runtime.load_game(ROOT / relative)
            surface = pygame.Surface((240, 160))
            game.draw(surface)

    def test_input_lab_loads_and_draws(self):
        game = runtime.load_game(ROOT / "games/input_lab/main.py")
        surface = pygame.Surface(runtime.LOGICAL_SIZE)
        game.draw(surface)
        game.handle_event(pygame.event.Event(pygame.JOYBUTTONDOWN, instance_id=2, button=0))
        self.assertIn(0, game.buttons_down)


if __name__ == "__main__":
    unittest.main()
