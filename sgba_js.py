"""Run browser-style JavaScript canvas games inside SGBA.

A game is a folder with an index.html (plus scripts and assets) written for the browser. SGBA runs its
scripts in QuickJS, provides the browser features a 2D canvas game needs (see sgba_js_shim.js), and draws
the game's main canvas through the normal SGBA runtime, so menus, input and packaging work as for Python games.
"""
from __future__ import annotations

import base64
import binascii
import html.parser
import io
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

import pygame

import sgba_js_ui

try:
    import quickjs
except ImportError:  # the bundle always includes it; plain dev installs may not
    quickjs = None

SHIM_PATH = Path(__file__).resolve().parent / "sgba_js_shim.js"
IMAGE_ID_BASE = 1_000_000
STANDARD_BUTTONS = ("a", "b", "x", "y", "l1", "r1", "l2", "r2", "select", "start", "l3", "r3", "up", "down", "left", "right")


# ---------------------------------------------------------------- colors

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
            vals = []
            for p in parts[:3]:
                vals.append(round(float(p[:-1]) * 2.55) if p.endswith("%") else round(float(p)))
            a = 255
            if len(parts) > 3:
                a = round(float(parts[3][:-1]) * 2.55) if parts[3].endswith("%") else round(float(parts[3]) * 255)
            result = (max(0, min(255, vals[0])), max(0, min(255, vals[1])), max(0, min(255, vals[2])), max(0, min(255, a)))
        elif s.startswith("hsl"):
            parts = [p for p in re.split(r"[,\s/()]+", s[s.index("(") + 1:].rstrip(") ")) if p]
            hue = float(parts[0].rstrip("deg")) % 360
            sat, lig = float(parts[1].rstrip("%")) / 100, float(parts[2].rstrip("%")) / 100
            c = pygame.Color(0, 0, 0)
            c.hsla = (hue, sat * 100, lig * 100, 100)
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


# ---------------------------------------------------------------- canvas 2D

