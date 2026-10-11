"""Draws the HTML a browser game shows over its canvas (menus, loading bars) and makes it pad-navigable.

The game's page is a small DOM tree (sgba_js_shim.js). Each time it changes, the host asks for a snapshot of the
elements laid over the canvas. This module lays that snapshot out as one centred panel, with every button as a
focusable row, draws it, and moves focus with the D-pad. No pointer is involved: A presses the focused
button, B goes back.
"""
from __future__ import annotations

import json
import re

import pygame

PANEL_MARGIN = 28
PANEL_PAD = 14
BG = (31, 31, 40)
LINE = (51, 51, 63)
TEXT = (230, 230, 236)
DIM = (154, 154, 168)
GOLD = (240, 180, 76)
DARK = (26, 18, 6)
KEY = (43, 43, 54)

INLINE = {"span", "b", "strong", "i", "em", "small", "a", "label", "code", "u", "s", "sup", "sub", "abbr", "font"}
HEADINGS = {"h1": 30, "h2": 24, "h3": 22, "h4": 20}
ROW_CLASS = re.compile(r"(^|\s)(row|shop-row|gem-row|shop-balance|bal|actions)(\s|$)")


def _has_class(node, pattern) -> bool:
    return bool(re.search(pattern, node.get("c", "")))


def _text_of(node) -> str:
    if "x" in node:
        return node["x"]
    return "".join(_text_of(k) for k in node.get("k", ()))


class Atom:
    """One inline item: a word or a box (button, icon) with a width and a height."""
    __slots__ = ("w", "h", "draw", "index", "focused")

    def __init__(self, w, h, draw, index=None, focused=False):
        self.w, self.h, self.draw = w, h, draw
        self.index, self.focused = index, focused


