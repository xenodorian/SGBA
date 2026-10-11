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


if __name__ == "__main__":
    unittest.main()
