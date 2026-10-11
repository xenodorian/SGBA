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

    def test_sprite_sheet_frames_and_camera_clipping(self):
        sheet_image = pygame.Surface((16, 8), pygame.SRCALPHA)
        pygame.draw.rect(sheet_image, (255, 0, 0, 255), (0, 0, 8, 8))
        pygame.draw.rect(sheet_image, (0, 255, 0, 255), (8, 0, 8, 8))
        sheet = runtime.SpriteSheet(sheet_image, (8, 8))
        self.assertEqual(len(sheet.frames), 2)
        target = pygame.Surface((16, 8))
        sheet.draw(target, 1, (8, 0), camera=(4, 0), clip=pygame.Rect(0, 0, 8, 8))
        self.assertEqual(target.get_at((4, 2))[:3], (0, 255, 0))
        self.assertEqual(target.get_at((12, 2))[:3], (0, 0, 0))

    def test_animation_advances_and_stops(self):
        animation = runtime.Animation([2, 3, 4], frame_duration=0.1, loop=False)
        animation.update(0.31)
        self.assertEqual(animation.frame, 4)
        self.assertTrue(animation.finished)
        animation.update(1.0)
        self.assertEqual(animation.frame, 4)

    def test_tilemap_draws_visible_tiles(self):
        tiles = pygame.Surface((8, 4), pygame.SRCALPHA)
        pygame.draw.rect(tiles, (255, 0, 0, 255), (0, 0, 4, 4))
        pygame.draw.rect(tiles, (0, 255, 0, 255), (4, 0, 4, 4))
        sheet = runtime.SpriteSheet(tiles, (4, 4))
        tilemap = runtime.TileMap([[0, 1, -1], [1, 0, 1]], sheet)
        target = pygame.Surface((8, 8))
        tilemap.draw(target, camera=(0, 0))
        self.assertEqual(target.get_at((1, 1))[:3], (255, 0, 0))
        self.assertEqual(target.get_at((5, 1))[:3], (0, 255, 0))
        self.assertEqual(target.get_at((1, 5))[:3], (0, 255, 0))

    def test_scene_manager_switches_and_delegates(self):
        class Scene:
            def __init__(self, color):
                self.color = color
                self.updated = 0
            def update(self, dt, inputs):
                self.updated += 1
            def draw(self, surface):
                surface.fill(self.color)
        scenes = runtime.SceneManager()
        red, blue = Scene((255, 0, 0)), Scene((0, 0, 255))
        scenes.add("red", red)
        scenes.add("blue", blue)
        scenes.update(runtime.FIXED_DT, runtime.InputState())
        self.assertEqual(red.updated, 1)
        scenes.switch("blue")
        target = pygame.Surface((2, 2))
        scenes.draw(target)
        self.assertEqual(target.get_at((0, 0))[:3], (0, 0, 255))

    def test_save_store_round_trip_and_size_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            store = runtime.SaveStore(Path(temp) / "slot.json")
            self.assertEqual(store.load({"coins": 0}), {"coins": 0})
            store.save({"coins": 12, "items": ["key"]})
            self.assertEqual(store.load(), {"coins": 12, "items": ["key"]})
            with self.assertRaises(ValueError):
                store.save({"text": "x" * (store.MAX_BYTES + 1)})

    def test_draw_sprite_restores_clip_and_applies_camera(self):
        target = pygame.Surface((10, 10))
        sprite = pygame.Surface((2, 2))
        sprite.fill((255, 255, 255))
        original_clip = target.get_clip()
        runtime.draw_sprite(target, sprite, (5, 5), camera=(2, 2), clip=pygame.Rect(0, 0, 4, 4))
        self.assertEqual(target.get_at((3, 3))[:3], (255, 255, 255))
        self.assertEqual(target.get_clip(), original_clip)

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
