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

    def start_blind(self, difficulty="hard"):
        self.step(5)
        self.press("down", 2)            # second row of the title menu: Blind Crash
        self.press("a", 3)               # opens the difficulty pick: Easy, Medium, Hard
        for _ in range({"easy": 0, "medium": 1, "hard": 2}[difficulty]):
            self.press("down", 2)
        self.press("a", 3)
        self.assertEqual(self.status()["mode"], "blind")
        self.assertEqual(self.status()["difficulty"], difficulty)

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

    def test_blind_crash_hard_has_ten_stardust(self):
        self.start_blind()
        self.assertEqual(self.status()["dustTotal"], 10)

    def test_blind_crash_starts_with_20_hp_and_no_route_line(self):
        self.start_blind()
        status = self.status()
        self.assertEqual((status["hp"], status["stardust"], status["orbit"], status["guide"]), (20, 0, 1, False))
        self.aim_at(0.3, 0.5)
        self.assertFalse(self.status()["preview"])

    def test_blind_crash_costs_hp_and_relaunches_from_where_it_crashed(self):
        self.start_blind()
        lv = self.levels["1:1"]
        sx, sy = lv["start"]
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
        self.assertGreater(math.hypot(status["x"] - sx, status["y"] - sy), 20)   # not back at the start
        self.assertTrue(0 < status["x"] < 640 and 0 < status["y"] < 480)
        for px, py, pr, *_ in lv["planets"]:
            self.assertGreater(math.hypot(status["x"] - px, status["y"] - py), pr + 4)   # and not inside a planet
        self.aim_at(math.atan2(240 - status["y"], 320 - status["x"]), 0.3)
        self.press("r1", 2)
        self.assertEqual(self.status()["state"], "flying")                    # it can launch again from there

    def test_crashing_into_a_planet_respawns_just_outside_it(self):
        self.start_blind()
        lv = self.levels["1:1"]
        (sx, sy), (px, py, pr, *_) = lv["start"], lv["planets"][0]
        self.aim_at(math.atan2(py - sy, px - sx), 0.5)                        # straight at the planet
        self.press("r1", 2)
        for _ in range(60 * 14):
            self.step(1)
            if self.status()["state"] == "dead":
                break
        self.step(90)
        status = self.status()
        self.assertEqual((status["state"], status["hp"]), ("idle", 19))
        gap = math.hypot(status["x"] - px, status["y"] - py) - pr
        self.assertTrue(4 < gap < 20, gap)                                    # beside the planet, not inside it

    def test_zero_hp_is_game_over_and_try_again_restarts(self):
        self.start_blind()
        self.game.host.ctx.eval("window.slingshotGrant(1)")
        sx = self.levels["1:1"]["start"][0]
        for _ in range(6):                                                    # a stardust on the way would mend the hull, so crash until it is gone
            if self.status()["over"]:
                break
            self.aim_at(math.pi if sx > 320 else 0.0, 0.9)
            self.press("r1", 2)
            self.step(60 * 14)
        status = self.status()
        self.assertTrue(status["over"] and status["menu"])
        self.assertEqual(status["hp"], 0)
        self.press("a", 3)                                                    # Try again
        status = self.status()
        self.assertEqual((status["mode"], status["hp"], status["orbit"], status["over"], status["menu"]), ("blind", 20, 1, False, False))

    def collect_one_dust(self):
        """Put the ship on the next stardust and fire a gentle shot so the pickup happens in flight."""
        spot = json.loads(self.game.host.ctx.eval("JSON.stringify(window.slingshotDust())"))
        self.game.host.ctx.eval(f"window.slingshotWarp({spot['x']}, {spot['y']})")
        self.aim_at(0.0, 0.05)
        self.press("r1", 2)
        self.step(3)

    def test_blind_crash_has_more_stardust_than_pathfinder(self):
        self.start_game()
        path_total = self.status()["dustTotal"]
        self.assertEqual(path_total, len(self.levels["1:1"]["dust"]))
        self.assertLess(path_total, 5)
        self.assertEqual(len(self.levels["1:1"]["bdust"]), 20)

    def test_pause_menu_resumes_or_quits(self):
        self.start_game()
        self.press("select", 2)
        self.assertTrue(self.status()["menu"])
        self.press("a", 3)                                                    # Back to orbit
        status = self.status()
        self.assertFalse(status["menu"])
        self.assertEqual(status["orbit"], 1)
        self.press("select", 2)
        self.press("down", 2)
        self.press("a", 3)                                                    # Quit game
        self.step(60)                                                         # this host cannot close the page, so the game falls back to the title
        self.assertEqual(self.game.host.ctx.eval("String(window.slingshotStatus())"), "null")
        self.press("a", 3)                                                    # the title menu works again: Pathfinder
        self.assertEqual((self.status()["mode"], self.status()["orbit"]), ("path", 1))

    def test_easy_has_twice_the_stardust_and_medium_mends_two_hp(self):
        self.start_blind("easy")
        self.assertEqual((self.status()["dustTotal"], self.status()["difficulty"]), (20, "easy"))
        self.game.host.ctx.eval("window.slingshotGrant(10)")
        self.collect_one_dust()
        self.assertEqual(self.status()["hp"], 11)                             # easy still mends 1 per stardust

    def test_medium_stardust_mends_two_hp_and_stops_at_twenty(self):
        self.start_blind("medium")
        self.assertEqual(self.status()["dustTotal"], 10)
        self.game.host.ctx.eval("window.slingshotGrant(10)")
        self.collect_one_dust()
        self.assertEqual(self.status()["hp"], 12)
        self.game.host.ctx.eval("window.slingshotGrant(19)")
        self.collect_one_dust()
        self.assertEqual((self.status()["hp"], self.status()["guide"]), (20, False))   # capped, route line only once already full

    def test_each_stardust_mends_one_hp(self):
        self.start_blind()
        self.game.host.ctx.eval("window.slingshotGrant(10)")
        self.collect_one_dust()
        self.assertEqual((self.status()["hp"], self.status()["stardust"], self.status()["guide"]), (11, 1, False))

    def test_stardust_at_full_hp_turns_the_route_line_on(self):
        self.start_blind()
        self.collect_one_dust()
        status = self.status()
        self.assertEqual((status["hp"], status["guide"]), (20, True))         # nothing to mend, so the route line comes on
        self.goto(2)
        self.assertFalse(self.status()["guide"])                              # for this orbit only

    def test_stardust_does_nothing_in_pathfinder(self):
        self.start_game()
        self.collect_one_dust()
        self.assertEqual((self.status()["hp"], self.status()["guide"]), (20, False))

    def test_clearing_an_orbit_mends_one_hp_on_easy_and_medium_but_not_hard(self):
        for difficulty, expect in (("easy", 11), ("medium", 11), ("hard", 10)):
            with self.subTest(difficulty=difficulty):
                self.setUp()
                self.start_blind(difficulty)
                self.game.host.ctx.eval("window.slingshotGrant(10)")
                self.fly_stored_launch("1:1")
                self.assertEqual(self.status()["state"], "won")
                self.assertEqual(self.status()["hp"], expect)

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
