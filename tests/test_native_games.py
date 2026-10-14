"""The emulator's own games (demo and input lab) are browser games, because the C runtime does not run Python games."""
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import runtime

try:
    import sgba_js
    HAVE_JS = sgba_js.quickjs is not None
except ImportError:
    HAVE_JS = False

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class EmulatorGamesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((640, 480))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def run_game(self, name):
        with tempfile.TemporaryDirectory() as tmp:
            game = sgba_js.JSGame(ROOT / "web_games" / name, save_dir=Path(tmp), log=lambda *a: None)
            inputs = runtime.InputState()
            out = pygame.Surface(game.logical_size, 0, game.surface_depth)
            for frame in range(60):
                inputs._set("right", ("test",), 10 < frame < 40)
                inputs._set("a", ("test",), frame > 50)
                game.update(1 / 60, inputs)
                inputs.consume_edges()
                game.draw(out)
            return game

    def test_demo_runs_without_errors(self):
        self.assertEqual(self.run_game("demo").host.errors, 0)

    def test_input_lab_runs_without_errors(self):
        self.assertEqual(self.run_game("input_lab").host.errors, 0)

    def test_packages_validate(self):
        from package_game import package_game
        from sgba_package import validate_package
        with tempfile.TemporaryDirectory() as tmp:
            for name, title in (("demo", "Demo"), ("input_lab", "Input Lab")):
                path = package_game(ROOT / "web_games" / name, Path(tmp) / f"{name}.sgba", title)
                self.assertEqual(validate_package(Path(path))["title"], title)


if __name__ == "__main__":
    unittest.main()