class Canvas2D:
    """Canvas 2D drawing state and operations on one pygame surface."""

    def __init__(self, host: "JSHost", surface: pygame.Surface) -> None:
        self.host = host
        self.surface = surface
        self.s = self._fresh_state()
        self.stack: list = []
        self.path: list = []

    @staticmethod
    def _fresh_state() -> dict:
        return {"fill": (0, 0, 0, 255), "stroke": (0, 0, 0, 255), "alpha": 1.0, "lw": 1.0, "font": (10, False, False),
                "align": "start", "baseline": "alphabetic", "comp": "source-over", "m": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
                "clip": None}

    # -- properties
    def set_prop(self, name: str, value) -> None:
        s = self.s
        if name in ("fillStyle", "strokeStyle"):
            style = self._parse_style(str(value))
            s["fill" if name == "fillStyle" else "stroke"] = style
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
                stops = sorted(((float(o), parse_color(c)) for o, c in data["s"]), key=lambda x: x[0])
                return ("grad", data["g"], [float(v) for v in data["a"]], stops)
            except (ValueError, KeyError, TypeError):
                return (0, 0, 0, 255)
        return parse_color(text)

    @staticmethod
    def _parse_font(text: str):
        m = re.search(r"(\d+(?:\.\d+)?)\s*px", text)
        size = float(m.group(1)) if m else 10.0
        return (size, "bold" in text or "700" in text or "800" in text, "italic" in text)

    # -- state stack and transform
    def m_save(self) -> None:
        self.stack.append(dict(self.s))

    def m_restore(self) -> None:
        if self.stack:
            self.s = self.stack.pop()
            self._apply_clip()

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

    # -- helpers
    def _apply_clip(self) -> None:
        self.surface.set_clip(self.s["clip"])

    def _flags(self) -> int:
        comp = self.s["comp"]
        if comp == "lighter":
            return pygame.BLEND_RGB_ADD
        if comp == "multiply":
            return pygame.BLEND_RGB_MULT
        return 0

    def _effective(self, color):
        a = color[3] * self.s["alpha"]
        return (color[0], color[1], color[2], int(a))

    def _paint_rect(self, rect: pygame.Rect, style) -> None:
        if rect.w <= 0 or rect.h <= 0:
            return
        if style[0] == "grad":
            tile = self._gradient_surface(rect, style)
            self._blit_alpha(tile, rect.topleft)
            return
        r, g, b, a = self._effective(style)
        if a <= 0:
            return
        flags = self._flags()
        if a >= 255 and not flags:
            self.surface.fill((r, g, b), rect)
            return
        tile = pygame.Surface(rect.size, pygame.SRCALPHA)
        tile.fill((r, g, b, a))
        self.surface.blit(tile, rect.topleft, special_flags=flags)

    def _blit_alpha(self, tile: pygame.Surface, pos) -> None:
        alpha = self.s["alpha"]
        if alpha < 1.0:
            tile = tile.copy()
            tile.set_alpha(int(alpha * 255))
        self.surface.blit(tile, pos, special_flags=self._flags())

    def _gradient_surface(self, rect: pygame.Rect, style) -> pygame.Surface:
        _tag, kind, args, stops = style
        tile = pygame.Surface(rect.size, pygame.SRCALPHA)
        ox, oy = rect.topleft
        if kind == "linear":
            x0, y0, x1, y1 = args
            dx, dy = x1 - x0, y1 - y0
            length2 = dx * dx + dy * dy or 1.0
            if abs(dy) >= abs(dx):                      # draw horizontal lines
                for row in range(rect.h):
                    py = oy + row + 0.5
                    t = ((rect.w / 2 + ox - x0) * dx + (py - y0) * dy) / length2
                    tile.fill(_lerp_stops(stops, max(0.0, min(1.0, t))), (0, row, rect.w, 1))
            else:                                       # draw vertical lines
                for col in range(rect.w):
                    px = ox + col + 0.5
                    t = ((px - x0) * dx + (rect.h / 2 + oy - y0) * dy) / length2
                    tile.fill(_lerp_stops(stops, max(0.0, min(1.0, t))), (col, 0, 1, rect.h))
        else:
            x0, y0, r0, x1, y1, r1 = args
            steps = int(max(r0, r1)) + 1
            for i in range(steps, -1, -1):
                t = i / steps
                radius = r0 + (r1 - r0) * t
                pygame.draw.circle(tile, _lerp_stops(stops, t), (int(x1 - ox), int(y1 - oy)), max(1, int(radius)))
        return tile

    # -- rectangles
    def _device_rect(self, x, y, w, h):
        x0, y0 = self._pt(x, y)
        x1, y1 = self._pt(x + w, y + h)
        left, right = sorted((round(x0), round(x1)))
        top, bottom = sorted((round(y0), round(y1)))
        return pygame.Rect(left, top, right - left, bottom - top)

    def m_fillRect(self, x, y, w, h) -> None:
        if self._axis_aligned():
            self._paint_rect(self._device_rect(x, y, w, h), self.s["fill"])
        else:
            self._fill_polygon([self._pt(x, y), self._pt(x + w, y), self._pt(x + w, y + h), self._pt(x, y + h)], self.s["fill"])

    def m_strokeRect(self, x, y, w, h) -> None:
        pts = [self._pt(x, y), self._pt(x + w, y), self._pt(x + w, y + h), self._pt(x, y + h)]
        self._stroke_points(pts, True, self.s["stroke"])

    def m_clearRect(self, x, y, w, h) -> None:
        rect = self._device_rect(x, y, w, h)
        clipped = rect.clip(self.surface.get_clip())
        if clipped.w > 0 and clipped.h > 0:
            self.surface.fill((0, 0, 0, 0), clipped)

    # -- paths
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
            closed = len(sub) > 2 and sub[0] == sub[-1]
            self._stroke_points(sub, closed, self.s["stroke"])

    def m_clip(self, *_rule) -> None:
        xs = [p[0] for sub in self.path for p in sub]
        ys = [p[1] for sub in self.path for p in sub]
        if xs:
            rect = pygame.Rect(math.floor(min(xs)), math.floor(min(ys)), math.ceil(max(xs) - min(xs)), math.ceil(max(ys) - min(ys)))
            current = self.s["clip"]
            self.s["clip"] = rect if current is None else rect.clip(current)
            self._apply_clip()

    def m_setLineDash(self, *_a) -> None:
        pass

    def _fill_polygon(self, pts, style) -> None:
        if len(pts) < 3:
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        left, top = math.floor(min(xs)), math.floor(min(ys))
        w, h = math.ceil(max(xs)) - left + 1, math.ceil(max(ys)) - top + 1
        if w <= 0 or h <= 0 or w > 4096 or h > 4096:
            return
        local = [(p[0] - left, p[1] - top) for p in pts]
        tile = pygame.Surface((w, h), pygame.SRCALPHA)
        if style[0] == "grad":
            grad = self._gradient_surface(pygame.Rect(left, top, w, h), style)
            mask = pygame.Surface((w, h), pygame.SRCALPHA)
            pygame.draw.polygon(mask, (255, 255, 255, 255), local)
            grad.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
            tile = grad
            self._blit_alpha(tile, (left, top))
            return
        color = self._effective(style)
        pygame.draw.polygon(tile, color, local)
        self.surface.blit(tile, (left, top), special_flags=self._flags())

    def _stroke_points(self, pts, closed, style) -> None:
        if len(pts) < 2 or style[0] == "grad":
            return
        a, b, c, d = self.s["m"][:4]
        width = max(1, round(self.s["lw"] * math.sqrt(abs(a * d - b * c)) or 1))
        color = self._effective(style)
        if color[3] >= 255 and not self._flags():
            pygame.draw.lines(self.surface, color[:3], closed, [(round(x), round(y)) for x, y in pts], width)
            return
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        left, top = math.floor(min(xs)) - width, math.floor(min(ys)) - width
        w, h = math.ceil(max(xs)) - left + width + 1, math.ceil(max(ys)) - top + width + 1
        if w <= 0 or h <= 0 or w > 4096 or h > 4096:
            return
        tile = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.lines(tile, color, closed, [(x - left, y - top) for x, y in pts], width)
        self.surface.blit(tile, (left, top), special_flags=self._flags())

    # -- images
    def m_drawImage(self, src_id, *a) -> None:
        source = self.host.surface_for(int(src_id))
        if source is None:
            return
        if len(a) == 2:
            sx, sy, sw, sh = 0, 0, source.get_width(), source.get_height()
            dx, dy, dw, dh = a[0], a[1], sw, sh
        elif len(a) == 4:
            sx, sy, sw, sh = 0, 0, source.get_width(), source.get_height()
            dx, dy, dw, dh = a
        elif len(a) == 8:
            sx, sy, sw, sh, dx, dy, dw, dh = a
        else:
            return
        sx, sy, sw, sh = int(round(sx)), int(round(sy)), int(round(sw)), int(round(sh))
        # clamp the source rectangle to the image, shrinking the destination to match
        bw, bh = source.get_size()
        if sw <= 0 or sh <= 0:
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
        m = self.s["m"]
        if self._axis_aligned():
            x0, y0 = self._pt(dx, dy)
            x1, y1 = self._pt(dx + dw, dy + dh)
            flip_x, flip_y = x1 < x0, y1 < y0
            left, top = round(min(x0, x1)), round(min(y0, y1))
            width, height = max(1, round(abs(x1 - x0))), max(1, round(abs(y1 - y0)))
            if width == sw and height == sh and not flip_x and not flip_y and self.s["alpha"] >= 1.0:
                self.surface.blit(source, (left, top), pygame.Rect(sx, sy, sw, sh), special_flags=self._flags())
                return
            piece = source.subsurface(pygame.Rect(sx, sy, sw, sh))
            if (width, height) != (sw, sh):
                piece = pygame.transform.scale(piece, (width, height))
            if flip_x or flip_y:
                piece = pygame.transform.flip(piece, flip_x, flip_y)
            self._blit_alpha(piece, (left, top))
            return
        # rotated or skewed: rotate around the destination centre
        piece = source.subsurface(pygame.Rect(sx, sy, sw, sh))
        sx_scale = math.hypot(m[0], m[1])
        sy_scale = math.hypot(m[2], m[3])
        angle = math.degrees(math.atan2(m[1], m[0]))
        target = (max(1, round(abs(dw) * sx_scale)), max(1, round(abs(dh) * sy_scale)))
        if target != (sw, sh):
            piece = pygame.transform.scale(piece, target)
        if (m[0] * m[3] - m[1] * m[2]) < 0:
            piece = pygame.transform.flip(piece, False, True)
        piece = pygame.transform.rotate(piece, -angle)
        cx, cy = self._pt(dx + dw / 2, dy + dh / 2)
        rect = piece.get_rect(center=(round(cx), round(cy)))
        self._blit_alpha(piece, rect.topleft)

    # -- text
    def _font(self):
        size, bold, italic = self.s["font"]
        return self.host.font(max(6, int(round(size * 1.25))), bold, italic)

    def measure(self, text: str) -> float:
        return float(self._font().size(text)[0])

    def m_fillText(self, text, x, y, max_width=None) -> None:
        self._text(str(text), x, y, self.s["fill"])

    def m_strokeText(self, text, x, y, max_width=None) -> None:
        self._text(str(text), x, y, self.s["stroke"])

    def _text(self, text: str, x, y, style) -> None:
        if not text or style[0] == "grad":
            return
        color = self._effective(style)
        if color[3] <= 0:
            return
        image = self._font().render(text, True, color[:3])
        if color[3] < 255:
            image.set_alpha(color[3])
        w, h = image.get_size()
        px, py = self._pt(x, y)
        align = self.s["align"]
        if align in ("center",):
            px -= w / 2
        elif align in ("right", "end"):
            px -= w
        base = self.s["baseline"]
        if base == "top" or base == "hanging":
            pass
        elif base == "middle":
            py -= h / 2
        elif base == "bottom" or base == "ideographic":
            py -= h
        else:
            py -= h * 0.8
        self.surface.blit(image, (round(px), round(py)), special_flags=self._flags())

    # -- pixels
    def get_image_hex(self, x, y, w, h) -> str:
        area = pygame.Rect(x, y, w, h)
        out = pygame.Surface((w, h), pygame.SRCALPHA)
        out.blit(self.surface, (0, 0), area)
        return pygame.image.tostring(out, "RGBA").hex()

    def put_image_hex(self, x, y, w, h, hex_data) -> None:
        raw = bytes.fromhex(hex_data)
        if len(raw) == w * h * 4:
            self.surface.blit(pygame.image.fromstring(raw, (w, h), "RGBA"), (x, y))


