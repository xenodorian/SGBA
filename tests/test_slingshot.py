"""Slingshot Orbits (web_games/slingshot_orbits) played headless with a fake pad, through the real JS host."""
import json
import math
import os
import re
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

GAME = Path(__file__).resolve().parents[1] / "web_games" / "slingshot_orbits"
VMAX = 320.0


class FakePad:
    def __init__(self, axes):
        self.axes = list(axes)

    def get_axis(self, index):
        return self.axes[index]


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class SlingshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((640, 480))
        text = (GAME / "levels.js").read_text(encoding="utf-8")
        cls.levels = json.loads(re.search(r"SLINGSHOT_LEVELS = (\{.*\});", text, re.S).group(1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.messages = []
        self.game = sgba_js.JSGame(GAME, save_dir=Path(tmp.name), log=lambda level, text: self.messages.append((level, text)))
        self.inputs = runtime.InputState()
        self.pad = FakePad([0.0] * 4)
        self.inputs._joysticks[0] = self.pad
        self.out = pygame.Surface(self.game.logical_size)

    def step(self, frames=1):
        for _ in range(frames):
            self.game.update(1 / 60, self.inputs)
            self.inputs.consume_edges()
            self.game.draw(self.out)

    def press(self, action, frames=2):
        self.inputs._set(action, ("test",), True)
        self.step(frames)
        self.inputs._set(action, ("test",), False)
        self.step(1)

    def status(self):
        return json.loads(self.game.host.ctx.eval("JSON.stringify(window.slingshotStatus())"))

    def start_game(self):
        self.step(5)
        self.press("a", 3)               # Launch is the first row of the menu
        self.assertEqual(self.status()["orbit"], 1)

    def dial_at(self, x, y):
        """Hold the right stick at (x, y) and return the power the page reads."""
        self.pad.axes[2], self.pad.axes[3] = x, y
        self.step(2)
        return self.status()["power"]

    def fly_stored_launch(self, key):
        """Aim with the sticks at the level's stored winning launch, fire with R1, and wait for the flight to end."""
        vx, vy = self.levels[key]["sol"][0], self.levels[key]["sol"][1]
        aim = math.atan2(vy, vx)
        power = math.hypot(vx, vy) / VMAX
        phi = power * 2 * math.pi + math.pi / 2
        self.pad.axes = [-math.cos(aim), -math.sin(aim), math.cos(phi), -math.sin(phi)]   # left stick pulled back, dial set
        self.step(4)
        self.assertAlmostEqual(self.status()["power"], power, places=2)
        self.pad.axes = [0.0] * 4
        self.press("r1", 2)
        self.assertEqual(self.status()["state"], "flying")
        for _ in range(60 * 14):
            self.step(1)
            if self.status()["state"] != "flying":
                break

    def goto(self, orbit):
        self.game.host.ctx.eval(f"window.slingshotGoto({orbit})")
        self.step(2)

    def test_levels_ship_for_the_whole_campaign(self):
        for orbit in range(1, 21):
            self.assertIn(f"{orbit}:{orbit}", self.levels)           # orbit N has N planets
            self.assertEqual(len(self.levels[f"{orbit}:{orbit}"]["planets"]), orbit)
            self.assertEqual(self.levels[f"{orbit}:{orbit}"]["sol"][3], 1)   # the stored launch is strict (no shortcuts)

    def test_campaign_starts_with_one_planet_and_adds_one_per_orbit(self):
        self.start_game()
        status = self.status()
        self.assertEqual((status["planets"], status["shots"], status["state"]), (1, 3, "idle"))
        for orbit in (2, 7, 13, 20):
            self.goto(orbit)
            self.assertEqual((self.status()["orbit"], self.status()["planets"]), (orbit, orbit))

    def test_clearing_orbit_twenty_wins_the_game(self):
        self.start_game()
        self.goto(20)
        self.fly_stored_launch("20:20")
        self.assertEqual(self.status()["state"], "won")
        self.assertFalse(self.status()["finished"])                          # the win screen waits for the celebration
        self.step(60 * 3)
        status = self.status()
        self.assertTrue(status["finished"] and status["menu"])               # the win screen is up and nothing loads orbit 21
        self.assertEqual(status["orbit"], 20)
        self.assertEqual([m for m in self.messages if m[0] == "error"], [])
        self.press("a", 3)                                                    # Play again starts a fresh campaign
        status = self.status()
        self.assertEqual((status["orbit"], status["planets"], status["total"], status["finished"]), (1, 1, 0, False))

    def test_clearing_an_earlier_orbit_moves_on_to_the_next_one(self):
        self.start_game()
        self.fly_stored_launch("1:1")
        self.assertEqual(self.status()["state"], "won")
        self.step(60 * 3)
        status = self.status()
        self.assertEqual((status["orbit"], status["planets"], status["finished"]), (2, 2, False))

    def test_effects_play_on_launch_and_on_hitting_the_target(self):
        played = []
        self.game.host.ctx.eval("window.__played = []; const __orig = Audio.prototype.play;"
                                "Audio.prototype.play = function () { window.__played.push(this.src); return __orig.call(this); };")
        self.start_game()
        self.fly_stored_launch("1:1")
        played = json.loads(self.game.host.ctx.eval("JSON.stringify(window.__played)"))
        names = [p.split("/")[-1] for p in played]
        self.assertIn("launch.ogg", names)
        self.assertIn("target.ogg", names)
        self.assertLess(names.index("launch.ogg"), names.index("target.ogg"))

    def test_right_stick_dial_is_a_clock_face_from_up(self):
        self.start_game()
        self.assertAlmostEqual(self.dial_at(0.0, -1.0), 0.0, places=2)     # up: zero intensity
        self.assertAlmostEqual(self.dial_at(-1.0, 0.0), 0.25, places=2)    # left: 90 degrees, 25 percent
        self.assertAlmostEqual(self.dial_at(0.0, 1.0), 0.50, places=2)     # down: 180 degrees
        self.assertAlmostEqual(self.dial_at(1.0, 0.0), 0.75, places=2)     # right: 270 degrees

    def test_dial_holds_its_value_when_the_stick_is_released(self):
        self.start_game()
        self.dial_at(-1.0, 0.0)
        self.pad.axes[2:] = [0.0, 0.0]
        self.step(30)
        self.assertAlmostEqual(self.status()["power"], 0.25, places=2)

    def test_stored_launch_wins_orbit_one_through_the_sticks(self):
        self.start_game()
        self.fly_stored_launch("1:1")
        self.assertEqual(self.status()["state"], "won")
        self.assertEqual(self.status()["total"], 500 + 2 * 250)              # orbit bonus with two shots left
        self.assertEqual([m for m in self.messages if m[0] == "error"], [])


if __name__ == "__main__":
    unittest.main()
