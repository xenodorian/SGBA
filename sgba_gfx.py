"""Graphics memory for SGBA's JavaScript host, modelled on a console's video memory.

Lessons taken from the Game Boy Advance and mGBA: a small fixed budget, compact pixel formats, data decoded
only when first needed, and never recomputed when a cached copy will do.

* ImageStore keeps images in one 16-bit format (RGB565, colorkey for cut-out sprites), decodes them lazily and
  evicts the least recently used when the budget is exceeded.
* LRUBytes is a byte-budgeted cache used for flipped/scaled sprite variants, rendered text and gradients.
* Pixel formats are kept identical from image to canvas to window buffer, because blits between different
  formats are several times slower on a small CPU.
"""
from __future__ import annotations

import struct
from collections import OrderedDict
from pathlib import Path

import pygame

KEY_CANDIDATES = ((255, 0, 255), (0, 255, 0), (255, 255, 0), (0, 255, 255))
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
RLE_MAX_WIDTH = 1024        # run-length coding speeds up mostly transparent layers but slows wide sprite strips


class Stats:
    """Counters for how much drawing a frame costs (device independent, so desktop runs predict handhelds)."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.blits = 0
        self.copy_px = 0       # opaque blits: plain memory copy
        self.key_px = 0        # colorkey blits
        self.alpha_px = 0      # per-pixel alpha blits: the expensive kind
        self.fill_px = 0
        self.ops = 0
        self.replayed = 0      # ops replayed after dirty-region diffing
        self.dirty_px = 0
        self.frames = 0
        self.skipped = 0       # frames that needed no drawing at all


def fade(surface: pygame.Surface, alpha: int) -> pygame.Surface:
    """A copy whose opacity is baked into its pixels. Blitting a surface that has both per-pixel alpha and a separate
    surface alpha is several times slower than blitting one with the fade already applied."""
    if surface.get_colorkey() is not None and not surface.get_flags() & pygame.SRCALPHA:
        # convert_alpha() loses the colorkey of an RLE-compressed surface (its keyed pixels turn solid), so go through a blit
        faded = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        faded.blit(surface, (0, 0))
    else:
        faded = surface.convert_alpha()
        if faded is surface:
            faded = surface.copy()
    faded.fill((255, 255, 255, max(0, min(255, alpha))), special_flags=pygame.BLEND_RGBA_MULT)
    return faded


class LRUBytes:
    """Least-recently-used cache limited by total bytes."""

    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.bytes = 0
        self.items: OrderedDict = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _size(surface: pygame.Surface) -> int:
        return surface.get_width() * surface.get_height() * surface.get_bytesize()

    def get(self, key):
        item = self.items.get(key)
        if item is None:
            self.misses += 1
            return None
        self.items.move_to_end(key)
        self.hits += 1
        return item

    def put(self, key, surface: pygame.Surface) -> pygame.Surface:
        size = self._size(surface)
        if size > self.max_bytes:
            return surface                       # too big to cache; caller still uses it once
        old = self.items.pop(key, None)
        if old is not None:
            self.bytes -= self._size(old)
        self.items[key] = surface
        self.bytes += size
        while self.bytes > self.max_bytes and len(self.items) > 1:
            _key, evicted = self.items.popitem(last=False)
            self.bytes -= self._size(evicted)
        return surface

    def clear(self) -> None:
        self.items.clear()
        self.bytes = 0


def png_size(path: Path):
    """Width and height from a PNG header without decoding the image, or None."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(24)
    except OSError:
        return None
    if head[:8] != PNG_MAGIC or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def to_compact(source: pygame.Surface, alpha_mode: str = "threshold", depth: int = 16, rle: bool = False):
    """Convert a decoded image to the compact 16-bit format. Returns (surface, kind).

    kind: "opaque" (RGB565), "key" (RGB565 + colorkey, cut-out sprites), "alpha" (32-bit, real translucency).
    Translucent edges that make up under 5% of the visible pixels are cut to on/off transparency
    (alpha_mode "threshold"), which keeps pixel-art sprites compact; "keep" preserves every alpha value.
    """
    w, h = source.get_size()
    total = w * h
    has_alpha = bool(source.get_flags() & pygame.SRCALPHA) or source.get_colorkey() is not None
    if not has_alpha:
        out = pygame.Surface((w, h), 0, depth)
        out.blit(source, (0, 0))
        return out, "opaque"
    visible = pygame.mask.from_surface(source, 0).count()
    solid = pygame.mask.from_surface(source, 254).count()
    if solid == total:
        out = pygame.Surface((w, h), 0, depth)
        out.blit(source, (0, 0))
        return out, "opaque"
    partial = visible - solid
    if partial and (alpha_mode == "keep" or partial * 20 > max(1, visible)):
        return source.convert_alpha(), "alpha"
    if partial:
        # keep pixels that are at least half opaque (made fully opaque), drop the rest
        mask = pygame.mask.from_surface(source, 127)
        cut = pygame.Surface((w, h), pygame.SRCALPHA)
        cut.blit(source, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)
        cut.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MAX)
        keep = mask.to_surface(setcolor=(255, 255, 255, 255), unsetcolor=(255, 255, 255, 0))
        cut.blit(keep, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
        source = cut
        visible = mask.count()
    transparent = total - visible
    for key in KEY_CANDIDATES:
        out = pygame.Surface((w, h), 0, depth)
        out.fill(key)
        stored = tuple(out.get_at((0, 0)))[:3]       # the key as this pixel format can actually represent it
        out.blit(source, (0, 0))
        clash = pygame.mask.from_threshold(out, stored, (1, 1, 1, 255)).count() - transparent
        if clash <= 0:
            if rle and w <= RLE_MAX_WIDTH:
                out.set_colorkey(stored, pygame.RLEACCEL)
            else:
                out.set_colorkey(stored)
            return out, "key"
    return source.convert_alpha(), "alpha"


class ImageStore:
    """Lazily decoded, budgeted images addressed by integer id."""

    def __init__(self, base_id: int, budget_bytes: int = 96 * 1024 * 1024, alpha_mode: str = "threshold",
                 compact: bool = True, log=None, depth: int = 16, rle: bool = False) -> None:
        self.depth = depth
        self.rle = rle
        self.next_id = base_id
        self.budget = budget_bytes
        self.alpha_mode = alpha_mode
        self.compact = compact
        self.log = log or (lambda *a: None)
        self.entries: dict = {}            # id -> dict(path, w, h, surface, kind, bytes, pinned)
        self.by_path: dict = {}
        self.lru: OrderedDict = OrderedDict()   # ids of decoded, evictable images, oldest first
        self.bytes = 0
        self.peak = 0
        self.loads = 0
        self.evictions = 0
        self.kinds: dict = {}

    # -- registration
    def register_file(self, path: Path):
        """Add an image file without decoding it. Returns (id, w, h) or None if it cannot be read."""
        key = str(path)
        known = self.by_path.get(key)
        if known is not None:
            e = self.entries[known]
            return known, e["w"], e["h"]
        size = png_size(path)
        surface = None
        if size is None:                   # not a PNG (or unreadable header): decode now to learn the size
            try:
                surface = pygame.image.load(key)
            except (pygame.error, OSError):
                return None
            size = surface.get_size()
        sid = self.next_id
        self.next_id += 1
        self.entries[sid] = {"path": key, "w": size[0], "h": size[1], "surface": None, "kind": None, "bytes": 0, "pinned": False}
        self.by_path[key] = sid
        if surface is not None:
            self._install(sid, surface)
        return sid, size[0], size[1]

    def register_surface(self, surface: pygame.Surface):
        """Add an already decoded image (data: URL). Kept permanently; it is small by assumption."""
        sid = self.next_id
        self.next_id += 1
        self.entries[sid] = {"path": None, "w": surface.get_width(), "h": surface.get_height(), "surface": None,
                             "kind": None, "bytes": 0, "pinned": True}
        self._install(sid, surface)
        return sid, surface.get_width(), surface.get_height()

    # -- access
    def has(self, sid: int) -> bool:
        return sid in self.entries

    def size(self, sid: int):
        e = self.entries.get(sid)
        return (e["w"], e["h"]) if e else None

    def get(self, sid: int):
        e = self.entries.get(sid)
        if e is None:
            return None
        surface = e["surface"]
        if surface is not None:
            if not e["pinned"]:
                self.lru.move_to_end(sid)
            return surface
        try:
            decoded = pygame.image.load(e["path"])
        except (pygame.error, OSError):
            self.log("warn", f"image failed to decode: {e['path']}")
            return None
        self.loads += 1
        self._install(sid, decoded)
        return self.entries[sid]["surface"]

    def _install(self, sid: int, decoded: pygame.Surface) -> None:
        e = self.entries[sid]
        if self.compact:
            surface, kind = to_compact(decoded, self.alpha_mode, self.depth, self.rle)
        else:
            surface = decoded.convert_alpha() if pygame.display.get_surface() is not None else decoded
            kind = "raw"
        e["surface"], e["kind"] = surface, kind
        e["bytes"] = surface.get_width() * surface.get_height() * surface.get_bytesize()
        self.bytes += e["bytes"]
        self.peak = max(self.peak, self.bytes)
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        if not e["pinned"]:
            self.lru[sid] = True
            self.lru.move_to_end(sid)
            self._evict(keep=sid)

    def _evict(self, keep: int) -> None:
        while self.bytes > self.budget and len(self.lru) > 1:
            victim = next(iter(self.lru))
            if victim == keep:
                self.lru.move_to_end(victim)
                continue
            self.lru.pop(victim)
            e = self.entries[victim]
            self.bytes -= e["bytes"]
            e["surface"], e["bytes"] = None, 0
            self.evictions += 1

    def kind(self, sid: int):
        e = self.entries.get(sid)
        return e["kind"] if e else None

    @property
    def decoded_count(self) -> int:
        return sum(1 for e in self.entries.values() if e["surface"] is not None)
