"""A character-cell canvas: draw text, boxes, half-block art and meters, then emit Rich Text."""

from __future__ import annotations

import math

from functools import lru_cache

from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.strip import Strip

EIGHTHS = " ▁▂▃▄▅▆▇█"
BRAILLE_BITS = [[0x1, 0x8], [0x2, 0x10], [0x4, 0x20], [0x40, 0x80]]


@lru_cache(maxsize=4096)
def _style(key: tuple) -> Style:
    fg, bg, bold = key
    return Style(color=fg, bgcolor=bg, bold=bold)


def width(s: str) -> int:
    return len(s)


def cut(s: str, n: int) -> str:
    if n <= 0:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


class Grid:
    def __init__(self, w: int, h: int, fg: str):
        self.w, self.h = w, h
        self.ch = [[" "] * w for _ in range(h)]
        self.fg = [[fg] * w for _ in range(h)]
        self.bg: list[list[str | None]] = [[None] * w for _ in range(h)]
        self.bold = [[False] * w for _ in range(h)]

    def put(self, x: int, y: int, s: str, fg: str | None = None, bg: str | None = None, bold: bool = False) -> int:
        """Write `s` at (x, y), clipped to the grid. Returns the x after the text."""
        if not 0 <= y < self.h:
            return x + len(s)
        for i, c in enumerate(s):
            xx = x + i
            if 0 <= xx < self.w:
                self.ch[y][xx] = c
                if fg:
                    self.fg[y][xx] = fg
                if bg:
                    self.bg[y][xx] = bg
                self.bold[y][xx] = bold
        return x + len(s)

    def fill(self, x: int, y: int, w: int, h: int, bg: str) -> None:
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.bg[yy][xx] = bg

    def box(self, x: int, y: int, w: int, h: int, fg: str, title: str = "", tfg: str | None = None) -> None:
        if w < 2 or h < 2:
            return
        self.put(x, y, "╭" + "─" * (w - 2) + "╮", fg)
        for j in range(1, h - 1):
            self.put(x, y + j, "│", fg)
            self.put(x + w - 1, y + j, "│", fg)
        self.put(x, y + h - 1, "╰" + "─" * (w - 2) + "╯", fg)
        if title:
            self.put(x + 2, y, " " + cut(title, w - 6) + " ", tfg or fg, bold=True)

    def art(self, x: int, y: int, px: list[list[str]]) -> None:
        """Half-block pixels: rows 2k and 2k+1 share one terminal row."""
        for cy in range(len(px) // 2):
            yy = y + cy
            if not 0 <= yy < self.h:
                continue
            top, bot = px[cy * 2], px[cy * 2 + 1]
            for cx in range(len(top)):
                xx = x + cx
                if 0 <= xx < self.w:
                    self.ch[yy][xx] = "▀"
                    self.fg[yy][xx] = top[cx]
                    self.bg[yy][xx] = bot[cx]
                    self.bold[yy][xx] = False

    def spectrum(self, x: int, y: int, w: int, h: int, bars: list[float], colors: tuple[str, str, str],
                 peaks: list[float] | None = None, cap: str = "") -> None:
        """Vertical bars two cells wide with a one-cell gap, coloured by height."""
        n = w // 3
        if n <= 0 or h <= 0:
            return
        low, mid, high = colors
        for b in range(n):
            # resample the source bars onto however many fit
            src = min(len(bars) - 1, int(b * len(bars) / n))
            hh = bars[src] * h * 8
            for r in range(h):
                f = max(0, min(8, round(hh - r * 8)))
                if not f:
                    break
                col = low if r < h * 0.5 else (mid if r < h * 0.8 else high)
                self.put(x + b * 3, y + h - 1 - r, EIGHTHS[f] * 2, col)
            if peaks:
                pr = min(h - 1, int(peaks[src] * h))
                if pr * 8 >= hh and peaks[src] > 0.02:
                    self.put(x + b * 3, y + h - 1 - pr, "▔▔", cap)

    def mirror(self, x: int, y: int, w: int, h: int, bars: list[float], colors: tuple[str, str, str], shadow: str) -> None:
        """Bars growing up from a centre line, with a dimmer reflection below it."""
        up = max(1, (h + 1) // 2)
        self.spectrum(x, y, w, up, bars, colors)
        down = h - up
        n = w // 3
        for b in range(n):
            v = bars[min(len(bars) - 1, int(b * len(bars) / n))]
            hh = v * down * 2  # half-cell steps, the finest a top-anchored block allows
            for r in range(down):
                f = hh - r * 2
                if f <= 0:
                    break
                self.put(x + b * 3, y + up + r, ("█" if f >= 2 else "▀") * 2, shadow)

    def scope(self, x: int, y: int, w: int, h: int, t: float, bars: list[float], colors: tuple[str, str, str]) -> None:
        """Stand-in trace for when no raw audio is available: its swing follows the spectrum."""
        if w <= 0 or h <= 0:
            return
        n = len(bars)
        bass = sum(bars[: n // 4]) / (n // 4)
        mids = sum(bars[n // 4: n // 2]) / (n // 4)
        treble = sum(bars[n // 2:]) / (n - n // 2)
        samples = [math.sin(s * 0.09 + t * 2.2) * bass * 0.9 + math.sin(s * 0.37 - t * 4.1) * mids * 0.6
                   + math.sin(s * 1.3 + t * 9.0) * treble * 0.5 for s in range(w * 2)]
        self.trace(x, y, w, h, samples, colors)

    def vector(self, x: int, y: int, w: int, h: int, pairs: list[tuple[float, float]], colors: tuple[str, str, str]) -> None:
        """A goniometer: mono content draws a vertical line, wide stereo spreads sideways."""
        if w <= 0 or h <= 0 or not pairs:
            return
        w2, h4 = w * 2, h * 4
        cx, cy = (w2 - 1) / 2, (h4 - 1) / 2
        cells: dict[tuple[int, int], int] = {}
        for l, r in pairs:
            side, mid = (l - r) * 0.7071, (l + r) * 0.7071
            px = round(cx + side * cx * 0.95)
            py = round(cy - mid * cy * 0.95)
            if 0 <= px < w2 and 0 <= py < h4:
                k = (px >> 1, py >> 2)
                cells[k] = cells.get(k, 0) | BRAILLE_BITS[py & 3][px & 1]
        low, mid_c, high = colors
        for (ccx, ccy), bits in cells.items():
            dots = bin(bits).count("1")
            self.put(x + ccx, y + ccy, chr(0x2800 + bits), low if dots <= 2 else mid_c if dots <= 5 else high)

    def trace(self, x: int, y: int, w: int, h: int, samples: list[float], colors: tuple[str, str, str]) -> None:
        """Draw a waveform (values about ±1, two per cell across) as a connected braille line."""
        if w <= 0 or h <= 0 or not samples:
            return
        h4 = h * 4
        ys = [max(0, min(h4 - 1, round((-v * 0.5 + 0.5) * (h4 - 1)))) for v in samples[: w * 2]]
        low, mid, high = colors
        cells: dict[tuple[int, int], int] = {}
        for s, a in enumerate(ys):
            b = ys[s + 1] if s + 1 < len(ys) else a
            for yy in range(min(a, b), max(a, b) + 1):
                k = (s >> 1, yy >> 2)
                cells[k] = cells.get(k, 0) | BRAILLE_BITS[yy & 3][s & 1]
        for (cx, cy), bits in cells.items():
            dist = abs(cy - (h - 1) / 2) / max(1, h / 2)  # further from the centre line = louder
            self.put(x + cx, y + cy, chr(0x2800 + bits), low if dist < 0.4 else mid if dist < 0.75 else high)

    def meter(self, x: int, y: int, w: int, v: float, label: str, dim: str, line: str, colors: tuple[str, str, str]) -> None:
        self.put(x, y, label, dim)
        low, mid, high = colors
        for i in range(w):
            on, p = i < v * w, i / max(1, w)
            self.put(x + 2 + i, y, "█" if on else "·", (low if p < 0.6 else mid if p < 0.85 else high) if on else line)

    def progress(self, x: int, y: int, w: int, f: float, on: str, off: str) -> None:
        if w < 3:
            return
        n = max(0, min(w - 1, int(f * w)))
        self.put(x, y, "━" * n + "╸", on)
        self.put(x + n + 1, y, "─" * (w - n - 1), off)

    def wave(self, x: int, y: int, w: int, h: int, t: float, level: float, col: str) -> None:
        """A braille oscilloscope: 2×4 dots per cell."""
        if w <= 0 or h <= 0:
            return
        h4 = h * 4
        amp = 0.25 + 0.75 * level
        ys = []
        for s in range(w * 2):
            v = math.sin(s * 0.21 + t * 3) * 0.55 + math.sin(s * 0.067 - t * 1.4) * 0.35 + math.sin(s * 0.9 + t * 5) * 0.1
            ys.append(round((v * amp * 0.5 + 0.5) * (h4 - 1)))
        cells: dict[tuple[int, int], int] = {}
        for s, a in enumerate(ys):
            b = ys[s + 1] if s + 1 < len(ys) else a
            for yy in range(min(a, b), max(a, b) + 1):
                k = (s >> 1, yy >> 2)
                cells[k] = cells.get(k, 0) | BRAILLE_BITS[yy & 3][s & 1]
        for (cx, cy), bits in cells.items():
            self.put(x + cx, y + cy, chr(0x2800 + bits), col)

    def to_strips(self) -> list[Strip]:
        """One Textual Strip per row, built straight from segments (much cheaper than Rich Text)."""
        strips = []
        for y in range(self.h):
            segs, run, key = [], [], None
            fg, bg, bold, ch = self.fg[y], self.bg[y], self.bold[y], self.ch[y]
            for x in range(self.w):
                k = (fg[x], bg[x], bold[x])
                if k != key:
                    if run:
                        segs.append(Segment("".join(run), _style(key)))
                    run, key = [], k
                run.append(ch[x])
            if run:
                segs.append(Segment("".join(run), _style(key)))
            strips.append(Strip(segs, self.w))
        return strips

    def to_text(self) -> Text:
        text = Text(no_wrap=True, overflow="crop")
        styles: dict[tuple, Style] = {}
        for y in range(self.h):
            run, key = [], None
            for x in range(self.w):
                k = (self.fg[y][x], self.bg[y][x], self.bold[y][x])
                if k != key and run:
                    text.append("".join(run), styles[key])
                    run = []
                if k not in styles:
                    styles[k] = Style(color=k[0], bgcolor=k[1], bold=k[2])
                key = k
                run.append(self.ch[y][x])
            if run:
                text.append("".join(run), styles[key])
            if y < self.h - 1:
                text.append("\n")
        return text
