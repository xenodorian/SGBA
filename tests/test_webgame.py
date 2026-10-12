import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import runtime
import sgba_package
import sgba_shell

ROOT = Path(__file__).resolve().parents[1]

try:
    import sgba_js
    HAVE_JS = sgba_js.quickjs is not None
except ImportError:
    HAVE_JS = False

PAGE = """<!doctype html><html><head><title>T</title></head><body><div id="stage"><canvas id="view" width="64" height="48"></canvas>
<div id="menu"><button id="keep">Play</button><button id="fs">Fullscreen</button></div></div>
<script>document.getElementById('view').getContext('2d').fillRect(0,0,10,10)</script></body></html>"""


def make_game(root: Path, sgba_ui=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(PAGE, encoding="utf-8")
    (root / "extra.mp3").write_bytes(b"x" * 100)
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "a.test.js").write_text("//", encoding="utf-8")
    if sgba_ui is not None:
        (root / "sgba_ui.json").write_text(json.dumps(sgba_ui), encoding="utf-8")
    return root


class WebGameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_package_a_web_folder_with_excludes_and_validate(self):
        import sys
        sys.path.insert(0, str(ROOT / 'tools'))
        import package_game as package_game_tool
        with tempfile.TemporaryDirectory() as tmp:
            game = make_game(Path(tmp) / "web")
            out = Path(tmp) / "g.sgba"
            package_game_tool.package_game(game, out, "Web Game", ("*.mp3", "tests/*"))
            manifest = sgba_package.validate_package(out)
            self.assertEqual((manifest["runtime"], manifest["entry_point"]), ("js", "index.html"))
            with zipfile.ZipFile(out) as archive:
                names = set(archive.namelist())
            self.assertEqual(names, {"manifest.json", "index.html"})

    def test_manifest_runtime_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.sgba"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("manifest.json", json.dumps({"format": "sgba", "format_version": 1, "title": "X",
                                                              "entry_point": "main.py", "runtime": "js"}))
                archive.writestr("main.py", "")
            with self.assertRaises(sgba_package.PackageError):
                sgba_package.validate_package(bad)

    def test_loose_folders_resolve_and_are_listed_in_the_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = make_game(Path(tmp) / "games" / "perry", {"title": "Parry Perry"})
            self.assertEqual(sgba_package.resolve_game_path(game), (game / "index.html").resolve())
            entries = sgba_shell.discover_games(Path(tmp))
            self.assertEqual([(e[0], e[3]) for e in entries], [("Parry Perry", True)])
            self.assertEqual(Path(entries[0][2]), game)

    def test_save_key_is_stable_for_folders_and_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = make_game(Path(tmp) / "Parry Perry")
            self.assertEqual(runtime.save_key(game), "parry-perry")
            self.assertEqual(runtime.save_key(game / "index.html"), "parry-perry")

    def test_package_extraction_uses_the_configured_cache_dir(self):
        import sys
        sys.path.insert(0, str(ROOT / 'tools'))
        import package_game as package_game_tool
        with tempfile.TemporaryDirectory() as tmp:
            game = make_game(Path(tmp) / "web")
            out = Path(tmp) / "g.sgba"
            package_game_tool.package_game(game, out, "Web Game")
            with mock_env(SGBA_CACHE_DIR=str(Path(tmp) / "cache")):
                entry = sgba_package.resolve_game_path(out)
            self.assertTrue(str(entry).startswith(str(Path(tmp) / "cache")))
            self.assertEqual(entry.name, "index.html")

    @unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
    def test_open_game_runs_a_browser_game_and_hide_removes_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            game = make_game(Path(tmp) / "web", {"hide": ["#fs"], "navigation": "engine"})
            with mock_env(SGBA_SAVE_DIR=str(Path(tmp) / "saves")):
                obj, size, entry = runtime.open_game(game)
            self.assertEqual(size, (64, 48))
            self.assertEqual(entry.name, "index.html")
            inputs = runtime.InputState()
            obj.update(1 / 60, inputs)
            self.assertEqual(obj.ui.focusable_count, 1)                # the Fullscreen button is hidden on this device
            labels = obj.host.ctx.eval("document.querySelector('#keep').textContent")
            self.assertEqual(labels, "Play")


class mock_env:
    """Set environment variables for a block."""
    def __init__(self, **values):
        self.values, self.saved = values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.saved[k] = os.environ.get(k)
            os.environ[k] = v

    def __exit__(self, *exc):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
