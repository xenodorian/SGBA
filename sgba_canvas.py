"""Canvas 2D for SGBA's JavaScript host, built like a small console video chip.

Every drawing call becomes a recorded op: (signature, bounding box, draw function, covers-everything flag,
clip). The main canvas keeps the previous frame's op list. At the end of a frame the two lists are compared;
only the screen regions touched by ops that changed are redrawn, and only those regions are sent to the
display. A frame that draws exactly what the last one did costs almost nothing, much like a GBA that leaves
its tile and sprite memory alone.

This is only valid when the page repaints the whole canvas each frame (its first op covers the canvas). If it
does not, or it reads the canvas back, the canvas falls back to ordinary immediate drawing, which is always
correct.
"""
from __future__ import annotations

import json
import math
import re
from collections import OrderedDict

import pygame

from sgba_gfx import LRUBytes, fade

IMAGE_ID_BASE = 1_000_000          # ids at or above this are loaded images, below are canvases (same value as sgba_js)

def mult_ready(tile):
    """Bake per-pixel alpha into an opaque tile so BLEND_RGB_MULT honors it (c' = 255 - (255 - c) * a)."""
    base = pygame.Surface(tile.get_size())
    if pygame.display.get_surface() is not None:
        base = base.convert()
    base.fill((255, 255, 255))
    base.blit(tile, (0, 0))
    return base


MULT = pygame.BLEND_RGB_MULT
COMPOSITE_FLAGS = {"lighter": pygame.BLEND_RGB_ADD, "multiply": pygame.BLEND_RGB_MULT}
MAX_DIRTY_RECTS = 6
FULL_REDRAW_FRACTION = 0.6

_color_cache: dict = {}


def parse_color(text: str):
    """CSS color string -> (r, g, b, a) with a in 0..255."""
    cached = _color_cache.get(text)
    if cached is not None:
        return cached
    s = text.strip().lower()
    result = (0, 0, 0, 255)
    try:
        if s.startswith("#"):
            h = s[1:]
            if len(h) in (3, 4):
                h = "".join(c * 2 for c in h)
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            a = int(h[6:8], 16) if len(h) >= 8 else 255
            result = (r, g, b, a)
        elif s.startswith("rgb"):
            parts = [p.strip() for p in re.split(r"[,\s/()]+", s[s.index("(") + 1:].rstrip(") ")) if p.strip()]
            vals = [round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)) for p in parts[:3]]
            a = 255
            if len(parts) > 3:
                a = round(float(parts[3][:-1]) * 2.55) if parts[3].endswith("%") else round(float(parts[3]) * 255)
            result = tuple(max(0, min(255, v)) for v in vals) + (max(0, min(255, a)),)
        elif s.startswith("hsl"):
            parts = [p for p in re.split(r"[,\s/()]+", s[s.index("(") + 1:].rstrip(") ")) if p]
            c = pygame.Color(0, 0, 0)
            c.hsla = (float(parts[0].rstrip("deg")) % 360, float(parts[1].rstrip("%")), float(parts[2].rstrip("%")), 100)
            a = round(float(parts[3]) * 255) if len(parts) > 3 else 255
            result = (c.r, c.g, c.b, a)
        elif s == "transparent":
            result = (0, 0, 0, 0)
        else:
            c = pygame.Color(s)
            result = (c.r, c.g, c.b, c.a)
    except (ValueError, IndexError):
        result = (0, 0, 0, 255)
    if len(_color_cache) < 4096:
        _color_cache[text] = result
    return result


def _lerp_stops(stops, t):
    if not stops:
        return (0, 0, 0, 0)
    if t <= stops[0][0]:
        return stops[0][1]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            k = 0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(round(c0[i] + (c1[i] - c0[i]) * k) for i in range(4))
    return stops[-1][1]


def merge_rects(rects, limit: int = MAX_DIRTY_RECTS, margin: int = 6):
    """Combine overlapping or nearby rectangles; if too many remain, use one bounding box."""
    rects = [r.copy() for r in rects if r.w > 0 and r.h > 0]
    changed = True
    while changed and len(rects) > 1:
        changed = False
        out: list = []
        for r in rects:
            for i, o in enumerate(out):
                if o.inflate(margin * 2, margin * 2).colliderect(r):
                    out[i] = o.union(r)
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    if len(rects) > limit:
        rects = [rects[0].unionall(rects[1:])]
    return rects


class Op:
    """One recorded drawing command."""
    __slots__ = ("sig", "box", "fn", "covers", "clip")

    def __init__(self, sig, box, fn, covers=False, clip=None):
        self.sig, self.box, self.fn, self.covers, self.clip = sig, box, fn, covers, clip


