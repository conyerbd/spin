"""Audio levels for the visualizer.

With `cava` installed the bars follow the real audio from PipeWire. Without
it, a synthetic spectrum animates while music is playing and settles when paused.
"""

from __future__ import annotations

import array
import asyncio
import math
import random
import shutil
import time

from .config import CACHE_DIR

BARS = 64
MODES = ["bars", "peaks", "mirror", "scope", "vector"]
RATE = 24000  # PCM sample rate for the scope and meters; plenty for drawing, cheap to read


class Levels:
    def __init__(self):
        self.bars = [0.0] * BARS
        self.real = False
        self._proc = None
        self._task = None
        self._seed = random.random() * 100
        self.peak = [0.0] * BARS
        self._peak_at = time.monotonic()

    @property
    def available(self) -> bool:
        return shutil.which("cava") is not None

    async def start(self, fps: int = 60) -> None:
        if not self.available:
            return
        conf = CACHE_DIR / "cava.conf"
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(
            "[general]\n"
            f"bars = {BARS}\n"
            f"framerate = {fps}\n"
            "[input]\n"
            "method = pipewire\n"
            "[output]\n"
            "method = raw\n"
            "raw_target = /dev/stdout\n"
            "data_format = ascii\n"
            "ascii_max_range = 1000\n"
            "bar_delimiter = 59\n"
            "frame_delimiter = 10\n"
            "channels = mono\n"
        )
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "cava", "-p", str(conf),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                self.real = False
                return
            vals = [v for v in line.decode().strip().split(";") if v]
            if len(vals) >= BARS:
                self.bars = [min(1.0, int(v) / 1000) for v in vals[:BARS]]
                self.real = True

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    def peaks(self, bars: list[float]) -> list[float]:
        """Peak caps that jump to each new high and then fall back slowly."""
        now = time.monotonic()
        fall = (now - self._peak_at) * 0.6
        self._peak_at = now
        self.peak = [max(v, p - fall) for v, p in zip(bars, self.peak)]
        return self.peak

    def frame(self, playing: bool) -> list[float]:
        """Current bar heights, 0–1."""
        if self.real:
            return self.bars
        t = time.monotonic()
        out = []
        for b in range(BARS):
            if not playing:
                out.append(0.04 + 0.02 * math.sin(b * 0.4 + t * 0.6))
                continue
            noise = (math.sin(b * 12.9898 + int(t * 6) * 78.233 + self._seed) * 43758.5453) % 1
            v = (math.sin(b * 0.35 + t * 1.3) * 0.5 + 0.5) * 0.5 + (math.sin(b * 0.11 - t * 2.1) * 0.5 + 0.5) * 0.3 + noise * 0.25
            out.append(min(1.0, v * (1.05 - b / BARS * 0.45)))
        return out


class Pcm:
    """Raw stereo samples from whatever is playing (the default output's monitor), via pw-record.

    These drive the true waveform views (scope, vector) and the L/R level meters.
    """

    def __init__(self):
        self.buf = array.array("h")  # interleaved L, R
        self.keep = RATE // 2  # frames: half a second is plenty for any view
        self.at = 0.0
        self._proc = None
        self._task = None
        self._gain = 1.0

    @property
    def available(self) -> bool:
        return shutil.which("pw-record") is not None

    @property
    def live(self) -> bool:
        return time.monotonic() - self.at < 0.5 and len(self.buf) >= 4096

    async def start(self) -> None:
        if not self.available:
            return
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "pw-record", "-P", "{ stream.capture.sink=true node.name=spin-scope }",
                "--rate", str(RATE), "--channels", "2", "--format", "s16", "--raw", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError:
            return
        self._task = asyncio.create_task(self._read())

    async def _read(self) -> None:
        assert self._proc and self._proc.stdout
        left = b""
        while True:
            chunk = await self._proc.stdout.read(8192)
            if not chunk:
                return
            data = left + chunk
            cut = len(data) // 4 * 4  # whole stereo frames only
            left = data[cut:]
            self.buf.frombytes(data[:cut])
            extra = len(self.buf) - self.keep * 2
            if extra > 0:
                del self.buf[:extra]
            self.at = time.monotonic()

    async def stop(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()

    def levels(self, frames: int = 2048) -> tuple[float, float]:
        """RMS level per channel over the last ~85 ms, mapped from -50…0 dBFS onto 0…1."""
        tail = self.buf[-frames * 2:]
        out = []
        for ch in (tail[0::2], tail[1::2]):
            if not ch:
                out.append(0.0)
                continue
            rms = math.sqrt(sum(v * v for v in ch) / len(ch)) / 32768
            db = 20 * math.log10(rms) if rms > 0 else -100
            out.append(max(0.0, min(1.0, (db + 50) / 50)))
        return out[0], out[1]

    def scope(self, n: int, points: int) -> list[float]:
        """`points` samples of the mono signal spanning `n` frames, starting at a rising zero
        crossing so the trace holds still like a triggered oscilloscope. Scaled to about ±1."""
        need = n + RATE // 50  # extra room to search for the trigger
        tail = self.buf[-need * 2:]
        mono = [(tail[i] + tail[i + 1]) / 65536 for i in range(0, len(tail) - 1, 2)]
        if len(mono) < n:
            return [0.0] * points
        start = 0
        for i in range(1, len(mono) - n):
            if mono[i - 1] < 0 <= mono[i]:
                start = i
                break
        win = mono[start:start + n]
        peak = max(0.05, max(abs(v) for v in win))
        # Auto-gain: drop at once when the trace would clip, recover slowly, so quiet
        # passages still show their shape without the trace jumping around.
        if peak * self._gain > 1:
            self._gain = 0.9 / peak
        else:
            self._gain += (0.9 / peak - self._gain) * 0.03
        step = n / points
        return [max(-1.0, min(1.0, win[int(i * step)] * self._gain)) for i in range(points)]

    def stereo(self, n: int) -> list[tuple[float, float]]:
        """The last `n` frames as (left, right) pairs scaled to about ±1, for the vectorscope."""
        tail = self.buf[-n * 2:]
        pairs = [(tail[i] / 32768, tail[i + 1] / 32768) for i in range(0, len(tail) - 1, 2)]
        peak = max([0.02] + [max(abs(l), abs(r)) for l, r in pairs])
        g = 0.95 / peak
        return [(l * g, r * g) for l, r in pairs]
