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


def page(body_script: str, canvas="<canvas id=\"view\" width=\"64\" height=\"48\"></canvas>") -> str:
    return f"<!doctype html><html><body>{canvas}<script>{body_script}</script></body></html>"


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class JSHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def make(self, script, files=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "index.html").write_text(page(script), encoding="utf-8")
        for name, data in (files or {}).items():
            (root / name).write_bytes(data)
        messages = []
        game = sgba_js.JSGame(root, save_dir=root / "save", log=lambda level, text: messages.append((level, text)))
        self.messages = messages
        return game, root

    def run_frames(self, game, count=3):
        inputs = runtime.InputState()
        for _ in range(count):
            game.update(1 / 60, inputs)
        out = pygame.Surface(game.logical_size)
        game.draw(out)
        return out

    def test_source_atop_washes_only_existing_pixels(self):
        game, _ = self.make("const c=document.createElement('canvas');c.width=20;c.height=20;const x=c.getContext('2d');"
                            "x.fillStyle='#00ff00';x.fillRect(0,0,10,20);x.globalCompositeOperation='source-atop';"
                            "x.globalAlpha=0.5;x.fillStyle='#ff0000';x.fillRect(0,0,20,20);"
                            "const g=document.getElementById('view').getContext('2d');g.drawImage(c,0,0);")
        out = self.run_frames(game)
        r, g, b = tuple(out.get_at((5, 5)))[:3]
        self.assertTrue(120 <= r <= 135 and 120 <= g <= 135 and b == 0)
        self.assertEqual(tuple(out.get_at((15, 5)))[:3], (0, 0, 0))     # transparent pixels stay empty

    def test_keyboard_is_not_also_a_pad_when_the_page_reads_keys(self):
        game, _ = self.make("addEventListener('keydown',()=>{});")
        self.run_frames(game, 1)
        inputs = runtime.InputState()
        inputs.process(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_a))
        self.assertTrue(inputs.down("left"))
        self.assertFalse(inputs.down_device("left"))
        self.assertIn('"pressed": false', game._pad_json(inputs, False))

    def test_canvas_size_comes_from_html_and_rects_draw(self):
        game, _ = self.make("const c=document.getElementById('view');const x=c.getContext('2d');"
                            "x.fillStyle='#ff0000';x.fillRect(2,3,10,10);x.fillStyle='rgba(0,0,255,0.5)';x.fillRect(20,20,4,4);")
        self.assertEqual(game.logical_size, (64, 48))
        out = self.run_frames(game)
        self.assertEqual(tuple(out.get_at((5, 5)))[:3], (255, 0, 0))
        self.assertEqual(tuple(out.get_at((60, 40)))[:3], (0, 0, 0))
        self.assertEqual(game.host.errors, 0)

    def test_raf_timers_and_events_run_in_order(self):
        game, _ = self.make("""
          window.log=[]; let n=0;
          function f(t){ n++; window.log.push('raf'+n); if(n<3) requestAnimationFrame(f); }
          requestAnimationFrame(f);
          setTimeout(()=>window.log.push('t'),20);
          Promise.resolve().then(()=>window.log.push('p'));
          window.addEventListener('keydown',e=>window.log.push('k:'+e.code));
        """)
        pygame.event.clear()
        game.handle_event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_z))
        self.run_frames(game, 6)
        self.assertEqual(game.host.ctx.eval("window.log.join(',')"), "p,k:KeyZ,raf1,t,raf2,raf3")

    def test_images_transform_and_local_storage(self):
        surface = pygame.Surface((2, 2))
        surface.fill((0, 255, 0))
        import io
        buffer = io.BytesIO()
        pygame.image.save(surface, buffer, "x.png")
        game, root = self.make("""
          const x=document.getElementById('view').getContext('2d'); const im=new Image();
          im.onload=()=>{ x.save(); x.translate(32,24); x.scale(-1,1); x.drawImage(im,0,0,2,2,3,4,8,8); x.restore(); window.loaded=im.width; };
          im.src='dot.png'; localStorage.setItem('k','v'); window.stored=localStorage.getItem('k')+localStorage.getItem('none');
        """, {"dot.png": buffer.getvalue()})
        out = self.run_frames(game)
        self.assertEqual(game.host.ctx.eval("window.loaded"), 2)
        self.assertEqual(game.host.ctx.eval("window.stored"), "vnull")
        self.assertEqual(tuple(out.get_at((32 - 3 - 4, 24 + 8)))[:3], (0, 255, 0))   # mirrored to the left of x=32
        self.assertEqual(tuple(out.get_at((32 + 3 + 4, 24 + 8)))[:3], (0, 0, 0))
        self.assertTrue((root / "save").exists())

    def test_script_errors_are_reported_not_fatal(self):
        game, _ = self.make("window.ok=1; null.boom; window.after=2;")
        self.assertTrue(any(level == "error" for level, _ in self.messages))
        self.assertEqual(game.host.ctx.eval("window.ok"), 1)

    def test_gamepad_reflects_sgba_actions(self):
        game, _ = self.make("window.read=()=>{const p=navigator.getGamepads()[0];return p?p.buttons[0].pressed+','+p.buttons[12].pressed:'none'}")
        inputs = runtime.InputState()
        inputs._set("a", ("test",), True)
        inputs._set("up", ("test",), True)
        game.update(1 / 60, inputs)
        self.assertEqual(game.host.ctx.eval("window.read()"), "true,true")

    def test_fetch_reads_local_json(self):
        game, _ = self.make("fetch('d.json').then(r=>r.json()).then(j=>{window.v=j.a});", {"d.json": b'{"a": 7}'})
        self.run_frames(game)
        self.assertEqual(game.host.ctx.eval("window.v"), 7)