# ---------------------------------------------------------------- host

class _PageParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.items: list = []        # ("script", src or None, inline text) and ("element", tag, id)
        self._script_attrs = None
        self._script_text: list = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            self._script_attrs = a
            self._script_text = []
        elif a.get("id"):
            self.items.append(("element", tag, a["id"], a.get("width"), a.get("height")))

    def handle_data(self, data):
        if self._script_attrs is not None:
            self._script_text.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script_attrs is not None:
            a = self._script_attrs
            self.items.append(("script", a.get("src"), "".join(self._script_text), a.get("type", "")))
            self._script_attrs = None

    def unknown_decl(self, data):
        pass


class JSHost:
    """One JavaScript page: QuickJS context, canvases, images, storage."""

    def __init__(self, root: Path, save_path: Path | None = None, log=None) -> None:
        if quickjs is None:
            raise RuntimeError("The quickjs-ng package is required to run JavaScript games")
        self.root = Path(root).resolve()
        self.log_fn = log or (lambda level, message: print(f"[js {level}] {message}", file=sys.stderr))
        self.save_path = save_path
        self.surfaces: dict = {}
        self.contexts: dict = {}
        self.images: dict = {}
        self.image_by_path: dict = {}
        self.next_image = IMAGE_ID_BASE
        self.fonts: dict = {}
        self.sounds: dict = {}
        self.storage = self._load_storage()
        self.clock_ms = 0.0
        self.main_id = None
        self.errors = 0
        self.pad_json = ""
        self.ctx = quickjs.Context()
        self.ctx.set_memory_limit(192 * 1024 * 1024)
        self.ctx.set_max_stack_size(2 * 1024 * 1024)
        self._register()
        self.run_script(SHIM_PATH.read_text(encoding="utf-8"), "sgba_js_shim.js")
        self._tick = self.ctx.get("__sgba_tick")
        self._key = self.ctx.get("__sgba_key")
        self._set_pads = self.ctx.get("__sgba_set_pads")

    # -- python callables exposed to JS
    def _register(self) -> None:
        add = self.ctx.add_callable
        add("__py_log", lambda level, msg: self._js_log(level, msg))
        add("__py_new_canvas", self._new_canvas)
        add("__py_resize", self._resize_canvas)
        add("__py_set", lambda cid, name, value: self._canvas_ctx(cid).set_prop(name, value))
        add("__py_call", self._call)
        add("__py_measure", lambda cid, text: self._canvas_ctx(cid).measure(text))
        add("__py_getimage", lambda cid, x, y, w, h: self._canvas_ctx(cid).get_image_hex(x, y, w, h))
        add("__py_putimage", lambda cid, x, y, w, h, s: self._canvas_ctx(cid).put_image_hex(x, y, w, h, s))
        add("__py_load_image", self._load_image)
        add("__py_storage", self._storage_op)
        add("__py_read_text", self._read_text)
        add("__py_audio", self._audio)

    def _js_log(self, level, message) -> None:
        if level == "error":
            self.errors += 1
        self.log_fn(level, str(message))

    def _new_canvas(self, cid, w, h) -> None:
        self.surfaces[int(cid)] = self._make_surface(int(w), int(h))

    def _make_surface(self, w: int, h: int) -> pygame.Surface:
        w, h = max(1, min(w, 4096)), max(1, min(h, 4096))
        surface = pygame.Surface((w, h), pygame.SRCALPHA)
        return surface

    def _resize_canvas(self, cid, w, h) -> None:
        cid = int(cid)
        old = self.surfaces.get(cid)
        new = self._make_surface(int(w), int(h))
        self.surfaces[cid] = new
        if cid in self.contexts:
            self.contexts[cid].surface = new
            self.contexts[cid].s = Canvas2D._fresh_state()
            self.contexts[cid].stack = []

    def _canvas_ctx(self, cid) -> Canvas2D:
        cid = int(cid)
        ctx = self.contexts.get(cid)
        if ctx is None:
            ctx = self.contexts[cid] = Canvas2D(self, self.surfaces[cid])
        return ctx

    def _call(self, cid, name, *args):
        try:
            getattr(self._canvas_ctx(cid), "m_" + str(name))(*args)
        except (pygame.error, ValueError, TypeError, IndexError, ZeroDivisionError, KeyError) as exc:
            self.errors += 1
            if self.errors < 30:
                self.log_fn("error", f"canvas {name}{args[:6]} failed: {exc}")

    def surface_for(self, sid: int):
        if sid >= IMAGE_ID_BASE:
            return self.images.get(sid)
        return self.surfaces.get(sid)

    def font(self, size: int, bold: bool, italic: bool) -> pygame.font.Font:
        key = (size, bold, italic)
        font = self.fonts.get(key)
        if font is None:
            if not pygame.font.get_init():
                pygame.font.init()
            font = pygame.font.Font(None, size)
            font.set_bold(bold)
            font.set_italic(italic)
            self.fonts[key] = font
        return font

    # -- files
    def _resolve(self, url: str):
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            return None
        path = unquote(parsed.path)
        candidate = (self.root / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _read_text(self, url):
        path = self._resolve(str(url))
        if path is None:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def _load_image(self, src):
        src = str(src)
        try:
            if src.startswith("data:"):
                head, _, payload = src.partition(",")
                data = base64.b64decode(payload) if ";base64" in head else unquote(payload).encode("latin-1")
                surface = pygame.image.load(io.BytesIO(data))
                key = None
            else:
                path = self._resolve(src)
                if path is None:
                    return "-1,0,0"
                key = str(path)
                if key in self.image_by_path:
                    sid = self.image_by_path[key]
                    w, h = self.images[sid].get_size()
                    return f"{sid},{w},{h}"
                surface = pygame.image.load(str(path))
            if pygame.display.get_surface() is not None:
                surface = surface.convert_alpha()
            sid = self.next_image
            self.next_image += 1
            self.images[sid] = surface
            if key:
                self.image_by_path[key] = sid
            return f"{sid},{surface.get_width()},{surface.get_height()}"
        except (pygame.error, OSError, ValueError, binascii.Error):
            self.log_fn("warn", f"image failed: {src[:80]}")
            return "-1,0,0"

    # -- storage
    def _load_storage(self) -> dict:
        if self.save_path and self.save_path.is_file():
            try:
                data = json.loads(self.save_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
            except (OSError, ValueError):
                pass
        return {}

    def flush_storage(self) -> None:
        if not self.save_path:
            return
        try:
            self.save_path.parent.mkdir(parents=True, exist_ok=True)
            encoded = json.dumps(self.storage, separators=(",", ":"))
            if len(encoded) <= 1024 * 1024:
                temp = self.save_path.with_suffix(".tmp")
                temp.write_text(encoded, encoding="utf-8")
                temp.replace(self.save_path)
        except OSError:
            pass

    def _storage_op(self, op, key, value):
        op, key = str(op), str(key)
        if op == "get":
            return self.storage.get(key)
        if op == "set":
            self.storage[key] = str(value)
            self._dirty = True
        elif op == "remove":
            self.storage.pop(key, None)
            self._dirty = True
        elif op == "clear":
            self.storage.clear()
            self._dirty = True
        elif op == "key":
            keys = list(self.storage)
            index = int(key) if key.isdigit() else -1
            return keys[index] if 0 <= index < len(keys) else None
        elif op == "length":
            return str(len(self.storage))
        return None

    _dirty = False

    # -- audio
    def _audio(self, action, src, volume, loop) -> None:
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init()
            path = self._resolve(str(src))
            if path is None:
                return
            if action == "play":
                sound = self.sounds.get(path)
                if sound is None:
                    sound = self.sounds[path] = pygame.mixer.Sound(str(path))
                sound.set_volume(max(0.0, min(1.0, float(volume))))
                sound.play(-1 if loop else 0)
            elif action == "stop":
                sound = self.sounds.get(path)
                if sound is not None:
                    sound.stop()
        except (pygame.error, OSError):
            pass

    # -- scripts
    def run_script(self, code: str, name: str = "script") -> bool:
        try:
            self.ctx.eval(code)
            self.pump()
            return True
        except quickjs.JSException as exc:
            self.errors += 1
            self.log_fn("error", f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - never let a script bring down the runtime
            self.errors += 1
            self.log_fn("error", f"{name}: {type(exc).__name__}: {exc}")
        return False

    def pump(self) -> None:
        for _ in range(10000):
            try:
                if not self.ctx.execute_pending_job():
                    break
            except quickjs.JSException as exc:
                self.errors += 1
                self.log_fn("error", f"promise job: {exc}")

    def load_page(self, index: str = "index.html") -> None:
        text = (self.root / index).read_text(encoding="utf-8")
        parser = _PageParser()
        parser.feed(text)
        self.ctx.get("__sgba_load_html")(text)
        self.pump()
        main = self.ctx.eval("(() => { const c = document.querySelector('canvas'); return c ? c.__id : -1; })()")
        self.main_id = int(main) if int(main) >= 0 else None
        for item in parser.items:
            if item[0] != "script":
                continue
            _kind, src, inline, stype = item
            if stype == "module":
                self.log_fn("warn", "module scripts are not supported yet")
                continue
            if src:
                if urlparse(src).scheme in ("http", "https"):
                    self.log_fn("warn", f"skipped remote script {src}")
                    continue
                path = self._resolve(src)
                if path is None:
                    self.log_fn("warn", f"missing script {src}")
                    continue
                self.run_script(path.read_text(encoding="utf-8"), src)
            elif inline.strip():
                self.run_script(inline, "inline script")
        for event in ("DOMContentLoaded", "load"):
            self.run_script(f"document.dispatchEvent(new Event('{event}')); window.dispatchEvent(new Event('{event}'));", event)

    # -- per frame
    def tick(self, dt: float) -> None:
        self.clock_ms += dt * 1000.0
        try:
            self._tick(self.clock_ms)
        except quickjs.JSException as exc:
            self.errors += 1
            self.log_fn("error", f"frame: {exc}")
        self.pump()
        if self._dirty:
            self._dirty = False
            self.flush_storage()

    def key_event(self, kind: str, key: int) -> None:
        name, code, key_code = js_key(key)
        try:
            self._key(kind, name, code, key_code)
        except quickjs.JSException as exc:
            self.log_fn("error", f"key: {exc}")
        self.pump()

    def set_pads(self, pad_json: str) -> None:
        if pad_json != self.pad_json:
            self.pad_json = pad_json
            self.ctx.eval(f"__sgba_set_pads(JSON.parse({json.dumps(pad_json)}))")

    @property
    def main_surface(self):
        return self.surfaces.get(self.main_id) if self.main_id is not None else None


_KEY_NAMES = {
    pygame.K_UP: ("ArrowUp", "ArrowUp", 38), pygame.K_DOWN: ("ArrowDown", "ArrowDown", 40),
    pygame.K_LEFT: ("ArrowLeft", "ArrowLeft", 37), pygame.K_RIGHT: ("ArrowRight", "ArrowRight", 39),
    pygame.K_SPACE: (" ", "Space", 32), pygame.K_RETURN: ("Enter", "Enter", 13), pygame.K_ESCAPE: ("Escape", "Escape", 27),
    pygame.K_BACKSPACE: ("Backspace", "Backspace", 8), pygame.K_TAB: ("Tab", "Tab", 9),
    pygame.K_LSHIFT: ("Shift", "ShiftLeft", 16), pygame.K_RSHIFT: ("Shift", "ShiftRight", 16),
    pygame.K_LCTRL: ("Control", "ControlLeft", 17), pygame.K_RCTRL: ("Control", "ControlRight", 17),
    pygame.K_LALT: ("Alt", "AltLeft", 18), pygame.K_RALT: ("Alt", "AltRight", 18),
}


def js_key(key: int):
    """pygame key -> (KeyboardEvent.key, .code, .keyCode)."""
    if key in _KEY_NAMES:
        return _KEY_NAMES[key]
    name = pygame.key.name(key) or ""
    if len(name) == 1 and name.isalpha():
        return (name, "Key" + name.upper(), ord(name.upper()))
    if len(name) == 1 and name.isdigit():
        return (name, "Digit" + name, ord(name))
    if name.startswith("[") and name.endswith("]"):
        return (name[1:-1], "Numpad" + name[1:-1], 96 + int(name[1:-1])) if name[1:-1].isdigit() else (name, name, key & 255)
    return (name or "Unidentified", name or "Unidentified", key & 255)


# ---------------------------------------------------------------- SGBA game adapter

class JSGame:
    """SGBA game object that hosts a browser-style JavaScript game.

    HTML the game shows over its canvas (menus, loading bars) is drawn by the engine, and every button in it is
    reachable with the pad. Optional sgba_ui.json in the game folder tunes this:
      {"navigation": "engine" | "game" | "off",   "engine" (default): D-pad moves focus, A presses, B goes back;
                                                  "game": the game already reads the pad and moves focus itself
       "hotkeys": {"select": "btn-hud-start"}}    pad action -> id of an on-screen button to press when no menu is open
    """

    REPEAT_DELAY = 0.40
    REPEAT_RATE = 0.12

    def __init__(self, root: Path, index: str = "index.html", save_dir: Path | None = None, log=None) -> None:
        root = Path(root)
        save_path = (save_dir or root) / "localStorage.json" if save_dir is not None else None
        self.host = JSHost(root, save_path=save_path, log=log)
        self.config = self._load_config(root)
        self.host.load_page(index)
        if self.host.main_surface is None:
            raise RuntimeError("index.html has no <canvas> element")
        self.ui = sgba_js_ui.DomOverlay(self.host)
        self._held: dict = {}

    @staticmethod
    def _load_config(root: Path) -> dict:
        config = {"navigation": "engine", "hotkeys": {}}
        path = root / "sgba_ui.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    config.update({k: v for k, v in data.items() if k in config})
            except (OSError, ValueError):
                pass
        return config

    @property
    def logical_size(self):
        return self.host.main_surface.get_size()

    def handle_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            self.host.key_event("keydown", event.key)
        elif event.type == pygame.KEYUP:
            self.host.key_event("keyup", event.key)

    def _pad_json(self, inputs, neutral: bool) -> str:
        def down(action):
            return False if neutral else bool(inputs.down(action))
        buttons = [{"pressed": down(a), "touched": down(a), "value": 1 if down(a) else 0} for a in STANDARD_BUTTONS]
        axes = [0.0, 0.0,
                (-1.0 if down("rs_left") else 1.0 if down("rs_right") else 0.0),
                (-1.0 if down("rs_up") else 1.0 if down("rs_down") else 0.0)]
        pad = {"id": "SGBA standard gamepad", "index": 0, "connected": True, "mapping": "standard", "timestamp": 0,
               "buttons": buttons, "axes": axes}
        return json.dumps([pad])

    def _repeat(self, inputs, action: str, dt: float) -> bool:
        """True on the press and then at a steady rate while the action stays held."""
        if inputs.pressed(action):
            self._held[action] = -self.REPEAT_DELAY
            return True
        if inputs.down(action):
            t = self._held.get(action)
            if t is None:
                return False
            t += dt
            if t >= self.REPEAT_RATE - 1e-9:
                self._held[action] = t - self.REPEAT_RATE
                return True
            self._held[action] = t
            return False
        self._held.pop(action, None)
        return False

    def update(self, dt: float, inputs) -> None:
        mode = self.config["navigation"]
        engine = mode == "engine"
        self.host.set_pads(self._pad_json(inputs, neutral=engine and self.ui.modal))
        self.host.tick(dt)
        self.ui.refresh()
        if not engine:
            return
        if self.ui.modal:
            self.ui.ensure_focus()
            if self._repeat(inputs, "up", dt):
                self.ui.move(-1)
            elif self._repeat(inputs, "down", dt):
                self.ui.move(1)
            elif inputs.pressed("a"):
                self.ui.activate()
            elif inputs.pressed("b"):
                self.ui.back()
            self.ui.refresh()
        else:
            for action, element_id in self.config["hotkeys"].items():
                if inputs.pressed(action):
                    self.ui.hotkey(element_id)

    def draw(self, surface: pygame.Surface) -> None:
        main = self.host.main_surface
        if main.get_size() == surface.get_size():
            surface.blit(main, (0, 0))
        else:
            surface.blit(pygame.transform.scale(main, surface.get_size()), (0, 0))
        self.ui.draw(surface)


def create_game_for(entry_file: str, index: str = "index.html", save_dir: str | None = None) -> JSGame:
    """Helper for a package's main.py: `def create_game(): return create_game_for(__file__)`."""
    return JSGame(Path(entry_file).resolve().parent, index, Path(save_dir) if save_dir else None)
