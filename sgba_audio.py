"""Sound for SGBA's JavaScript host, with a small fixed memory cost.

Browsers decode a whole music track into raw samples before playing it (a three minute stereo track is about
30 MB). That is far too much for a 1 GB handheld, so this engine keeps tracks compressed:

* Long sounds (music) are streamed straight from the compressed file by SDL_mixer, one at a time, decoded a few
  milliseconds at a time. They cost almost no memory.
* Short sounds (effects) are decoded to memory at a low sample rate and kept in a small LRU cache.
* The mixer runs at 22.05 kHz, which halves mixing work and sample memory versus 44.1 kHz, much as the Game Boy
  Advance's sound hardware used low rates.

Games keep using the Web Audio and `Audio` interfaces; the shim turns those into the simple commands below.
"""
from __future__ import annotations

import struct
import wave
from collections import OrderedDict
from pathlib import Path

import pygame

STREAM_MIN_BYTES = 96 * 1024            # files larger than this are streamed instead of decoded
SFX_BUDGET_BYTES = 6 * 1024 * 1024      # decoded effects kept in memory
DEFAULT_RATE = 22050


def ogg_duration(path: Path) -> float:
    """Length of an Ogg Vorbis file in seconds from its headers, without decoding it. 0.0 if unknown."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as handle:
            head = handle.read(512)
            if head[:4] != b"OggS" or b"\x01vorbis" not in head:
                return 0.0
            at = head.index(b"\x01vorbis")
            rate = struct.unpack("<I", head[at + 12:at + 16])[0]
            handle.seek(max(0, size - 65536))
            tail = handle.read()
        last = tail.rfind(b"OggS")
        if last < 0 or rate <= 0:
            return 0.0
        granule = struct.unpack("<q", tail[last + 6:last + 14])[0]
        return max(0.0, granule / rate)
    except (OSError, struct.error, ValueError):
        return 0.0


def duration_of(path: Path) -> float:
    suffix = path.suffix.lower()
    if suffix == ".ogg":
        return ogg_duration(path)
    if suffix == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                return handle.getnframes() / float(handle.getframerate() or 1)
        except (OSError, wave.Error, EOFError):
            return 0.0
    try:
        return path.stat().st_size * 8 / 128000.0      # rough: a 128 kbit/s file
    except OSError:
        return 0.0


class _Source:
    __slots__ = ("path", "stream", "loop", "gain", "channel", "sound")

    def __init__(self, path, stream, loop, gain):
        self.path, self.stream, self.loop, self.gain = path, stream, loop, gain
        self.channel = None
        self.sound = None


class AudioEngine:
    def __init__(self, root: Path, log=None, rate: int = DEFAULT_RATE) -> None:
        self.root = root
        self.log = log or (lambda *a: None)
        self.rate = rate
        self.ready = None                 # None = not tried yet, False = unavailable (silent), True = running
        self.buffers: dict = {}           # buffer id -> Path
        self.by_path: dict = {}
        self.sources: dict = {}           # source id -> _Source
        self.sfx: OrderedDict = OrderedDict()
        self.sfx_bytes = 0
        self.music_sid = None
        self.next_buffer = 1
        self.stats = {"streamed": 0, "decoded": 0}

    # -- setup
    def _init(self) -> bool:
        if self.ready is not None:
            return self.ready
        try:
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(self.rate, -16, 2, 1024)
            pygame.mixer.set_num_channels(12)
            self.ready = True
        except (pygame.error, NotImplementedError, ImportError, OSError) as exc:
            self.log("warn", f"audio unavailable, running silent: {exc}")
            self.ready = False
        return self.ready

    # -- called from JavaScript
    def register(self, path) -> str:
        """Register a sound file (nothing is decoded). Returns "buffer_id,duration_seconds" or "-1,0"."""
        if path is None:
            return "-1,0"
        key = str(path)
        bid = self.by_path.get(key)
        if bid is None:
            bid = self.next_buffer
            self.next_buffer += 1
            self.by_path[key] = bid
            self.buffers[bid] = Path(path)
        return f"{bid},{duration_of(Path(path)):.3f}"

    def command(self, cmd: str, sid: int, a, b) -> None:
        if cmd == "start":
            self._start(sid, int(a), str(b))
        elif cmd == "gain":
            self._set_gain(sid, float(a))
        elif cmd == "stop":
            self._stop(sid)

    def busy(self, sid: int) -> bool:
        src = self.sources.get(sid)
        if src is None or not self.ready:
            return False
        if src.stream:
            return self.music_sid == sid and pygame.mixer.music.get_busy()
        return src.channel is not None and src.channel.get_busy()

    @staticmethod
    def _release_music() -> None:
        """Close the streamed track. stop() leaves the file open, and Windows cannot delete or replace an open file.
        unload() needs pygame 2.0 or newer, so older versions keep the old behaviour."""
        unload = getattr(pygame.mixer.music, "unload", None)
        if unload is not None:
            try:
                unload()
            except pygame.error:
                pass

    def update(self) -> None:
        """Forget sources that finished playing."""
        for sid in [s for s, src in self.sources.items() if not src.loop and not self.busy(s)]:
            src = self.sources.pop(sid, None)
            if self.music_sid == sid:
                self.music_sid = None
                if src is not None and src.stream and self.ready:
                    self._release_music()

    # -- playback
    def _start(self, sid: int, buffer_id: int, spec: str) -> None:
        path = self.buffers.get(buffer_id)
        if path is None or not self._init():
            return
        try:
            gain_text, _, loop_text = spec.partition(",")
            gain, loop = max(0.0, min(1.0, float(gain_text or 1))), loop_text == "1"
            self._stop(sid)
            stream = path.stat().st_size > STREAM_MIN_BYTES
            src = _Source(path, stream, loop, gain)
            if stream:
                if self.music_sid is not None and self.music_sid != sid:
                    self.sources.pop(self.music_sid, None)
                pygame.mixer.music.load(str(path))
                pygame.mixer.music.set_volume(gain)
                pygame.mixer.music.play(-1 if loop else 0)
                self.music_sid = sid
                self.stats["streamed"] += 1
            else:
                src.sound = self._sound(path)
                src.channel = src.sound.play(-1 if loop else 0)
                if src.channel is not None:
                    src.channel.set_volume(gain)
            self.sources[sid] = src
        except (pygame.error, NotImplementedError, OSError) as exc:
            self.log("warn", f"cannot play {path.name}: {exc}")

    def _sound(self, path: Path) -> pygame.mixer.Sound:
        sound = self.sfx.get(path)
        if sound is not None:
            self.sfx.move_to_end(path)
            return sound
        sound = pygame.mixer.Sound(str(path))
        self.stats["decoded"] += 1
        size = sound.get_length() * self.rate * 4
        self.sfx[path] = sound
        self.sfx_bytes += size
        while self.sfx_bytes > SFX_BUDGET_BYTES and len(self.sfx) > 1:
            old_path, old = self.sfx.popitem(last=False)
            self.sfx_bytes -= old.get_length() * self.rate * 4
        return sound

    def _set_gain(self, sid: int, gain: float) -> None:
        src = self.sources.get(sid)
        if src is None or not self.ready:
            return
        gain = max(0.0, min(1.0, gain))
        src.gain = gain
        try:
            if src.stream:
                pygame.mixer.music.set_volume(gain)
            elif src.channel is not None:
                src.channel.set_volume(gain)
        except pygame.error:
            pass

    def _stop(self, sid: int) -> None:
        src = self.sources.pop(sid, None)
        if src is None or not self.ready:
            return
        try:
            if src.stream:
                if self.music_sid == sid:
                    pygame.mixer.music.stop()
                    self._release_music()
                    self.music_sid = None
            elif src.channel is not None:
                src.channel.stop()
        except pygame.error:
            pass