PAGE = """<!doctype html><html><head><style>
#menu[hidden]{display:none!important} .gone{display:none}
</style></head><body><div id="stage"><canvas id="view" width="64" height="48"></canvas>
<button id="hud">Menu</button>
<div id="menu" hidden><h2 id="title">Pause</h2><p>Pick one &amp; go</p>
<div id="list"><button id="b1" class="primary">One</button><button id="b2">Two</button><button id="b3" disabled>Three</button><button id="b4">Four</button></div>
<div class="gone"><button id="ghost">Hidden</button></div></div></div>
<script>
window.log = [];
for (const id of ['b1','b2','b4','hud']) document.getElementById(id).addEventListener('click', () => window.log.push(id));
document.addEventListener('keydown', e => { if (e.key === 'Escape') window.log.push('esc'); });
window.openMenu = () => { document.getElementById('menu').hidden = false; };
</script></body></html>"""


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class JSDomAndUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def make(self, config=None, html=PAGE):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "index.html").write_text(html, encoding="utf-8")
        if config:
            (root / "sgba_ui.json").write_text(__import__("json").dumps(config), encoding="utf-8")
        game = sgba_js.JSGame(root, save_dir=root / "save", log=lambda *a: None)
        self.inputs = runtime.InputState()
        return game

    def step(self, game, action=None, frames=2):
        if action:
            self.inputs._set(action, ("t",), True)
        for i in range(frames):
            game.update(1 / 60, self.inputs)
            if i == 0 and action:
                self.inputs._set(action, ("t",), False)
            self.inputs.consume_edges()

    def ev(self, game, code):
        return game.host.ctx.eval(code)

    def test_selectors_innerhtml_and_hidden(self):
        game = self.make()
        self.assertEqual(self.ev(game, "document.querySelectorAll('#list button:not(:disabled)').length"), 3)
        self.assertEqual(self.ev(game, "document.querySelectorAll('#list > button.primary, #hud').length"), 2)
        self.assertEqual(self.ev(game, "document.querySelector('[disabled]').id"), "b3")
        self.assertEqual(self.ev(game, "document.getElementById('b2').closest('#stage').id"), "stage")
        self.ev(game, "document.getElementById('list').innerHTML = '<button data-x-y=\"7\">A &lt;b&gt;</button><i>t</i>'")
        self.assertEqual(self.ev(game, "document.querySelector('#list button').dataset.xY"), "7")
        self.assertEqual(self.ev(game, "document.getElementById('list').textContent"), "A <b>t")
        self.assertEqual(self.ev(game, "document.getElementById('menu').hidden"), True)

    def test_overlay_hidden_until_opened_then_drawn_and_navigable(self):
        game = self.make()
        self.step(game)
        self.assertFalse(game.ui.modal)                 # menu is hidden; the lone HUD button is not a panel
        self.ev(game, "openMenu()")
        self.step(game)
        self.assertTrue(game.ui.modal)
        self.assertEqual(game.ui.focusable_count, 3)    # disabled and display:none buttons are skipped
        out = pygame.Surface((640, 480))
        out.fill((9, 9, 9))
        game.draw(out)
        self.assertNotEqual(tuple(out.get_at((320, 240)))[:3], (9, 9, 9))
        self.step(game)
        self.assertEqual(self.ev(game, "document.activeElement.id"), "b1")        # primary gets focus first
        self.step(game, "down")
        self.assertEqual(self.ev(game, "document.activeElement.id"), "b2")
        self.step(game, "down")
        self.assertEqual(self.ev(game, "document.activeElement.id"), "b4")        # b3 (disabled) is skipped
        self.step(game, "down")
        self.assertEqual(self.ev(game, "document.activeElement.id"), "b1")        # wraps
        self.step(game, "up")
        self.assertEqual(self.ev(game, "document.activeElement.id"), "b4")
        self.step(game, "a")
        self.assertEqual(self.ev(game, "window.log.join(',')"), "b4")
        self.step(game, "b")
        self.assertIn("esc", self.ev(game, "window.log.join(',')"))

    def test_engine_mode_hides_pad_from_game_while_menu_open_and_hotkey_works(self):
        game = self.make({"hotkeys": {"select": "hud"}})
        self.inputs._set("a", ("t",), True)
        self.step(game)
        self.assertTrue(self.ev(game, "navigator.getGamepads()[0].buttons[0].pressed"))   # no menu: pad reaches the game
        self.inputs._set("a", ("t",), False)
        self.step(game, "select")
        self.assertIn("hud", self.ev(game, "window.log.join(',')"))                        # hotkey pressed the HUD button
        self.ev(game, "openMenu()")
        self.step(game)
        self.inputs._set("a", ("t",), True)
        game.update(1 / 60, self.inputs)
        self.assertFalse(self.ev(game, "navigator.getGamepads()[0].buttons[0].pressed"))  # menu open: pad belongs to the menu

    def test_game_mode_leaves_navigation_to_the_game(self):
        game = self.make({"navigation": "game"})
        self.ev(game, "openMenu()")
        self.step(game)
        self.assertTrue(game.ui.modal)                   # still drawn
        self.step(game, "down")
        self.assertNotEqual(self.ev(game, "document.activeElement.id"), "b2")   # the engine did not move focus
        self.inputs._set("a", ("t",), True)
        game.update(1 / 60, self.inputs)
        self.assertTrue(self.ev(game, "navigator.getGamepads()[0].buttons[0].pressed"))   # pad passes through


if __name__ == "__main__":
    unittest.main()