class Layout:
    """Lays a snapshot out into draw operations on a tall surface and records focus rectangles."""

    def __init__(self, host, width: int) -> None:
        self.host = host
        self.width = width
        self.ops: list = []          # (callable(surface, dy)) in paint order
        self.focus_rects: dict = {}  # focus index -> pygame.Rect (in content coordinates)
        self.cur_rect = None
        self.height = 0

    # -- fonts
    def font(self, size: int, bold: bool = False):
        return self.host.font(size, bold, False)

    # -- inline flow: words and boxes wrapped to the width
    def _atoms(self, nodes, style, out) -> None:
        for node in nodes:
            if "x" in node:
                text = node["x"]
                font = self.font(style["size"], style["bold"])
                for word in re.findall(r"\s*\S+|\s+", text):
                    w = font.size(word)[0]
                    color = style["color"]
                    out.append(Atom(w, font.get_height(),
                                    lambda surf, x, y, word=word, font=font, color=color: surf.blit(font.render(word, True, color), (x, y))))
                continue
            tag = node["t"]
            if tag == "br":
                out.append(Atom(0, 0, None))
                continue
            if tag in ("button",) or node.get("f") is not None and tag in ("a", "input", "select"):
                out.append(self._button_atom(node, style))
                continue
            if tag in ("canvas", "img"):
                out.append(self._canvas_atom(node))
                continue
            if tag == "input" and node.get("it") in ("checkbox", "radio"):
                out.append(self._checkbox_atom(node))
                continue
            sub = dict(style)
            if tag in ("b", "strong"):
                sub["bold"] = True
            if tag == "small":
                sub["size"] = max(14, style["size"] - 4)
                sub["color"] = DIM
            if _has_class(node, r"(^|\s)(note|dim|pct|shop-note)(\s|$)"):
                sub["color"] = DIM
            self._atoms(node.get("k", ()), sub, out)

    def _canvas_atom(self, node) -> Atom:
        surface = self.host.surface_for(node["cv"])
        w, h = node.get("w") or 24, node.get("h") or 24
        scale = min(1.0, 32.0 / max(1, h))
        w, h = max(1, int(w * scale)), max(1, int(h * scale))

        def draw(surf, x, y):
            if surface is not None:
                surf.blit(pygame.transform.scale(surface, (w, h)), (x, y))
        return Atom(w + 4, h, draw)

    def _checkbox_atom(self, node) -> Atom:
        on = bool(node.get("ck"))
        focus = node.get("on")

        def draw(surf, x, y):
            pygame.draw.rect(surf, GOLD if focus else LINE, (x, y, 18, 18), 2, border_radius=3)
            if on:
                pygame.draw.rect(surf, GOLD, (x + 4, y + 4, 10, 10), border_radius=2)
        return Atom(26, 20, draw)

    def _button_atom(self, node, style, full_width: int | None = None) -> Atom:
        label = _text_of(node).strip() or node.get("v", "") or ""
        font = self.font(21, True)
        tw, th = font.size(label)
        w = full_width or (tw + 28)
        h = th + 14
        primary = _has_class(node, r"(^|\s)primary(\s|$)")
        focus = bool(node.get("on"))
        disabled = bool(node.get("d"))
        index = node.get("f")

        def draw(surf, x, y):
            rect = pygame.Rect(x, y, w, h)
            if disabled:
                fill, fg, border = (35, 35, 44), (110, 110, 122), LINE
            elif focus:
                fill, fg, border = ((255, 214, 120) if primary else (240, 240, 250)), DARK, (255, 255, 255)
            elif primary:
                fill, fg, border = GOLD, DARK, GOLD
            else:
                fill, fg, border = KEY, TEXT, LINE
            pygame.draw.rect(surf, fill, rect, border_radius=6)
            pygame.draw.rect(surf, border, rect, 3 if focus else 1, border_radius=6)
            image = font.render(label, True, fg)
            surf.blit(image, image.get_rect(center=rect.center))
        return Atom(w, h, draw, index, focus)

    def _emit_line(self, line, x, y, width, align) -> int:
        lw = sum(a.w for a in line)
        lh = max((a.h for a in line), default=0)
        dx = x + ((width - lw) // 2 if align == "center" else 0)
        for atom in line:
            if atom.index is not None:
                rect = pygame.Rect(dx, y + (lh - atom.h) // 2, atom.w, atom.h)
                self.focus_rects[atom.index] = rect
                if atom.focused:
                    self.cur_rect = rect
            if atom.draw is not None:
                self.ops.append(lambda surf, dy, atom=atom, ax=dx, ay=y + (lh - atom.h) // 2: atom.draw(surf, ax, ay - dy))
            dx += atom.w
        return lh

    def flow(self, nodes, x, y, width, style, align="left") -> int:
        atoms: list = []
        self._atoms(nodes, style, atoms)
        lines, line, used = [], [], 0
        for atom in atoms:
            if atom.w == 0 and atom.h == 0 and atom.draw is None:   # <br>
                lines.append(line)
                line, used = [], 0
                continue
            is_space = False
            if used + atom.w > width and line and not is_space:
                lines.append(line)
                line, used = [], 0
                # drop leading blanks on the new line is skipped: words carry their own leading space
            line.append(atom)
            used += atom.w
        if line:
            lines.append(line)
        top = y
        for line in lines:
            if line and line[0].draw is not None:
                pass
            y += self._emit_line(line, x, y, width, align) + 2
        return max(0, y - top)

    # -- blocks
    def block(self, node, x, y, width, style) -> int:
        """Lay out one element, return its height."""
        if "x" in node:
            return self.flow([node], x, y, width, style) if node["x"].strip() else 0
        tag = node["t"]
        kids = node.get("k", ())
        if tag in ("button",) or (node.get("f") is not None and tag in ("a", "select")):
            return self.button_block(node, x, y, width)
        if tag in ("canvas", "img"):
            return self.flow([node], x, y, width, style)
        if tag == "table":
            return self.table(node, x, y, width, style)
        if tag == "hr":
            self.ops.append(lambda surf, dy, x=x, y=y, w=width: pygame.draw.line(surf, LINE, (x, y - dy + 3), (x + w, y - dy + 3)))
            return 8
        if _has_class(node, r"(^|\s)bar(\s|$)"):
            return self.bar(node, x, y, width)
        if tag in HEADINGS:
            sub = dict(style, size=HEADINGS[tag], bold=True, color=DIM if tag in ("h2", "h3") else TEXT)
            return self.flow(kids, x, y, width, sub, "center") + 6
        sub = dict(style)
        if tag == "p" or _has_class(node, r"(^|\s)note(\s|$)"):
            sub["color"] = DIM if _has_class(node, r"(^|\s)note(\s|$)") else style["color"]
            sub["size"] = 19 if _has_class(node, r"(^|\s)note(\s|$)") else style["size"]
        if _has_class(node, r"(^|\s)(pct)(\s|$)"):
            sub["color"] = GOLD
            sub["size"] = 18
        buttons = [k for k in kids if "t" in k and (k["t"] == "button" or k.get("f") is not None)]
        blocky = [k for k in kids if "t" in k and k["t"] not in INLINE and k["t"] not in ("canvas", "img", "input", "br")]
        if ROW_CLASS.search(node.get("c", "")) and not (len(buttons) == len(kids) and len(kids) > 1 and not _has_class(node, r"(^|\s)row(\s|$)")):
            return self.row(node, x, y, width, sub)
        if buttons and len(buttons) == len([k for k in kids if "t" in k or k.get("x", "").strip()]):
            # a plain list of buttons: one full-width focusable row each
            top = y
            for b in buttons:
                y += self.button_block(b, x, y, width) + 6
            return y - top
        if blocky or buttons:
            top = y
            run: list = []
            for k in kids:
                if "x" in k or ("t" in k and (k["t"] in INLINE or k["t"] in ("canvas", "img", "input", "br"))):
                    run.append(k)
                    continue
                if run:
                    y += self.flow(run, x, y, width, sub)
                    run = []
                y += self.block(k, x, y, width, sub) + (3 if "t" in k and k["t"] in ("div", "section", "p") else 0)
            if run:
                y += self.flow(run, x, y, width, sub)
            return y - top
        align = "center" if tag in ("p", "div") and _has_class(node, r"(^|\s)(box|hero|center|msg)(\s|$)|menu-msg") else "left"
        if node.get("id") in ("menu-msg", "loading-msg"):
            align = "center"
        return self.flow(kids, x, y, width, sub, align) + (4 if tag == "p" else 0)

    def button_block(self, node, x, y, width) -> int:
        label = _text_of(node).strip()
        atom = self._button_atom(node, {}, full_width=width)
        index = node.get("f")
        self.ops.append(lambda surf, dy, atom=atom, x=x, y=y: atom.draw(surf, x, y - dy))
        if index is not None:
            self.focus_rects[index] = pygame.Rect(x, y, width, atom.h)
        if node.get("on") or _has_class(node, r"(^|\s)cur(\s|$)"):
            self.cur_rect = pygame.Rect(x, y, width, atom.h)
        return atom.h

    def highlight(self, mark: int, rect: pygame.Rect) -> None:
        """Insert a selection background behind everything drawn since `mark`."""
        def draw(surf, dy, rect=rect):
            bg = pygame.Surface(rect.size, pygame.SRCALPHA)
            pygame.draw.rect(bg, (255, 255, 255, 26), bg.get_rect(), border_radius=6)
            pygame.draw.rect(bg, GOLD, bg.get_rect(), 2, border_radius=6)
            surf.blit(bg, (rect.x, rect.y - dy))
        self.ops.insert(mark, draw)
        self.cur_rect = rect

    def row(self, node, x, y, width, style) -> int:
        """Children side by side; the widest flexible child takes the leftover width."""
        kids = [k for k in node.get("k", ()) if "t" in k or k.get("x", "").strip()]
        if not kids:
            return 0
        naturals = [self._natural_width(k, style) for k in kids]
        total = sum(naturals) + 8 * (len(kids) - 1)
        flex = max(range(len(kids)), key=lambda i: naturals[i])
        widths = list(naturals)
        if total > width:
            widths[flex] = max(60, width - (sum(naturals) - naturals[flex]) - 8 * (len(kids) - 1))
        else:
            widths[flex] += width - total
        mark = len(self.ops)
        cx, rowh = x, 0
        for k, w in zip(kids, widths):
            rowh = max(rowh, self._place(k, cx, y, w, style))
            cx += w + 8
        if _has_class(node, r"(^|\s)cur(\s|$)"):
            self.highlight(mark, pygame.Rect(x - 3, y - 2, width + 6, rowh + 4))
        return rowh + 4

    def _place(self, k, x, y, w, style) -> int:
        if "x" in k:
            return self.flow([k], x, y, w, style)
        if k["t"] in INLINE or k["t"] in ("canvas", "img"):
            return self.flow([k], x, y, w, style)
        return self.block(k, x, y, w, style)

    def _natural_width(self, k, style) -> int:
        if "x" in k:
            return self.font(style["size"], style["bold"]).size(k["x"])[0] + 2
        if k["t"] == "button" or k.get("f") is not None:
            return self.font(21, True).size(_text_of(k).strip())[0] + 28
        if k["t"] in ("canvas", "img"):
            h = k.get("h") or 24
            return int((k.get("w") or 24) * min(1.0, 32.0 / max(1, h))) + 4
        return min(self.width, max((self._natural_width(c, style) for c in k.get("k", ())), default=0) +
                   (6 if k["t"] in INLINE else 0)) if k["t"] not in INLINE else sum(self._natural_width(c, style) for c in k.get("k", ()))

    def table(self, node, x, y, width, style) -> int:
        rows: list = []

        def collect(n):
            for c in n.get("k", ()):
                if "t" not in c:
                    continue
                if c["t"] == "tr":
                    rows.append((c, [cell for cell in c.get("k", ()) if "t" in cell and cell["t"] in ("td", "th")]))
                else:
                    collect(c)
        collect(node)
        if not rows:
            return 0
        columns = max(len(cells) for _tr, cells in rows)
        naturals = [0] * columns
        for _tr, cells in rows:
            for i, cell in enumerate(cells):
                naturals[i] = max(naturals[i], self.font(19, cell["t"] == "th").size(_text_of(cell).strip())[0] + 12)
        total = sum(naturals)
        if total > width:
            scale = width / total
            naturals = [max(40, int(n * scale)) for n in naturals]
        top = y
        for tr, cells in rows:
            mark = len(self.ops)
            cx, rowh = x, 0
            for i, cell in enumerate(cells):
                sub = dict(style, size=19, bold=cell["t"] == "th", color=DIM if cell["t"] == "th" else style["color"])
                rowh = max(rowh, self.flow(cell.get("k", ()), cx, y, naturals[i] - 8, sub))
                cx += naturals[i]
            if _has_class(tr, r"(^|\s)cur(\s|$)"):
                self.highlight(mark, pygame.Rect(x - 3, y - 2, min(width, sum(naturals)) + 6, rowh + 4))
            y += rowh + 4
        return y - top

    def bar(self, node, x, y, width) -> int:
        fill = next((k for k in node.get("k", ()) if "pw" in k), None)
        frac = max(0.0, min(1.0, (fill["pw"] if fill else 0) / 100.0))
        h = 10
        self.ops.append(lambda surf, dy, x=x, y=y, w=width, frac=frac: (
            pygame.draw.rect(surf, (43, 43, 54), (x, y - dy, w, h), border_radius=4),
            pygame.draw.rect(surf, GOLD, (x, y - dy, max(0, int(w * frac)), h), border_radius=4)))
        return h + 6


class DomOverlay:
    """Keeps the overlay in step with the page and handles focus movement."""

    def __init__(self, host, view_size=(640, 480)) -> None:
        self.host = host
        self.view = view_size
        self.version = -1
        self.focusable_count = 0
        self.active = -1
        self.surface = None          # tall content surface
        self.height = 0
        self.focus_rects: dict = {}
        self.focus_indices: list = []
        self.scroll = 0
        self.panels = 0
        self._get = host.ctx.get("__sgba_ui")
        self._focus = host.ctx.get("__sgba_ui_focus")
        self._click = host.ctx.get("__sgba_ui_click")
        self._back = host.ctx.get("__sgba_ui_back")
        self._by_id = host.ctx.get("__sgba_ui_activate_by_id")
        self._visible = host.ctx.get("__sgba_ui_visible")
        self._ver = host.ctx.get("__sgba_ver")

    @property
    def modal(self) -> bool:
        return self.focusable_count > 0

    def refresh(self) -> None:
        version = int(self._ver())
        if version == self.version:
            return
        self.version = version
        data = json.loads(self._get())
        self.active = data["active"]
        self._build(data["roots"])

    def _is_hotkey_root(self, root) -> bool:
        return root["t"] == "button" or (root.get("f") is not None and not root.get("k"))

    def _build(self, roots) -> None:
        panels = [r for r in roots if "t" in r and not self._is_hotkey_root(r)]
        # only panels that show something: text, buttons, bars or images
        panels = [r for r in panels if self._has_content(r)]
        self.panels = len(panels)
        self.focus_rects = {}
        self.focusable_count = 0
        self.focus_indices = []
        if not panels:
            self.surface = None
            return
        panel_w = self.view[0] - 2 * PANEL_MARGIN
        layout = Layout(self.host, panel_w - 2 * PANEL_PAD)
        base = {"size": 21, "bold": False, "color": TEXT}
        y = PANEL_PAD
        for root in panels:
            y += layout.block(root, PANEL_PAD, y, panel_w - 2 * PANEL_PAD, base)
        height = y + PANEL_PAD
        self.height = height
        self.focus_rects = layout.focus_rects
        self.focus_indices = self._focusables(panels)
        self.focusable_count = len(self.focus_indices)
        self.default_focus = self._default_focus(panels)
        content = pygame.Surface((panel_w, min(height, 4000)), pygame.SRCALPHA)
        for op in layout.ops:
            op(content, 0)
        self.surface = content
        self.cur_rect = layout.cur_rect
        self._scroll_to_focus()

    def _has_content(self, node) -> bool:
        if "x" in node:
            return bool(node["x"].strip())
        if node["t"] in ("canvas", "img", "button", "input"):
            return True
        if node.get("pw") is not None:
            return True
        return any(self._has_content(k) for k in node.get("k", ()))

    def _default_focus(self, nodes) -> int:
        """Index of the primary button, else the first focusable one."""
        found, first = [], []

        def walk(n):
            for node in n:
                if "t" not in node:
                    continue
                if node.get("f") is not None and not node.get("d"):
                    first.append(node["f"])
                    if _has_class(node, r"(^|\s)primary(\s|$)"):
                        found.append(node["f"])
                walk(node.get("k", ()))
        walk(nodes)
        return found[0] if found else (first[0] if first else 0)

    def _focusables(self, nodes) -> list:
        """Page-wide focus indices of the focusable elements inside the drawn panels, in order."""
        found: list = []
        for n in nodes:
            if "t" not in n:
                continue
            if n.get("f") is not None:
                found.append(n["f"])
            found.extend(self._focusables(n.get("k", ())))
        return found

    def _scroll_to_focus(self) -> None:
        view_h = self.view[1] - 2 * PANEL_MARGIN
        rect = self.focus_rects.get(self.active) or getattr(self, "cur_rect", None)
        if self.height <= view_h:
            self.scroll = 0
            return
        if rect is not None:
            if rect.top - 8 < self.scroll:
                self.scroll = max(0, rect.top - 8)
            elif rect.bottom + 8 > self.scroll + view_h:
                self.scroll = min(self.height - view_h, rect.bottom + 8 - view_h)
        self.scroll = max(0, min(self.scroll, self.height - view_h))

    def draw(self, surface: pygame.Surface) -> None:
        if self.surface is None:
            return
        veil = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        veil.fill((0, 0, 0, 110))
        surface.blit(veil, (0, 0))
        panel_w = self.surface.get_width()
        view_h = min(self.height, self.view[1] - 2 * PANEL_MARGIN)
        x = (surface.get_width() - panel_w) // 2
        y = (surface.get_height() - view_h) // 2
        panel = pygame.Surface((panel_w, view_h), pygame.SRCALPHA)
        pygame.draw.rect(panel, BG + (235,), (0, 0, panel_w, view_h), border_radius=10)
        pygame.draw.rect(panel, LINE, (0, 0, panel_w, view_h), 1, border_radius=10)
        panel.blit(self.surface, (0, 0), pygame.Rect(0, self.scroll, panel_w, view_h))
        if self.height > view_h:                           # scroll hint bar
            track = int(view_h * view_h / self.height)
            top = int(self.scroll * (view_h - track) / max(1, self.height - view_h))
            pygame.draw.rect(panel, (90, 90, 105), (panel_w - 5, top, 3, track), border_radius=2)
        surface.blit(panel, (x, y))

    # -- navigation
    default_focus = 0

    def ensure_focus(self) -> None:
        """Make sure something in an open menu has focus (the game may not have focused anything)."""
        if self.focusable_count and self.active not in self.focus_indices:
            self._focus(self.default_focus)
            self.refresh()

    def move(self, delta: int) -> bool:
        indices = self.focus_indices
        if not indices:
            return False
        position = indices.index(self.active) if self.active in indices else (-1 if delta > 0 else 0)
        self._focus(indices[(position + delta) % len(indices)])
        return True

    def activate(self) -> bool:
        if not self.focusable_count:
            return False
        self._click(self.active if self.active in self.focus_indices else self.focus_indices[0])
        return True

    def back(self) -> bool:
        return bool(self._back())

    def hotkey(self, element_id: str) -> bool:
        return bool(self._by_id(element_id))