class Canvas2D:
    """Canvas 2D drawing state plus the op recorder for one surface."""

    def __init__(self, host, canvas_id: int, surface: pygame.Surface, retained: bool = False) -> None:
        self.host = host
        self.id = canvas_id
        self.surface = surface            # (property: also caches the bounds rectangle)
        self.retained = retained
        self.opaque = surface.get_bytesize() != 4 or not (surface.get_flags() & pygame.SRCALPHA)
        self.version = 0
        self.s = self._fresh_state()
        self.stack: list = []
        self.path: list = []
        self.pending: list = []           # ops recorded this frame (retained canvases)
        self.prev: list | None = None     # last frame's ops when it was a full repaint
        self.persistent = False           # True once retained drawing is unsafe for this canvas
        self.groups: OrderedDict = OrderedDict()    # recorded display lists the page asked to reuse (see group_begin)
        self._grp = None

    @staticmethod
    def _fresh_state() -> dict:
        return {"fill": (0, 0, 0, 255), "stroke": (0, 0, 0, 255), "alpha": 1.0, "lw": 1.0, "font": (10, False, False),
                "align": "start", "baseline": "alphabetic", "comp": "source-over", "m": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                "clip": None}

    def reset_state(self) -> None:
        self.s = self._fresh_state()
        self.stack = []
        self.path = []

    def make_retained(self, opaque_surface: pygame.Surface) -> None:
        """Turn this into the page's main canvas: opaque, and drawn through the frame-diffing recorder."""
        self.surface = opaque_surface
        self.retained = True
        self.opaque = True
        self.persistent = False
        self.prev = None
        self.pending = []
        self.stale = None

    @property
    def surface(self) -> pygame.Surface:
        return self._surface

    @surface.setter
    def surface(self, value: pygame.Surface) -> None:
        self._surface = value
        self._bounds = value.get_rect()

    @property
    def rect(self) -> pygame.Rect:
        return self._bounds.copy()

    # ------------------------------------------------------------ recording
    def emit(self, sig, box: pygame.Rect, fn, covers: bool = False) -> None:
        clip = self.s["clip"]
        if clip is not None:
            box = box.clip(clip)
            sig = sig + (tuple(clip),)
        box = box.clip(self._bounds)
        if box.w <= 0 or box.h <= 0:
            return
        op = Op(sig, box, fn, covers, clip)
        if self.retained and not self.persistent:
            self.pending.append(op)
            self.host.stats.ops += 1
        else:
            self._run(op, None)
            self.version += 1
            self.touched = True

    touched = False

    def _run(self, op: Op, region) -> None:
        surface = self.surface
        clip = op.clip
        if region is not None:
            clip = region if clip is None else region.clip(clip)
        surface.set_clip(clip)
        kind = op.fn(surface)
        if kind:
            area = op.box if clip is None else op.box.clip(clip)
            px = area.w * area.h
            stats = self.host.stats
            stats.blits += 1
            if kind == "alpha":
                stats.alpha_px += px
            elif kind == "key":
                stats.key_px += px
            elif kind == "fill":
                stats.fill_px += px
            else:
                stats.copy_px += px

    def go_persistent(self) -> None:
        """Stop retained mode: draw anything pending now and from here on draw immediately."""
        if self.persistent:
            return
        pending = [op for frame in (self.stale or []) for op in frame] + self.pending
        self.pending, self.stale = [], []
        self.persistent = True
        self.prev = None
        for op in pending:
            self._run(op, None)
        self.surface.set_clip(None)
        self.version += 1
        self.touched = True

    force_full = False
    stale: list = None          # frames recorded by earlier ticks that were never shown

    def begin_frame(self) -> None:
        """Called before the page runs a new tick: set aside what the last tick drew. Whether it is shown or dropped
        is decided when a frame is composited, once it is known whether a newer complete repaint exists."""
        if not self.retained or self.persistent or not self.pending:
            return
        if self.stale is None:
            self.stale = []
        self.stale.append(self.pending)
        self.pending = []
        if len(self.stale) > 8:                      # a page that never repaints fully: apply old frames now
            self._flush_stale()

    def _flush_stale(self) -> None:
        for frame in self.stale or []:
            for op in frame:
                self._run(op, None)
        self.surface.set_clip(None)
        self.stale = []
        self.prev = None
        self.force_full = True
        self.version += 1

    def end_frame(self):
        """Composite the newest frame(s): returns the list of changed rectangles (empty = nothing changed)."""
        stats = self.host.stats
        frames, self.stale = (self.stale or []), []
        if self.pending:
            frames.append(self.pending)
        self.pending = []
        if not self.retained or self.persistent:
            touched, self.touched = self.touched or any(frames), False
            return [self.rect] if touched else []
        frames = [f for f in frames if f]
        if not frames:
            if self.force_full:
                self.force_full = False
                return [self.rect]
            return []
        # a complete repaint makes every earlier frame irrelevant: show only the newest one
        for i in range(len(frames) - 1, -1, -1):
            if self._fully_covered([op.box for op in frames[i] if op.covers]):
                stats.skipped += i
                frames = frames[i:]
                break
        out: list = []
        for ops in frames:
            out.extend(self._composite(ops))
        if self.force_full:
            self.force_full = False
            return [self.rect]
        return out

    def _composite(self, ops: list):
        stats = self.host.stats
        if self.force_full:
            self.prev = None
        full = self.rect
        if not self._fully_covered([op.box for op in ops if op.covers]):
            for op in ops:               # not a full repaint: behave like a normal persistent canvas
                self._run(op, None)
            self.surface.set_clip(None)
            self.prev = None
            self.version += 1
            return [full]
        prev = self.prev
        self.prev = ops
        if prev is None:
            for op in ops:
                self._run(op, None)
            self.surface.set_clip(None)
            self.version += 1
            stats.replayed += len(ops)
            return [full]
        changed: list = []
        for i in range(max(len(prev), len(ops))):
            a = prev[i] if i < len(prev) else None
            b = ops[i] if i < len(ops) else None
            if a is None or b is None or a.sig != b.sig:
                if a is not None:
                    changed.append(a.box)
                if b is not None:
                    changed.append(b.box)
        if not changed:
            stats.skipped += 1
            return []
        regions = merge_rects(changed)
        area = sum(r.w * r.h for r in regions)
        if area > FULL_REDRAW_FRACTION * full.w * full.h:
            regions = [full]
            area = full.w * full.h
        for region in regions:
            for op in ops:
                if op.box.colliderect(region):
                    self._run(op, region)
                    stats.replayed += 1
        self.surface.set_clip(None)
        stats.dirty_px += area
        self.version += 1
        return regions

    _cover_cache: dict = {}

    def _fully_covered(self, boxes) -> bool:
        """True if the opaque ops of a frame together paint every pixel, so replaying a region needs no clearing."""
        full = self.rect
        if any(b.contains(full) for b in boxes):
            return True
        key = (tuple(tuple(b) for b in boxes), full.size)
        known = Canvas2D._cover_cache.get(key)
        if known is None:
            coverage = pygame.Surface(full.size, 0, 8)
            coverage.fill(0)
            for b in boxes:
                coverage.fill(1, b)
            coverage.set_colorkey(0)
            known = pygame.mask.from_surface(coverage).count() == full.w * full.h
            if len(Canvas2D._cover_cache) > 64:
                Canvas2D._cover_cache.clear()
            Canvas2D._cover_cache[key] = known
        return known

    # ------------------------------------------------------------ display lists
    GROUP_LIMIT = 600            # recorded groups kept
    GROUP_MAX_OPS = 400          # a group longer than this is not worth keeping

    def group_begin(self, key) -> int:
        """The page is about to draw something that depends only on `key` and the current drawing state.
        If the same group was recorded before, its ops are appended now and the page skips drawing it (1).
        Otherwise recording starts and the page draws it as usual, then calls group_end (0).
        2 means the group is not tracked (nested in another group, or this canvas is not retained): just draw it."""
        if not self.retained or self.persistent or self._grp is not None:
            return 2
        s = self.s
        clip = s["clip"]
        sig = (key, s["m"], s["alpha"], s["comp"], s["fill"], s["stroke"], s["lw"], s["font"], s["align"], s["baseline"],
               None if clip is None else tuple(clip))
        ops = self.groups.get(sig)
        if ops is not None:
            self.groups.move_to_end(sig)
            self.pending.extend(ops)
            self.host.stats.ops += len(ops)
            return 1
        self._grp = (sig, len(self.pending))
        return 0

    def group_end(self) -> None:
        grp, self._grp = self._grp, None
        if grp is None or self.persistent or not self.retained:
            return
        sig, start = grp
        ops = self.pending[start:]
        if not ops or len(ops) > self.GROUP_MAX_OPS:
            return
        for op in ops:        # a picture taken from another canvas can change without the op noticing: do not keep those
            kind = op.sig[0]
            if (kind == "I" or kind == "R" or kind == "H") and op.sig[1] < IMAGE_ID_BASE:
                return
        self.groups[sig] = ops
        if len(self.groups) > self.GROUP_LIMIT:
            self.groups.popitem(last=False)

    # ------------------------------------------------------------ state
    def set_prop(self, name: str, value) -> None:
        s = self.s
        if name in ("fillStyle", "strokeStyle"):
            s["fill" if name == "fillStyle" else "stroke"] = self._parse_style(str(value))
        elif name == "globalAlpha":
            s["alpha"] = max(0.0, min(1.0, float(value)))
        elif name == "lineWidth":
            s["lw"] = max(0.0, float(value))
        elif name == "font":
            s["font"] = self._parse_font(str(value))
        elif name == "textAlign":
            s["align"] = str(value)
        elif name == "textBaseline":
            s["baseline"] = str(value)
        elif name == "globalCompositeOperation":
            s["comp"] = str(value)

    @staticmethod
    def _parse_style(text: str):
        if text.startswith("{"):
            try:
                data = json.loads(text)
                stops = tuple(sorted(((float(o), parse_color(c)) for o, c in data["s"]), key=lambda x: x[0]))
                return ("grad", data["g"], tuple(float(v) for v in data["a"]), stops)
            except (ValueError, KeyError, TypeError):
                return (0, 0, 0, 255)
        return parse_color(text)

    @staticmethod
    def _parse_font(text: str):
        m = re.search(r"(\d+(?:\.\d+)?)\s*px", text)
        return (float(m.group(1)) if m else 10.0, "bold" in text or "700" in text or "800" in text, "italic" in text)

    def m_save(self) -> None:
        self.stack.append(dict(self.s))

    def m_restore(self) -> None:
        if self.stack:
            self.s = self.stack.pop()

    def _mul(self, n) -> None:
        a, b, c, d, e, f = self.s["m"]
        na, nb, nc, nd, ne, nf = n
        self.s["m"] = (a * na + c * nb, b * na + d * nb, a * nc + c * nd, b * nc + d * nd, a * ne + c * nf + e, b * ne + d * nf + f)

    def m_translate(self, x, y) -> None:
        self._mul((1, 0, 0, 1, x, y))

    def m_scale(self, x, y) -> None:
        self._mul((x, 0, 0, y, 0, 0))

    def m_rotate(self, t) -> None:
        c, s = math.cos(t), math.sin(t)
        self._mul((c, s, -s, c, 0, 0))

    def m_transform(self, a, b, c, d, e, f) -> None:
        self._mul((a, b, c, d, e, f))

    def m_setTransform(self, a=1, b=0, c=0, d=1, e=0, f=0) -> None:
        self.s["m"] = (float(a), float(b), float(c), float(d), float(e), float(f))

    def m_resetTransform(self) -> None:
        self.s["m"] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    def _pt(self, x, y):
        a, b, c, d, e, f = self.s["m"]
        return (a * x + c * y + e, b * x + d * y + f)

    def _axis_aligned(self) -> bool:
        m = self.s["m"]
        return m[1] == 0 and m[2] == 0

    def _flags(self) -> int:
        return COMPOSITE_FLAGS.get(self.s["comp"], 0)

    def _color(self, style):
        a = style[3] * self.s["alpha"]
        return (style[0], style[1], style[2], int(a))

    def _device_rect(self, x, y, w, h) -> pygame.Rect:
        x0, y0 = self._pt(x, y)
        x1, y1 = self._pt(x + w, y + h)
        left, right = sorted((round(x0), round(x1)))
        top, bottom = sorted((round(y0), round(y1)))
        return pygame.Rect(left, top, right - left, bottom - top)

    # ------------------------------------------------------------ rectangles
    def m_fillRect(self, x, y, w, h) -> None:
        s = self.s
        style = s["fill"]
        m = s["m"]
        if m[1] == 0.0 and m[2] == 0.0 and s["comp"] == "source-over" and style[0] != "grad":
            # the common case, kept short: a plain solid rectangle
            alpha = int(style[3] * s["alpha"])
            if alpha <= 0:
                return
            ma, md, me, mf = m[0], m[3], m[4], m[5]
            x0 = round(ma * x + me)
            x1 = round(ma * (x + w) + me)
            y0 = round(md * y + mf)
            y1 = round(md * (y + h) + mf)
            if x1 < x0:
                x0, x1 = x1, x0
            if y1 < y0:
                y0, y1 = y1, y0
            x1 -= x0
            y1 -= y0
            if x1 <= 0 or y1 <= 0:
                return
            rect = pygame.Rect(x0, y0, x1, y1)
            r, g, b = style[0], style[1], style[2]
            if alpha >= 255:
                color = (r, g, b)

                def draw_fill(surface, rect=rect, color=color):
                    surface.fill(color, rect)
                    return "fill"
                self.emit(("F", (x0, y0, x1, y1), r, g, b, 255, 0), rect, draw_fill, True)
            else:
                host = self.host

                def draw_tile(surface, rect=rect, rgba=(r, g, b, alpha)):
                    surface.blit(host.solid_tile(rect.size, rgba), rect.topleft)
                    return "alpha"
                self.emit(("F", (x0, y0, x1, y1), r, g, b, alpha, 0), rect, draw_tile)
            return
        if self._axis_aligned():
            rect = self._device_rect(x, y, w, h)
            self._paint(rect, style)
        else:
            self._fill_polygon([self._pt(x, y), self._pt(x + w, y), self._pt(x + w, y + h), self._pt(x, y + h)], style)

    def _paint(self, rect: pygame.Rect, style) -> None:
        if rect.w <= 0 or rect.h <= 0:
            return
        flags = self._flags()
        host = self.host
        if style[0] == "grad":
            alpha = self.s["alpha"]

            def draw_gradient(surface, rect=rect, style=style, alpha=alpha, flags=flags):
                tile = host.gradient_tile(rect.size, rect.topleft, style, alpha, flags == MULT)
                surface.blit(tile, rect.topleft, special_flags=flags)
                return "alpha"
            self.emit(("G", tuple(rect), style, alpha, flags), rect, draw_gradient)
            return
        r, g, b, a = self._color(style)
        if self.s["comp"] == "source-in":
            def draw_source_in(surface, rect=rect, r=r, g=g, b=b, a=a):
                surface.fill((0, 0, 0, a), rect, pygame.BLEND_RGBA_MULT)    # keep only where the canvas already has pixels
                surface.fill((r, g, b, 0), rect, pygame.BLEND_RGBA_ADD)
                return "alpha"
            self.emit(("SI", tuple(rect), r, g, b, a), rect, draw_source_in)
            return
        if self.s["comp"] == "source-atop":
            if a <= 0:
                return

            def draw_source_atop(surface, rect=rect, r=r, g=g, b=b, a=a):
                keep = 255 - a                                          # wash the pixels already there, leave transparent ones alone
                surface.fill((keep, keep, keep, 255), rect, pygame.BLEND_RGBA_MULT)
                surface.fill((r * a // 255, g * a // 255, b * a // 255, 0), rect, pygame.BLEND_RGBA_ADD)
                return "alpha"
            self.emit(("SA", tuple(rect), r, g, b, a), rect, draw_source_atop)
            return
        if a <= 0:
            return
        covers = a >= 255 and not flags

        def draw_solid(surface, rect=rect, r=r, g=g, b=b, a=a, flags=flags):
            if a >= 255 and not flags:
                surface.fill((r, g, b), rect)
                return "fill"
            tile = host.solid_tile(rect.size, (r, g, b, a), flags == MULT)
            surface.blit(tile, rect.topleft, special_flags=flags)
            return "alpha"
        self.emit(("F", tuple(rect), r, g, b, a, flags), rect, draw_solid, covers)

    def m_strokeRect(self, x, y, w, h) -> None:
        pts = [self._pt(x, y), self._pt(x + w, y), self._pt(x + w, y + h), self._pt(x, y + h)]
        self._stroke_points(pts, True, self.s["stroke"])

    def m_clearRect(self, x, y, w, h) -> None:
        rect = self._device_rect(x, y, w, h).clip(self.rect)
        if rect.w <= 0 or rect.h <= 0:
            return
        color = (0, 0, 0) if self.opaque else (0, 0, 0, 0)
        covers = self.opaque

        def draw(surface, rect=rect, color=color):
            surface.fill(color, rect)
            return "fill"
        self.emit(("C", tuple(rect)), rect, draw, covers)

    # ------------------------------------------------------------ paths
    def m_beginPath(self) -> None:
        self.path = []

    def m_moveTo(self, x, y) -> None:
        self.path.append([self._pt(x, y)])

    def m_lineTo(self, x, y) -> None:
        if not self.path:
            self.path.append([])
        self.path[-1].append(self._pt(x, y))

    def m_closePath(self) -> None:
        if self.path and self.path[-1]:
            self.path[-1].append(self.path[-1][0])

    def m_rect(self, x, y, w, h) -> None:
        self.path.append([self._pt(x, y), self._pt(x + w, y), self._pt(x + w, y + h), self._pt(x, y + h), self._pt(x, y)])

    def m_arc(self, x, y, r, start, end, ccw=False) -> None:
        self.m_ellipse(x, y, r, r, 0, start, end, ccw)

    def m_ellipse(self, x, y, rx, ry, rot, start, end, ccw=False) -> None:
        if ccw and end > start:
            end -= 2 * math.pi * math.ceil((end - start) / (2 * math.pi))
        elif not ccw and end < start:
            end += 2 * math.pi * math.ceil((start - end) / (2 * math.pi))
        span = end - start
        if abs(span) > 2 * math.pi:
            span = 2 * math.pi if span > 0 else -2 * math.pi
        steps = max(8, int(abs(span) / (2 * math.pi) * 32))
        pts = []
        for i in range(steps + 1):
            t = start + span * i / steps
            px, py = rx * math.cos(t), ry * math.sin(t)
            if rot:
                px, py = px * math.cos(rot) - py * math.sin(rot), px * math.sin(rot) + py * math.cos(rot)
            pts.append(self._pt(x + px, y + py))
        if self.path and self.path[-1]:
            self.path[-1].extend(pts)
        else:
            self.path.append(pts)

    def m_arcTo(self, x1, y1, x2, y2, r) -> None:
        self.m_lineTo(x1, y1)

    def m_quadraticCurveTo(self, cx, cy, x, y) -> None:
        if not self.path or not self.path[-1]:
            return self.m_moveTo(x, y)
        p0 = self.path[-1][-1]
        c, p1 = self._pt(cx, cy), self._pt(x, y)
        for i in range(1, 9):
            t = i / 8
            self.path[-1].append(((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0],
                                  (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]))

    def m_bezierCurveTo(self, c1x, c1y, c2x, c2y, x, y) -> None:
        if not self.path or not self.path[-1]:
            return self.m_moveTo(x, y)
        p0 = self.path[-1][-1]
        c1, c2, p1 = self._pt(c1x, c1y), self._pt(c2x, c2y), self._pt(x, y)
        for i in range(1, 11):
            t = i / 10
            u = 1 - t
            self.path[-1].append((u ** 3 * p0[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * p1[0],
                                  u ** 3 * p0[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * p1[1]))

    def m_fill(self, *_rule) -> None:
        for sub in self.path:
            self._fill_polygon(sub, self.s["fill"])

    def m_stroke(self) -> None:
        for sub in self.path:
            self._stroke_points(sub, len(sub) > 2 and sub[0] == sub[-1], self.s["stroke"])

    def m_clip(self, *_rule) -> None:
        xs = [p[0] for sub in self.path for p in sub]
        ys = [p[1] for sub in self.path for p in sub]
        if xs:
            rect = pygame.Rect(math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs) - min(xs)), math.ceil(max(ys) - min(ys)))
            current = self.s["clip"]
            self.s["clip"] = rect if current is None else rect.clip(current)

    def m_setLineDash(self, *_a) -> None:
        pass

    def _fill_polygon(self, pts, style) -> None:
        if len(pts) < 3 or style[0] == "grad":
            return
        color = self._color(style)
        if color[3] <= 0:
            return
        qpts = tuple((round(x * 4) / 4, round(y * 4) / 4) for x, y in pts)
        xs = [p[0] for p in qpts]
        ys = [p[1] for p in qpts]
        left, top = math.floor(min(xs)), math.floor(min(ys))
        w, h = math.ceil(max(xs)) - left + 1, math.ceil(max(ys)) - top + 1
        if w <= 0 or h <= 0 or w > 4096 or h > 4096:
            return
        flags = self._flags()

        def draw(surface, qpts=qpts, color=color, left=left, top=top, w=w, h=h, flags=flags):
            local = [(x - left, y - top) for x, y in qpts]
            if color[3] >= 255 and not flags:
                pygame.draw.polygon(surface, color[:3], [(x + left, y + top) for x, y in local])
                return "fill"
            tile = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.polygon(tile, color, local)
            if flags == MULT:
                tile = mult_ready(tile)
            surface.blit(tile, (left, top), special_flags=flags)
            return "alpha"
        self.emit(("P", qpts, color, flags), pygame.Rect(left, top, w, h), draw)

    def _stroke_points(self, pts, closed, style) -> None:
        if len(pts) < 2 or style[0] == "grad":
            return
        a, b, c, d = self.s["m"][:4]
        width = max(1, round(self.s["lw"] * math.sqrt(abs(a * d - b * c)) or 1))
        color = self._color(style)
        if color[3] <= 0:
            return
        qpts = tuple((round(x), round(y)) for x, y in pts)
        xs = [p[0] for p in qpts]
        ys = [p[1] for p in qpts]
        left, top = min(xs) - width, min(ys) - width
        w, h = max(xs) - left + width + 1, max(ys) - top + width + 1
        if w <= 0 or h <= 0 or w > 4096 or h > 4096:
            return
        flags = self._flags()

        def draw(surface, qpts=qpts, closed=closed, color=color, width=width, left=left, top=top, w=w, h=h, flags=flags):
            if color[3] >= 255 and not flags:
                pygame.draw.lines(surface, color[:3], closed, qpts, width)
                return "fill"
            tile = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.lines(tile, color, closed, [(x - left, y - top) for x, y in qpts], width)
            if flags == MULT:
                tile = mult_ready(tile)
            surface.blit(tile, (left, top), special_flags=flags)
            return "alpha"
        self.emit(("L", qpts, closed, color, width, flags), pygame.Rect(left, top, w, h), draw)

    # ------------------------------------------------------------ images
    def m_drawImage(self, src_id, *a) -> None:
        host = self.host
        src_id = int(src_id)
        st = self.s
        m = st["m"]
        n = len(a)
        if src_id >= IMAGE_ID_BASE and (n == 8 or n == 2) and m[1] == 0.0 and m[2] == 0.0 and st["comp"] == "source-over":
            # the common case, kept short: a whole-pixel crop of an image drawn upright, no edge adjustments needed
            e = host.images.entries.get(src_id)
            if e is None:
                return
            bw, bh = e["w"], e["h"]
            if n == 8:
                sx, sy, sw, sh, dx, dy, dw, dh = a
            else:
                sx, sy, sw, sh = 0, 0, bw, bh
                dx, dy = a
                dw, dh = bw, bh
            if (type(sx) is int and type(sy) is int and type(sw) is int and type(sh) is int and sx >= 0 and sy >= 0
                    and 0 < sw and 0 < sh and sx + sw <= bw and sy + sh <= bh and dw != 0 and dh != 0):
                ma, md, me, mf = m[0], m[3], m[4], m[5]
                x0 = ma * dx + me
                x1 = ma * (dx + dw) + me
                y0 = md * dy + mf
                y1 = md * (dy + dh) + mf
                flip_x = x1 < x0
                flip_y = y1 < y0
                left = round(x1 if flip_x else x0)
                top = round(y1 if flip_y else y0)
                width = round(abs(x1 - x0))
                height = round(abs(y1 - y0))
                if width < 1:
                    width = 1
                if height < 1:
                    height = 1
                alpha = int(st["alpha"] * 255)
                kind = e["kind"] or "key"
                box = pygame.Rect(left, top, width, height)

                def draw_fast(surface, src_id=src_id, sx=sx, sy=sy, sw=sw, sh=sh, left=left, top=top, width=width, height=height,
                              flip_x=flip_x, flip_y=flip_y, alpha=alpha, kind=kind):
                    image = host.source_surface(src_id)
                    if image is None:
                        return None
                    if width == sw and height == sh and not flip_x and not flip_y and alpha >= 255:
                        surface.blit(image, (left, top), pygame.Rect(sx, sy, sw, sh))
                    else:
                        surface.blit(host.variant(src_id, image, (sx, sy, sw, sh), (width, height), flip_x, flip_y, alpha), (left, top))
                    return "alpha" if (alpha < 255 or kind == "alpha") else kind
                self.emit(("I", src_id, 0, sx, sy, sw, sh, left, top, width, height, flip_x, flip_y, alpha, 0), box, draw_fast,
                          kind == "opaque" and alpha >= 255)
                return
        size = host.source_size(src_id)
        if size is None:
            return
        bw, bh = size
        if len(a) == 2:
            sx, sy, sw, sh = 0, 0, bw, bh
            dx, dy, dw, dh = a[0], a[1], bw, bh
        elif len(a) == 4:
            sx, sy, sw, sh = 0, 0, bw, bh
            dx, dy, dw, dh = a
        elif len(a) == 8:
            sx, sy, sw, sh, dx, dy, dw, dh = a
        else:
            return
        sx, sy, sw, sh = int(round(sx)), int(round(sy)), int(round(sw)), int(round(sh))
        if sw <= 0 or sh <= 0 or dw == 0 or dh == 0:
            return
        fx, fy = dw / sw, dh / sh
        if sx < 0:
            dx -= sx * fx; dw += sx * fx; sw += sx; sx = 0
        if sy < 0:
            dy -= sy * fy; dh += sy * fy; sh += sy; sy = 0
        if sx + sw > bw:
            dw -= (sx + sw - bw) * fx; sw = bw - sx
        if sy + sh > bh:
            dh -= (sy + sh - bh) * fy; sh = bh - sy
        if sw <= 0 or sh <= 0 or dw == 0 or dh == 0:
            return
        if src_id == self.id or (host.is_retained(src_id) and not host.canvases[src_id].persistent):
            host.canvases[src_id].go_persistent()      # reading a retained canvas back: stop deferring its drawing
            if src_id == self.id:
                self.go_persistent()
        version = host.source_version(src_id)
        alpha = int(self.s["alpha"] * 255)
        flags = self._flags()
        m = self.s["m"]
        if self._axis_aligned():
            x0, y0 = self._pt(dx, dy)
            x1, y1 = self._pt(dx + dw, dy + dh)
            flip_x, flip_y = x1 < x0, y1 < y0
            left, top = round(min(x0, x1)), round(min(y0, y1))
            width, height = max(1, round(abs(x1 - x0))), max(1, round(abs(y1 - y0)))
            box = pygame.Rect(left, top, width, height)
            kind = host.source_kind(src_id)
            covers = (kind == "opaque" and alpha >= 255 and not flags and self.s["comp"] == "source-over")

            def draw(surface, src_id=src_id, sx=sx, sy=sy, sw=sw, sh=sh, left=left, top=top, width=width, height=height,
                     flip_x=flip_x, flip_y=flip_y, alpha=alpha, flags=flags):
                image = host.source_surface(src_id)
                if image is None:
                    return None
                if width == sw and height == sh and not flip_x and not flip_y and alpha >= 255:
                    surface.blit(image, (left, top), pygame.Rect(sx, sy, sw, sh), special_flags=flags)
                else:
                    piece = host.variant(src_id, image, (sx, sy, sw, sh), (width, height), flip_x, flip_y, alpha)
                    surface.blit(piece, (left, top), special_flags=flags)
                return "alpha" if (alpha < 255 or host.source_kind(src_id) == "alpha") else host.source_kind(src_id)
            self.emit(("I", src_id, version, sx, sy, sw, sh, left, top, width, height, flip_x, flip_y, alpha, flags), box, draw, covers)
            return
        if m[1] == 0 and m[2] != 0 and m[0] != 0 and m[3] != 0:
            self._draw_sheared(src_id, version, (sx, sy, sw, sh), (dx, dy, dw, dh), alpha, flags)
            return
        # rotated or skewed: rotate around the destination centre
        sx_scale = math.hypot(m[0], m[1])
        sy_scale = math.hypot(m[2], m[3])
        angle = math.degrees(math.atan2(m[1], m[0]))
        target = (max(1, round(abs(dw) * sx_scale)), max(1, round(abs(dh) * sy_scale)))
        mirrored = (m[0] * m[3] - m[1] * m[2]) < 0
        cx, cy = self._pt(dx + dw / 2, dy + dh / 2)
        radius = int(math.hypot(*target) / 2) + 2
        box = pygame.Rect(round(cx) - radius, round(cy) - radius, radius * 2, radius * 2)
        qangle = round(angle * 2) / 2

        def draw_rotated(surface, src_id=src_id, sx=sx, sy=sy, sw=sw, sh=sh, target=target, mirrored=mirrored,
                         qangle=qangle, cx=round(cx), cy=round(cy), alpha=alpha, flags=flags, version=version):
            image = host.source_surface(src_id)
            if image is None:
                return
            piece = host.rotated(("rot", src_id, version, sx, sy, sw, sh, target, mirrored, qangle, alpha >> 3),
                                 lambda: _rotate(host, src_id, image, (sx, sy, sw, sh), target, mirrored, qangle, alpha))
            surface.blit(piece, piece.get_rect(center=(cx, cy)), special_flags=flags)
            return "alpha"
        self.emit(("R", src_id, version, sx, sy, sw, sh, target, mirrored, qangle, round(cx), round(cy), alpha, flags), box, draw_rotated)

    def _draw_sheared(self, src_id, version, src, dst, alpha, flags) -> None:
        """Horizontal shear (x' = a*x + c*y, y' = d*y), e.g. cast shadows: shift each row of the scaled image."""
        host = self.host
        a, _b, c, d, e, f = self.s["m"]
        dx, dy, dw, dh = dst
        w, h = max(1, round(abs(a * dw))), max(1, round(abs(d * dh)))
        if w > 2048 or h > 2048:
            return
        step = round(c * 64)                     # shear quantized to 1/64 px per row
        ys = [dy + (r + 0.5) * dh / h for r in range(h)] if d > 0 else [dy + dh - (r + 0.5) * dh / h for r in range(h)]
        xs = [c * y for y in ys]
        left_x = (a * dx if a > 0 else a * (dx + dw)) + e
        lo, hi = min(xs), max(xs)
        left = math.floor(left_x + lo)
        top = round(min(d * dy, d * (dy + dh)) + f)
        span = math.ceil(hi - lo) + 1
        box = pygame.Rect(left, top, w + span, h)

        def draw(surface, src_id=src_id, src=src, w=w, h=h, a=a, d=d, step=step, alpha=alpha, flags=flags, version=version,
                 left=left, top=top, rel=[x - lo for x in xs]):
            image = host.source_surface(src_id)
            if image is None:
                return
            def build():
                base = host.variant(src_id, image, src, (w, h), a < 0, d < 0)
                piece = pygame.Surface((w + span, h), pygame.SRCALPHA)
                for r in range(h):
                    piece.blit(base, (round(rel[r]), r), pygame.Rect(0, r, w, 1))
                return fade(piece, alpha) if alpha < 255 else piece
            piece = host.rotated(("shear", src_id, version, src, w, h, a < 0, d < 0, step, span, alpha >> 3, round(hi - lo)), build)
            surface.blit(piece, (left, top), special_flags=flags)
            return "alpha"
        self.emit(("H", src_id, version, src, w, h, left, top, step, alpha, flags), box, draw)

    # ------------------------------------------------------------ text
    def measure(self, text: str) -> float:
        size, bold, italic = self.s["font"]
        return float(self.host.font(max(6, int(round(size * 1.25))), bold, italic).size(text)[0])

    def m_fillText(self, text, x, y, max_width=None) -> None:
        self._text(str(text), x, y, self.s["fill"])

    def m_strokeText(self, text, x, y, max_width=None) -> None:
        self._text(str(text), x, y, self.s["stroke"])

    def _text(self, text: str, x, y, style) -> None:
        if not text:
            return
        gradient = style[0] == "grad"
        color = (255, 255, 255, int(255 * self.s["alpha"])) if gradient else self._color(style)
        if color[3] <= 0:
            return
        size, bold, italic = self.s["font"]
        font_key = (max(6, int(round(size * 1.25))), bold, italic)
        px, py = self._pt(x, y)
        align, base = self.s["align"], self.s["baseline"]
        host = self.host
        w, h = host.font(*font_key).size(text)
        if align == "center":
            px -= w / 2
        elif align in ("right", "end"):
            px -= w
        if base in ("middle",):
            py -= h / 2
        elif base in ("bottom", "ideographic"):
            py -= h
        elif base not in ("top", "hanging"):
            py -= h * 0.8
        left, top = round(px), round(py)
        flags = self._flags()

        def draw(surface, text=text, font_key=font_key, color=color, left=left, top=top, flags=flags, style=style):
            if gradient:
                image = host.gradient_text(text, font_key, (left, top), style, color[3])
            else:
                image = host.text_image(text, font_key, color[:3], color[3])
            surface.blit(image, (left, top), special_flags=flags)
            return "alpha"
        self.emit(("T", text, font_key, color, left, top, flags, style if gradient else None), pygame.Rect(left, top, w, h), draw)

    # ------------------------------------------------------------ pixels
    def get_image_hex(self, x, y, w, h) -> str:
        self.go_persistent()
        area = pygame.Rect(x, y, w, h)
        out = pygame.Surface((w, h), pygame.SRCALPHA)
        self.surface.set_clip(None)
        out.blit(self.surface, (0, 0), area)
        return pygame.image.tostring(out, "RGBA").hex()

    def put_image_hex(self, x, y, w, h, hex_data) -> None:
        self.go_persistent()
        raw = bytes.fromhex(hex_data)
        if len(raw) == w * h * 4:
            self.surface.set_clip(None)
            self.surface.blit(pygame.image.fromstring(raw, (w, h), "RGBA"), (x, y))
            self.version += 1


def _rotate(host, src_id, image, rect, target, mirrored, qangle, alpha):
    piece = pygame.transform.rotate(host.variant(src_id, image, rect, target, False, mirrored), -qangle)
    if alpha < 255:
        piece = fade(piece, alpha)
    return piece


def gradient_surface(size, origin, style, depth_like: pygame.Surface | None = None) -> pygame.Surface:
    """Render a linear or radial gradient covering a rectangle at `origin`."""
    _tag, kind, args, stops = style
    w, h = size
    tile = pygame.Surface(size, pygame.SRCALPHA)
    ox, oy = origin
    if kind == "linear":
        x0, y0, x1, y1 = args
        dx, dy = x1 - x0, y1 - y0
        length2 = dx * dx + dy * dy or 1.0
        if abs(dy) >= abs(dx):
            for row in range(h):
                t = ((w / 2 + ox - x0) * dx + (oy + row + 0.5 - y0) * dy) / length2
                tile.fill(_lerp_stops(stops, max(0.0, min(1.0, t))), (0, row, w, 1))
        else:
            for col in range(w):
                t = ((ox + col + 0.5 - x0) * dx + (h / 2 + oy - y0) * dy) / length2
                tile.fill(_lerp_stops(stops, max(0.0, min(1.0, t))), (col, 0, 1, h))
    else:
        x0, y0, r0, x1, y1, r1 = args
        steps = int(max(r0, r1)) + 1
        for i in range(steps, -1, -1):
            t = i / steps
            pygame.draw.circle(tile, _lerp_stops(stops, t), (int(x1 - ox), int(y1 - oy)), max(1, int(r0 + (r1 - r0) * t)))
    return tile
