import io
import os
import struct
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

import pygame
import runtime
import sgba_gfx

try:
    import sgba_js
    HAVE_JS = sgba_js.quickjs is not None
except ImportError:
    HAVE_JS = False


def png_bytes(surface: pygame.Surface) -> bytes:
    buffer = io.BytesIO()
    pygame.image.save(surface, buffer, "x.png")
    return buffer.getvalue()


def rgba(size, fill=(0, 0, 0, 0)):
    surface = pygame.Surface(size, pygame.SRCALPHA)
    surface.fill(fill)
    return surface


class GfxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_to_compact_classifies_images(self):
        solid = rgba((20, 20), (10, 200, 30, 255))
        out, kind = sgba_gfx.to_compact(solid, depth=16)
        self.assertEqual((kind, out.get_bitsize()), ("opaque", 16))
        cutout = rgba((20, 20))
        cutout.fill((200, 10, 10, 255), (5, 5, 10, 10))
        out, kind = sgba_gfx.to_compact(cutout, depth=32)
        self.assertEqual(kind, "key")
        self.assertIsNotNone(out.get_colorkey())
        canvas = pygame.Surface((20, 20), 0, 32)
        canvas.fill((1, 2, 3))
        canvas.blit(out, (0, 0))
        self.assertEqual(tuple(canvas.get_at((7, 7)))[:3], (200, 10, 10))
        self.assertEqual(tuple(canvas.get_at((1, 1)))[:3], (1, 2, 3))      # transparent area left alone

    def test_rare_translucent_edges_are_cut_but_heavy_translucency_is_kept(self):
        edge = rgba((40, 40))
        edge.fill((50, 50, 50, 255), (5, 5, 30, 30))
        edge.fill((50, 50, 50, 100), (5, 5, 30, 1))             # one faint row: under 5% of the visible pixels
        out, kind = sgba_gfx.to_compact(edge, depth=32)
        self.assertEqual(kind, "key")
        glow = rgba((40, 40), (255, 255, 255, 90))
        out, kind = sgba_gfx.to_compact(glow, depth=32)
        self.assertEqual(kind, "alpha")
        out, kind = sgba_gfx.to_compact(edge, alpha_mode="keep", depth=32)
        self.assertEqual(kind, "alpha")

    def test_magenta_art_does_not_become_transparent(self):
        art = rgba((20, 20))
        art.fill((255, 0, 255, 255), (2, 2, 8, 8))
        out, kind = sgba_gfx.to_compact(art, depth=32)
        self.assertEqual(kind, "key")
        canvas = pygame.Surface((20, 20), 0, 32)
        canvas.fill((9, 9, 9))
        canvas.blit(out, (0, 0))
        self.assertEqual(tuple(canvas.get_at((4, 4)))[:3], (255, 0, 255))   # a different key colour was chosen

    def test_image_store_is_lazy_and_evicts_least_recently_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for i in range(4):
                path = Path(tmp) / f"i{i}.png"
                path.write_bytes(png_bytes(rgba((100, 100), (i * 40, 0, 0, 255))))
                paths.append(path)
            store = sgba_gfx.ImageStore(1000, budget_bytes=2 * 100 * 100 * 4 + 10, depth=32)
            ids = [store.register_file(p)[0] for p in paths]
            self.assertEqual(store.size(ids[0]), (100, 100))
            self.assertEqual(store.decoded_count, 0)             # nothing decoded by registering
            store.get(ids[0]); store.get(ids[1])
            store.get(ids[0])                                      # touch 0 so 1 is the oldest
            store.get(ids[2])
            self.assertEqual(store.evictions, 1)
            self.assertIsNone(store.entries[ids[1]]["surface"])
            self.assertIsNotNone(store.entries[ids[0]]["surface"])
            before = store.loads
            self.assertIsNotNone(store.get(ids[1]))                # comes back from disk when needed again
            self.assertEqual(store.loads, before + 1)
            self.assertLessEqual(store.bytes, store.budget)

    def test_lru_bytes_cache(self):
        cache = sgba_gfx.LRUBytes(2000)
        for key in "abc":
            cache.put(key, pygame.Surface((10, 10), 0, 32))    # 400 bytes each
        self.assertEqual(cache.bytes, 1200)
        for key in "defg":
            cache.put(key, pygame.Surface((10, 10), 0, 32))
        self.assertLessEqual(cache.bytes, 2000)
        self.assertIsNone(cache.get("a"))
        self.assertIsNotNone(cache.get("g"))


