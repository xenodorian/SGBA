"""Slingshot Orbits on a handheld without analog sticks: d-pad and button controls, the first-press guard,
the packaged port, and a guard on how many canvas operations a frame costs (the scene must stay baked)."""
import json
import os
import sys
import tempfile
import unittest
import zipfile
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
GAME = ROOT / "web_games" / "slingshot_orbits"
sys.path.insert(0, str(ROOT / "tools"))


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class SlingshotDpadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((640, 480))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.game = sgba_js.JSGame(GAME, save_dir=Path(tmp.name), log=lambda *a: None)
        self.inputs = runtime.InputState()
        self.out = pygame.Surface(self.game.logical_size)

    def frame(self, held=()):
        for action in ("up", "down", "left", "right", "a", "b", "r1"):
            self.inputs._set(action, ("test",), action in held)
        self.game.update(1 / 60, self.inputs)
        self.inputs.consume_edges()
        self.game.draw(self.out)

    def hold(self, held, frames):
        for _ in range(frames):
            self.frame(held)
        self.frame()

    def status(self):
        return json.loads(self.game.host.ctx.eval("JSON.stringify(window.slingshotStatus())"))

    def start(self):
        for _ in range(5):
            self.frame()
        self.hold(("a",), 3)                # the menu's Launch row, chosen with A
        self.frame()

    def test_the_press_that_starts_the_game_does_not_fire_a_shot(self):
        self.start()
        status = self.status()
        self.assertEqual((status["state"], status["shots"]), ("idle", 3))

    def test_dpad_turns_aim_sets_power_and_a_launches(self):
        self.start()
        aim0, power0 = self.status()["aim"], self.status()["power"]
        self.hold(("up",), 20)
        self.assertLess(self.status()["aim"], aim0 - 0.3)                  # up turns the aim toward the top
        self.hold(("right",), 20)
        self.assertGreater(self.status()["power"], power0 + 0.05)          # right adds power
        raised = self.status()["power"]
        self.hold(("left",), 10)
        self.assertLess(self.status()["power"], raised)                    # left takes it away
        before = self.status()["aim"]
        self.hold(("down",), 20)
        coarse = abs(self.status()["aim"] - before)
        before = self.status()["aim"]
        self.hold(("down", "b"), 20)
        self.assertLess(abs(self.status()["aim"] - before), coarse / 2)    # B makes the same turn slow
        self.hold(("a",), 2)
        self.assertEqual(self.status()["state"], "flying")
        self.assertEqual(self.game.host.errors, 0)

    def test_r1_also_launches(self):
        self.start()
        self.hold(("r1",), 2)
        self.assertEqual(self.status()["state"], "flying")

    def test_canvas_operations_per_frame_stay_low(self):
        self.start()
        self.hold(("up",), 30)
        stats = self.game.host.stats
        ops0, frames0 = stats.ops, stats.frames
        for _ in range(120):
            self.frame()
        per_frame = (stats.ops - ops0) / max(1, stats.frames - frames0)
        self.assertLess(per_frame, 60, f"{per_frame:.0f} canvas operations per frame: the scene must stay baked")

    def test_port_package_has_launcher_and_single_sgba(self):
        from build_game_port import build_port
        from sgba_package import validate_package
        with tempfile.TemporaryDirectory() as tmp:
            target = build_port(GAME, "Slingshot Orbits", Path(tmp))
            with zipfile.ZipFile(target) as z:
                self.assertEqual(sorted(z.namelist()), ["Slingshot Orbits.sh", "slingshot-orbits-game/slingshot-orbits.sgba"])
                self.assertTrue(z.getinfo("Slingshot Orbits.sh").external_attr >> 16 & 0o111)
                script = z.read("Slingshot Orbits.sh").decode()
                self.assertIn('exec bash "$HERE/SGBA.sh" "$GAMEDIR/slingshot-orbits.sgba"', script)
            self.assertEqual(validate_package(Path(tmp) / "slingshot-orbits.sgba")["title"], "Slingshot Orbits")


if __name__ == "__main__":
    unittest.main()
