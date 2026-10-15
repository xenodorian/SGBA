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

    def start_blind(self):
        self.step(5)
        self.press("down", 2)            # second row of the title menu: Blind Crash
        self.press("a", 3)
        self.assertEqual(self.status()["mode"], "blind")

    def aim_at(self, angle, power):
        phi = power * 2 * math.pi + math.pi / 2
        self.pad.axes = [-math.cos(angle), -math.sin(angle), math.cos(phi), -math.sin(phi)]
        self.step(4)
        self.pad.axes = [0.0] * 4

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

    # ---------- the title menu and Blind Crash ----------
    def test_title_menu_first_row_is_pathfinder(self):
        self.start_game()
        status = self.status()
        self.assertEqual((status["mode"], status["hp"]), ("path", 20))
        self.aim_at(0.3, 0.5)
        self.assertTrue(self.status()["preview"])                            # Pathfinder shows the route line

    def test_every_level_stores_its_own_start_and_goal(self):
        starts = {tuple(self.levels[f"{n}:{n}"]["start"]) for n in range(1, 21)}
        self.assertGreater(len(starts), 15)                                   # random ship positions, not one fixed spot
        for n in range(1, 21):
            lv = self.levels[f"{n}:{n}"]
            for x, y in (lv["start"], lv["goal"]):
                self.assertTrue(0 < x < 640 and 0 < y < 480)

    def test_blind_crash_starts_with_20_hp_and_no_route_line(self):
        self.start_blind()
        status = self.status()
        self.assertEqual((status["hp"], status["stardust"], status["orbit"], status["guide"]), (20, 0, 1, False))
        self.aim_at(0.3, 0.5)
        self.assertFalse(self.status()["preview"])

    def test_blind_crash_costs_hp_and_relaunches_from_the_start(self):
        self.start_blind()
        sx, sy = self.levels["1:1"]["start"]
        self.aim_at(math.pi if sx > 320 else 0.0, 0.9)                        # straight at the nearer wall, full power
        self.press("r1", 2)
        for _ in range(60 * 14):
            self.step(1)
            if self.status()["state"] == "dead":
                break
        self.assertEqual(self.status()["hp"], 19)
        self.step(90)
        status = self.status()
        self.assertEqual((status["state"], status["orbit"], status["hp"], status["menu"]), ("idle", 1, 19, False))
        self.assertAlmostEqual(status["x"], sx, places=1)                     # launches again from the same spot
        self.assertAlmostEqual(status["y"], sy, places=1)

    def test_zero_hp_is_game_over_and_try_again_restarts(self):
        self.start_blind()
        self.game.host.ctx.eval("window.slingshotGrant(1, 0)")
        sx = self.levels["1:1"]["start"][0]
        self.aim_at(math.pi if sx > 320 else 0.0, 0.9)
        self.press("r1", 2)
        self.step(60 * 14)
        status = self.status()
        self.assertTrue(status["over"] and status["menu"])
        self.assertEqual(status["hp"], 0)
        self.press("a", 3)                                                    # Try again
        status = self.status()
        self.assertEqual((status["mode"], status["hp"], status["orbit"], status["over"], status["menu"]), ("blind", 20, 1, False, False))

    def test_stardust_repairs_for_three_and_route_line_for_ten(self):
        self.start_blind()
        self.game.host.ctx.eval("window.slingshotGrant(10, 2)")
        self.press("x", 2)
        self.assertEqual((self.status()["hp"], self.status()["stardust"]), (10, 2))   # two is not enough
        self.game.host.ctx.eval("window.slingshotGrant(10, 13)")
        self.press("x", 2)
        self.assertEqual((self.status()["hp"], self.status()["stardust"]), (11, 10))
        self.aim_at(0.3, 0.5)
        self.press("y", 2)
        status = self.status()
        self.assertEqual((status["guide"], status["stardust"]), (True, 0))
        self.step(3)
        self.assertTrue(self.status()["preview"])
        self.goto(2)
        self.assertFalse(self.status()["guide"])                              # the line lasts for one orbit

    def test_repair_stops_at_full_hull(self):
        self.start_blind()
        self.game.host.ctx.eval("window.slingshotGrant(20, 9)")
        self.press("x", 2)
        self.assertEqual((self.status()["hp"], self.status()["stardust"]), (20, 9))

    def test_stardust_on_the_route_is_collected(self):
        self.start_blind()
        self.game.host.ctx.eval("window.slingshotGoto(1)")
        self.fly_stored_launch("1:1")
        self.assertEqual(self.status()["state"], "won")
        self.assertEqual(self.status()["stardust"], self.status()["dust"])

    def test_blind_crash_can_be_won_with_the_stored_launches(self):
        self.start_blind()
        self.goto(20)
        self.fly_stored_launch("20:20")
        self.assertEqual(self.status()["state"], "won")
        self.step(60 * 3)
        status = self.status()
        self.assertTrue(status["finished"] and status["menu"])
        self.press("a", 3)                                                    # Play again keeps the mode
        self.assertEqual((self.status()["mode"], self.status()["orbit"]), ("blind", 1))


if __name__ == "__main__":
    unittest.main()