PAGE = """<!doctype html><html><body><canvas id="view" width="160" height="120"></canvas><script>
const cv = document.getElementById('view'), x = cv.getContext('2d');
const sprite = new Image(); sprite.src = 'sprite.png';
const bg = new Image(); bg.src = 'bg.png';
let n = 0;
function frame() {
  n++;
  x.drawImage(bg, 0, 0);                                   // opaque full-canvas background
  if (n % 7 !== 0) { x.fillStyle = 'rgba(200,40,40,0.5)'; x.fillRect(10 + (n % 20), 20, 30, 10); }
  x.save(); x.globalAlpha = 0.6; x.drawImage(sprite, 0, 0, 16, 16, 60 + (n * 2) % 50, 50, 16, 16); x.restore();
  x.save(); x.translate(120, 80); x.scale(-1, 1); x.drawImage(sprite, 0, 0, 16, 16, 0, (n % 30), 32, 32); x.restore();
  const g = x.createLinearGradient(0, 0, 0, 120); g.addColorStop(0, 'rgba(0,0,60,0.5)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  x.fillStyle = g; x.fillRect(0, 0, 160, 120);
  x.fillStyle = '#fff'; x.font = '12px sans-serif'; x.fillText('f' + (n >> 2), 4, 14);
  x.save(); x.translate(80, 60); x.rotate(n * 0.05); x.drawImage(sprite, 0, 0, 16, 16, -8, -8, 16, 16); x.restore();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script></body></html>"""


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class CanvasEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def make(self, page=PAGE, config=None, **files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "index.html").write_text(page, encoding="utf-8")
        sprite = rgba((16, 16))
        for y in range(16):
            for x in range(16):
                if (x + y) % 3:
                    sprite.set_at((x, y), (30 + x * 12, 200 - y * 10, 90, 255))
        (root / "sprite.png").write_bytes(png_bytes(sprite))
        bg = pygame.Surface((160, 120))
        for y in range(120):
            bg.fill((20 + y, 40, 80 + y // 2), (0, y, 160, 1))
        (root / "bg.png").write_bytes(png_bytes(bg))
        for name, data in files.items():
            (root / name).write_bytes(data)
        if config:
            import json
            (root / "sgba_ui.json").write_text(json.dumps(config), encoding="utf-8")
        game = sgba_js.JSGame(root, save_dir=root / "save", log=lambda *a: None)
        return game

    def frames(self, game, count, skip=1):
        """Run the page, drawing every `skip`th tick. Returns the screen after each drawn frame."""
        inputs = runtime.InputState()
        screen = pygame.Surface(game.logical_size, 0, game.surface_depth)
        out = []
        for tick in range(count):
            game.update(1 / 60, inputs)
            if tick % skip == skip - 1:
                rects = game.draw(screen)
                out.append((screen.copy(), rects))
        return out

    def test_diffing_compositor_matches_immediate_drawing_exactly(self):
        retained = self.make()
        immediate = self.make()
        immediate.host.canvases[immediate.host.main_id].persistent = True       # force plain immediate drawing
        a = self.frames(retained, 45)
        b = self.frames(immediate, 45)
        for i, ((shot_a, _), (shot_b, _)) in enumerate(zip(a, b)):
            self.assertEqual(pygame.image.tobytes(shot_a, "RGB"), pygame.image.tobytes(shot_b, "RGB"), f"frame {i} differs")
        self.assertGreater(retained.host.stats.skipped + retained.host.stats.replayed, 0)

    def test_frame_skipping_shows_the_same_picture_as_drawing_every_frame(self):
        every = self.make()
        skipping = self.make()
        a = self.frames(every, 40)
        b = self.frames(skipping, 40, skip=3)
        self.assertEqual(pygame.image.tobytes(a[-1][0], "RGB"), pygame.image.tobytes(b[-1][0], "RGB")) if len(a) == len(b) else None
        # after 39 ticks (a multiple of 3 minus 1) both have run the same number of ticks
        self.assertEqual(pygame.image.tobytes(a[38][0], "RGB"), pygame.image.tobytes(b[12][0], "RGB"))
        self.assertGreater(skipping.host.stats.skipped, 0)                      # unshown frames were dropped, not drawn

    def test_static_picture_costs_nothing_and_small_changes_give_small_rectangles(self):
        page = """<canvas id="view" width="200" height="100"></canvas><script>
          const x = document.getElementById('view').getContext('2d'); let n = 0;
          (function f() { n++; x.fillStyle = '#123'; x.fillRect(0, 0, 200, 100);
            x.fillStyle = '#fa0'; x.fillRect(150, 10, 8, 8);                       // never moves
            if (n > 5) { x.fillStyle = '#0af'; x.fillRect(10 + n, 40, 6, 6); }       // starts moving
            requestAnimationFrame(f); })();
        </script>"""
        game = self.make(page)
        shots = self.frames(game, 12)
        self.assertEqual(shots[0][1], [pygame.Rect(0, 0, 200, 100)])            # first frame: everything
        self.assertEqual(shots[2][1], [])                                       # unchanged frame: nothing to draw
        moving = shots[8][1]
        self.assertTrue(moving and sum(r.w * r.h for r in moving) < 200 * 100 // 4)

    def test_partial_drawing_pages_keep_canvas_contents(self):
        page = """<canvas id="view" width="100" height="100"></canvas><script>
          const x = document.getElementById('view').getContext('2d'); let n = 0;
          (function f() { n++; x.fillStyle = 'rgb(' + (n * 20) + ',0,0)'; x.fillRect(n * 8, 10, 6, 6); requestAnimationFrame(f); })();
        </script>"""
        game = self.make(page)
        shots = self.frames(game, 6)
        last = shots[-1][0]
        self.assertEqual(tuple(last.get_at((8 * 2 + 1, 12)))[:3], (40, 0, 0))   # an earlier frame's square is still there
        self.assertEqual(tuple(last.get_at((8 * 6 + 1, 12)))[:3], (120, 0, 0))

    def test_reading_the_canvas_back_is_correct(self):
        page = """<canvas id="view" width="40" height="40"></canvas><script>
          const x = document.getElementById('view').getContext('2d');
          x.fillStyle = '#0f0'; x.fillRect(0, 0, 40, 40);
          window.px = x.getImageData(5, 5, 1, 1).data[1];
        </script>"""
        game = self.make(page)
        self.assertEqual(game.host.ctx.eval("window.px"), 255)

    def test_gradient_filled_text_is_drawn(self):
        page = """<canvas id="view" width="120" height="40"></canvas><script>
          const x = document.getElementById('view').getContext('2d');
          x.fillStyle = '#000'; x.fillRect(0, 0, 120, 40);
          const g = x.createLinearGradient(0, 0, 0, 40); g.addColorStop(0, '#ff0'); g.addColorStop(1, '#f00');
          x.font = 'bold 28px sans-serif'; x.textBaseline = 'top'; x.fillStyle = g; x.fillText('HI', 4, 2);
        </script>"""
        game = self.make(page)
        shot = self.frames(game, 2)[-1][0]
        lit = [tuple(shot.get_at((x, y)))[:3] for x in range(4, 40) for y in range(2, 34) if sum(shot.get_at((x, y))[:3]) > 60]
        self.assertGreater(len(lit), 40)                         # the letters were filled, not skipped
        self.assertGreater(max(r for r, g, b in lit), 200)       # in the gradient's red/yellow, not plain white
        self.assertTrue(all(r >= g for r, g, b in lit))          # yellow (top) to red (bottom): never bluer or greener than red

    def test_overlay_menu_forces_a_full_redraw_then_goes_quiet(self):
        page = """<div id="stage"><canvas id="view" width="64" height="48"></canvas>
          <div id="menu" hidden><button id="b">Go</button></div></div><script>
          const x = document.getElementById('view').getContext('2d');
          (function f() { x.fillStyle = '#336'; x.fillRect(0, 0, 64, 48); requestAnimationFrame(f); })();
          window.show = () => { document.getElementById('menu').hidden = false; };
        </script>"""
        game = self.make(page, config={"navigation": "game"})
        screen = pygame.Surface(game.logical_size, 0, game.surface_depth)
        inputs = runtime.InputState()
        game.update(1 / 60, inputs)
        self.assertEqual(game.draw(screen), [screen.get_rect()])
        game.update(1 / 60, inputs)
        self.assertEqual(game.draw(screen), [])                                   # nothing moved, nothing to draw
        plain = tuple(screen.get_at((2, 2)))[:3]
        game.host.ctx.eval("window.show()")
        game.update(1 / 60, inputs)
        self.assertEqual(game.draw(screen), [screen.get_rect()])                  # the menu appeared: redraw everything
        self.assertLess(sum(tuple(screen.get_at((2, 2)))[:3]), sum(plain))        # and the background is dimmed behind it
        game.update(1 / 60, inputs)
        self.assertEqual(game.draw(screen), [])

    def test_preload_decodes_listed_images_before_play(self):
        page = """<canvas id="view" width="16" height="16"></canvas><script>
          window.a = new Image(); a.src = 'sprite.png'; window.b = new Image(); b.src = 'bg.png';
        </script>"""
        game = self.make(page, config={"preload": ["sprite.*"]})
        store = game.host.images
        self.assertEqual(store.decoded_count, 1)
        self.assertEqual(store.loads, 1)


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class RuntimePresentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        cls.display = pygame.display.set_mode((64, 48))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def test_run_scene_presents_only_changed_rectangles(self):
        class Game:
            retained = True
            surface_depth = 32
            calls = 0

            def update(self, dt, inputs):
                pass

            def draw(self, surface):
                Game.calls += 1
                if Game.calls == 1:
                    surface.fill((10, 20, 30))
                    return [surface.get_rect()]
                if Game.calls == 2:
                    return []                                   # nothing changed
                if Game.calls == 3:
                    surface.fill((200, 0, 0), (4, 4, 8, 8))
                    return [pygame.Rect(4, 4, 8, 8)]
                pygame.event.post(pygame.event.Event(pygame.QUIT))
                return []
        updates, flips = [], []
        with mock.patch("pygame.display.update", side_effect=lambda r=None: updates.append(r)), \
                mock.patch("pygame.display.flip", side_effect=lambda: flips.append(1)):
            result = runtime.run_scene(self.display, runtime.InputState(), Game(), (64, 48))
        self.assertEqual(result, "closed")
        self.assertEqual(updates[0], [pygame.Rect(0, 0, 64, 48)])
        self.assertEqual(updates[1], [pygame.Rect(4, 4, 8, 8)])
        self.assertEqual(len(updates), 2)                       # the "nothing changed" frame sent nothing to the display
        self.assertEqual(tuple(self.display.get_at((6, 6)))[:3], (200, 0, 0))
        self.assertEqual(tuple(self.display.get_at((30, 30)))[:3], (10, 20, 30))


def wav_bytes(seconds: float, rate: int = 22050) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<h", 800) * int(rate * seconds))
    return buffer.getvalue()


@unittest.skipUnless(HAVE_JS, "quickjs-ng is not installed")
class AudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.display.set_mode((1, 1))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    PAGE = """<canvas id="view" width="20" height="20"></canvas><script>
      const ctx = new AudioContext(), master = ctx.createGain(); master.gain.value = 0.5; master.connect(ctx.destination);
      window.out = [];
      async function load(name) { const r = await fetch(name); return ctx.decodeAudioData(await r.arrayBuffer()); }
      (async () => {
        const music = await load('music.wav'), sfx = await load('sfx.wav');
        window.dur = music.duration;
        const g = ctx.createGain(), s = ctx.createBufferSource(); s.buffer = music; s.loop = true;
        g.gain.setValueAtTime(0, ctx.currentTime); g.gain.linearRampToValueAtTime(1, ctx.currentTime + 1);
        s.connect(g); g.connect(master); s.start(0); window.music = s;
        const e = ctx.createBufferSource(); e.buffer = sfx; e.connect(master); e.start(0); window.sfx = e;
        e.onended = () => out.push('sfx ended');
      })();
      const a = new Audio('sfx.wav'); a.volume = 0.25; window.audio = a;
    </script>"""

    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "index.html").write_text(self.PAGE, encoding="utf-8")
        (root / "music.wav").write_bytes(wav_bytes(3.0, 22050))           # ~132 KB: streamed
        (root / "sfx.wav").write_bytes(wav_bytes(0.1, 22050))             # small: decoded and cached
        return sgba_js.JSGame(root, save_dir=root / "save", log=lambda *a: None)

    def test_music_streams_effects_decode_and_gain_follows_automation(self):
        game = self.make()
        inputs = runtime.InputState()
        for _ in range(3):
            game.update(1 / 60, inputs)
        engine = game.host.audio
        self.assertTrue(engine.ready)
        kinds = sorted(src.stream for src in engine.sources.values())
        self.assertEqual(kinds, [False, True])                              # one effect decoded, one track streamed
        self.assertEqual(engine.stats, {"streamed": 1, "decoded": 1})
        self.assertAlmostEqual(game.host.ctx.eval("window.dur"), 3.0, places=1)
        stream = next(s for s in engine.sources.values() if s.stream)
        self.assertLess(stream.gain, 0.05)                                  # starts silent (ramp from 0)
        for _ in range(60):                                                 # one second of game time
            game.update(1 / 60, inputs)
        stream = next(s for s in engine.sources.values() if s.stream)
        self.assertAlmostEqual(stream.gain, 0.5, delta=0.06)                # ramped to 1 then halved by the master gain
        game.host.ctx.eval("window.music.stop()")
        game.update(1 / 60, inputs)
        self.assertFalse(any(s.stream for s in engine.sources.values()))

    def test_audio_element_plays_at_its_volume(self):
        game = self.make()
        inputs = runtime.InputState()
        game.update(1 / 60, inputs)
        game.host.ctx.eval("window.audio.play()")
        game.update(1 / 60, inputs)
        engine = game.host.audio
        self.assertTrue(any(abs(s.gain - 0.25) < 0.01 for s in engine.sources.values()))
        game.host.ctx.eval("window.audio.pause()")
        self.assertEqual(game.host.ctx.eval("window.audio.paused"), True)

    def test_ogg_duration_reader_rejects_other_files(self):
        import sgba_audio
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "x.ogg"
            junk.write_bytes(b"not an ogg file")
            self.assertEqual(sgba_audio.ogg_duration(junk), 0.0)
            wav = Path(tmp) / "y.wav"
            wav.write_bytes(wav_bytes(0.5))
            self.assertAlmostEqual(sgba_audio.duration_of(wav), 0.5, places=2)


if __name__ == "__main__":
    unittest.main()
