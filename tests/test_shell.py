import os
import tempfile
import unittest
import zipfile
import json
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import runtime
import sgba_shell

JB = pygame.JOYBUTTONDOWN


def key(kind, k):
    return pygame.event.Event(kind, key=k)


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.display = pygame.display.set_mode((640, 480))
        cls._tmp = tempfile.TemporaryDirectory()
        cls._log_patch = mock.patch.object(sgba_shell, "LOG_PATH", Path(cls._tmp.name) / "test.log")
        cls._log_patch.start()   # keep test noise out of the real sgba.log

    @classmethod
    def tearDownClass(cls):
        cls._log_patch.stop()
        cls._tmp.cleanup()
        pygame.quit()

    def test_custom_bindings_replace_default_pad_map(self):
        inputs = runtime.InputState()
        inputs.set_bindings({"a": [("button", 5)], "up": [("hat", 0, "up")], "right": [("axis", 2, "+")]})
        inputs.process(pygame.event.Event(JB, instance_id=1, button=0))   # default 'a' button
        self.assertFalse(inputs.down("a"))
        inputs.process(pygame.event.Event(JB, instance_id=1, button=5))
        self.assertTrue(inputs.down("a"))
        inputs.process(pygame.event.Event(pygame.JOYBUTTONUP, instance_id=1, button=5))
        self.assertFalse(inputs.down("a"))
        inputs.process(pygame.event.Event(pygame.JOYHATMOTION, instance_id=1, hat=0, value=(0, 1)))
        self.assertTrue(inputs.down("up"))
        inputs.process(pygame.event.Event(pygame.JOYHATMOTION, instance_id=1, hat=0, value=(0, 0)))
        self.assertFalse(inputs.down("up"))
        inputs.process(pygame.event.Event(pygame.JOYAXISMOTION, instance_id=1, axis=2, value=0.9))
        self.assertTrue(inputs.down("right"))
        inputs.process(key(pygame.KEYDOWN, pygame.K_z))                   # keyboard still works
        self.assertTrue(inputs.down("a"))
        inputs.set_bindings(None)
        inputs.process(pygame.event.Event(JB, instance_id=1, button=0))
        self.assertTrue(inputs.down("a"))

    def test_binding_validation(self):
        with self.assertRaises(ValueError):
            runtime.InputState().set_bindings({"jump": [("button", 1)]})
        with self.assertRaises(ValueError):
            runtime.InputState().set_bindings({"a": [("mouse", 1)]})

    def test_controls_file_round_trip_and_bad_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "controls.json"
            self.assertEqual(sgba_shell.load_controls(path), (False, None))
            bindings = {"a": [("button", 3)], "up": [("hat", 0, "up")], "left": [("axis", 0, "-")]}
            self.assertTrue(sgba_shell.save_controls(bindings, path))
            self.assertEqual(sgba_shell.load_controls(path), (True, bindings))
            inputs = runtime.InputState()
            self.assertTrue(sgba_shell.apply_saved_controls(inputs, path))
            inputs.process(pygame.event.Event(JB, instance_id=1, button=3))
            self.assertTrue(inputs.down("a"))
            sgba_shell.save_controls(None, path)
            self.assertEqual(sgba_shell.load_controls(path), (True, None))
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(sgba_shell.load_controls(path), (False, None))
            path.write_text(json.dumps({"bindings": {"a": [["button"]]}}), encoding="utf-8")
            self.assertEqual(sgba_shell.load_controls(path), (False, None))

    def test_wizard_captures_every_step_and_rejects_duplicates(self):
        wizard = sgba_shell.ControllerWizard()
        for number in range(12):
            wizard.update(1.0, None)
            wizard.handle_event(pygame.event.Event(JB, instance_id=1, button=number))
        self.assertEqual(wizard.step, 12)
        wizard.update(1.0, None)
        wizard.handle_event(pygame.event.Event(JB, instance_id=1, button=0))   # duplicate
        self.assertEqual(wizard.step, 12)
        self.assertIn("Already used", wizard.message)
        wizard.update(1.0, None)
        wizard.handle_event(pygame.event.Event(JB, instance_id=1, button=5))   # B button (5) skips optional L2
        self.assertEqual(wizard.step, 13)
        wizard.update(1.0, None)
        wizard.handle_event(pygame.event.Event(pygame.JOYAXISMOTION, instance_id=1, axis=5, value=0.95))
        self.assertTrue(wizard.completed and wizard.finished)
        self.assertEqual(wizard.bindings["up"], [("button", 0)])
        self.assertEqual(wizard.bindings["r2"], [("axis", 5, "+")])
        self.assertNotIn("l2", wizard.bindings)
        wizard.draw(pygame.Surface(sgba_shell.MENU_SIZE))

    def test_wizard_ignores_bounce_and_stops_on_keyboard(self):
        wizard = sgba_shell.ControllerWizard()
        wizard.update(1.0, None)
        wizard.handle_event(pygame.event.Event(JB, instance_id=1, button=4))
        wizard.handle_event(pygame.event.Event(JB, instance_id=1, button=5))   # within cooldown
        self.assertEqual(wizard.step, 1)
        wizard.handle_event(key(pygame.KEYDOWN, pygame.K_RETURN))
        self.assertTrue(wizard.finished and wizard.keyboard_seen and not wizard.completed)

    def test_discover_games_lists_packages_and_dev_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            games = Path(tmp) / "games"
            (games / "dev1").mkdir(parents=True)
            (games / "dev1" / "main.py").write_text("", encoding="utf-8")
            (games / "template").mkdir()
            (games / "template" / "main.py").write_text("", encoding="utf-8")
            with zipfile.ZipFile(games / "ok.sgba", "w") as archive:
                archive.writestr("manifest.json", json.dumps(
                    {"format": "sgba", "format_version": 1, "title": "Nice Game", "entry_point": "main.py"}))
                archive.writestr("main.py", "")
            (games / "bad.sgba").write_bytes(b"nope")
            entries = sgba_shell.discover_games(Path(tmp))
            labels = {label: enabled for label, _k, _p, enabled in entries}
            self.assertEqual(labels, {"Nice Game": True, "bad (invalid)": False, "dev1 (dev)": True})

    def test_menu_navigation_and_draw(self):
        entries = [(f"g{i}", "game", i, True) for i in range(20)]
        entries.append(("off", "game", None, False))
        menu = sgba_shell.Menu(entries)
        inputs = runtime.InputState()
        inputs.process(key(pygame.KEYDOWN, pygame.K_DOWN))
        menu.update(0.016, inputs)
        self.assertEqual(menu.index, 1)
        inputs.consume_edges()
        inputs.process(key(pygame.KEYUP, pygame.K_DOWN))
        inputs.process(key(pygame.KEYDOWN, pygame.K_UP))
        inputs.process(key(pygame.KEYUP, pygame.K_UP))
        menu.update(0.016, inputs)
        inputs.consume_edges()
        inputs.process(key(pygame.KEYDOWN, pygame.K_UP))
        menu.update(0.016, inputs)
        self.assertEqual(menu.index, len(entries) - 1)      # wraps to the disabled entry
        inputs.consume_edges()
        inputs.process(key(pygame.KEYDOWN, pygame.K_z))
        menu.update(0.016, inputs)
        self.assertIsNone(menu.choice)                      # disabled entries cannot start
        menu.draw(pygame.Surface(sgba_shell.MENU_SIZE))

    def test_open_display_falls_back_through_drivers(self):
        tried = []
        real_init = pygame.display.init

        def fake_init():
            driver = os.environ.get("SDL_VIDEODRIVER")
            tried.append(driver)
            if driver != "x11":
                raise pygame.error("no such driver")
            os.environ["SDL_VIDEODRIVER"] = "dummy"   # stand in for a working x11
            real_init()

        saved = os.environ.pop("SDL_VIDEODRIVER", None)
        try:
            with mock.patch("pygame.display.init", fake_init):
                surface = runtime.open_display(False)
            self.assertEqual(tried, [None, "kmsdrm", "x11"])
            self.assertIsNotNone(surface)
            os.environ["SDL_VIDEODRIVER"] = "nonexistent"
            with mock.patch("pygame.display.init", fake_init):
                with self.assertRaises(RuntimeError):
                    runtime.open_display(False)       # explicit choice is tried alone
        finally:
            os.environ["SDL_VIDEODRIVER"] = "dummy" if saved is None else saved
            pygame.display.quit()
            pygame.display.init()
            type(self).display = pygame.display.set_mode((640, 480))

    def _run_shell(self, root, batches):
        script = iter(batches)

        def fake_get():
            try:
                return next(script)
            except StopIteration:
                return [pygame.event.Event(pygame.QUIT)]

        inputs = runtime.InputState()
        with mock.patch.object(sgba_shell, "ROOT", Path(root)), \
                mock.patch.object(sgba_shell, "LOG_PATH", Path(root) / "sgba.log"), \
                mock.patch.object(sgba_shell, "CONTROLS_PATH", Path(root) / "controls.json"), \
                mock.patch("pygame.event.get", fake_get):
            sgba_shell.shell_main(self.display, inputs, runtime)
        return script

    def test_shell_runs_game_and_returns_to_menu_then_survives_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "games" / "a_good"
            bad = Path(tmp) / "games" / "b_bad"
            good.mkdir(parents=True)
            bad.mkdir()
            marker = Path(tmp) / "ran.txt"
            (good / "main.py").write_text(
                "class G:\n"
                "    def draw(self, s):\n"
                f"        with open(r'{marker}', 'a') as f:\n"
                "            f.write('x')\n"
                "def create_game():\n    return G()\n", encoding="utf-8")
            (bad / "main.py").write_text(
                "class G:\n    def draw(self, s):\n        raise ValueError('boom')\n"
                "def create_game():\n    return G()\n", encoding="utf-8")
            enter, back = pygame.K_RETURN, pygame.K_BACKSPACE
            z, down = pygame.K_z, pygame.K_DOWN
            D, U = pygame.KEYDOWN, pygame.KEYUP
            batches = [
                [key(D, z)], [key(U, z)],                                  # menu: start a_good
                [], [], [key(D, enter), key(D, back)], [key(U, enter), key(U, back)],   # combo returns to menu
                [key(D, down)], [key(U, down)], [key(D, z)], [key(U, z)],   # menu: start b_bad (crashes)
                [], [], [key(D, z)], [key(U, z)],                           # error screen dismissed (after delay)
            ]
            # error screen ignores input for 0.6 s; pad with idle frames
            batches[12:12] = [[]] * 50
            __import__("sys").modules.pop("sgba_game", None)   # left behind by other tests
            script = self._run_shell(tmp, batches)
            self.assertTrue(marker.exists())
            log = (Path(tmp) / "sgba.log").read_text(encoding="utf-8")
            self.assertIn("ValueError: boom", log)
            self.assertNotIn("sgba_game", __import__("sys").modules)
            self.assertEqual(list(script), [])                              # whole script consumed


if __name__ == "__main__":
    unittest.main()
