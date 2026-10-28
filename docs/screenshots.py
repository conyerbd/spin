"""Render the README screenshots with made-up demo data.

Nothing here touches Spotify or your own library: albums, covers and audio are
all generated, so the images are safe to publish. Run from the repo root:

    .venv/bin/python docs/screenshots.py

Writes PNGs to docs/screenshots/ (via headless chromium; SVGs if it is missing).
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Keep the run away from the real config, tokens and cache.
_tmp = tempfile.mkdtemp(prefix="spin-shots-")
os.environ["XDG_CONFIG_HOME"] = _tmp
os.environ["XDG_CACHE_HOME"] = _tmp

from PIL import Image, ImageDraw  # noqa: E402

from spin import viz  # noqa: E402
from spin.app import SpinApp  # noqa: E402
from spin.config import Palette, theme_palette  # noqa: E402
from spin.library import Entry  # noqa: E402

OUT = Path(__file__).parent / "screenshots"

# ---- demo library (all fictional) -------------------------------------------

ALBUMS = [
    ("Neon Harbor", "The Night Shift", 12, ("#2A1B3D", "#FF4FA3", "#F7D154"), "sun"),
    ("Glass Cathedral", "Aurora Vale", 7, ("#1E2A44", "#88C0D0", "#E5E9F0"), "rings"),
    ("Midnight Transit", "Lowlight", 5, ("#121418", "#D9A441", "#E6DCC6"), "lines"),
    ("Static Bloom", "Paper Satellites", 4, ("#3A2238", "#E07A9B", "#F4E9FF"), "bloom"),
    ("Coastline FM", "Vela", 3, ("#0F3B47", "#3FE0D0", "#F2C14E"), "waves"),
    ("Analog Hearts", "Kilowatt Kid", 2, ("#2B2A27", "#C65A3A", "#F2E9D8"), "grid"),
    ("Low Orbit", "Mira Sol", 2, ("#0B0F1E", "#6F8796", "#FFF6E6"), "planet"),
    ("Pine & Ember", "Hollow Creek", 1, ("#1F2A1B", "#7C9A4D", "#E37B52"), "mountains"),
    ("Cassette Summer", "Retrograde", 1, ("#3D2A10", "#FF9F1C", "#FFE29A"), "stripes"),
]
TRACKS = [("Harbor Lights", 212), ("Night Ferry", 248), ("Signal Fire", 196), ("Dockside", 231),
          ("Neon Tide", 247), ("Undertow", 203), ("Lantern Row", 265), ("Fog Machine", 189),
          ("Last Call", 222), ("Breakwater", 254), ("Afterglow", 238), ("Sunrise Over Pier 9", 301)]


def cover(colors, motif, size=300) -> Image.Image:
    """Simple generated cover art."""
    bg, a, b = colors
    img = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(img)
    s = size
    if motif == "sun":
        for y in range(int(s * .75)):  # sky: background fading toward the accent at the horizon
            t = y / (s * .75) * 0.45
            d.line([(0, y), (s, y)], fill=tuple(int(int(bg[k:k + 2], 16) * (1 - t) + int(a[k:k + 2], 16) * t) for k in (1, 3, 5)))
        d.ellipse([s * .2, s * .15, s * .8, s * .75], fill=b)
        d.pieslice([s * .2, s * .15, s * .8, s * .75], 0, 180, fill=a)
        for y in range(int(s * .5), int(s * .75), 14):
            d.rectangle([0, y, s, y + 5], fill=bg)
        for i in range(8):
            y = s * .75 + (i * i) * 2.2
            d.line([(0, y), (s, y)], fill=a, width=2)
        for i in range(-6, 7):
            d.line([(s / 2 + i * 12, s * .75), (s / 2 + i * 80, s)], fill=a, width=2)
    elif motif == "rings":
        for r in range(s // 2, 0, -22):
            d.ellipse([s / 2 - r, s / 2 - r, s / 2 + r, s / 2 + r], outline=a if (r // 22) % 2 else b, width=9)
    elif motif == "lines":
        for i in range(0, s, 18):
            d.line([(0, i), (s, i + s * .4)], fill=a, width=3)
        d.rectangle([s * .3, s * .3, s * .7, s * .7], fill=b)
    elif motif == "bloom":
        for k in range(8):
            ang = k * math.pi / 4
            cx, cy = s / 2 + math.cos(ang) * s * .18, s / 2 + math.sin(ang) * s * .18
            d.ellipse([cx - s * .16, cy - s * .16, cx + s * .16, cy + s * .16], fill=a)
        d.ellipse([s * .4, s * .4, s * .6, s * .6], fill=b)
    elif motif == "waves":
        for row in range(10):
            pts = [(x, s * .15 + row * s * .08 + math.sin(x / 22 + row) * 10) for x in range(0, s + 1, 6)]
            d.line(pts, fill=a if row % 2 else b, width=5)
    elif motif == "grid":
        for i in range(0, s, 30):
            d.line([(i, 0), (i, s)], fill=a, width=2)
            d.line([(0, i), (s, i)], fill=a, width=2)
        d.polygon([(s / 2, s * .25), (s * .78, s * .7), (s * .22, s * .7)], fill=b)
    elif motif == "planet":
        for i in range(60):
            x, y = (i * 97) % s, (i * 53) % s
            d.point((x, y), fill=b)
        d.ellipse([s * .25, s * .25, s * .75, s * .75], fill=a)
        d.arc([s * .1, s * .4, s * .9, s * .62], 160, 380, fill=b, width=6)
    elif motif == "mountains":
        d.polygon([(0, s), (s * .35, s * .35), (s * .7, s)], fill=a)
        d.polygon([(s * .3, s), (s * .7, s * .25), (s, s * .8), (s, s)], fill=a)
        d.ellipse([s * .65, s * .1, s * .85, s * .3], fill=b)
    else:
        for i, col in enumerate([a, b, a, b, a]):
            d.rectangle([0, s * (.3 + i * .09), s, s * (.3 + i * .09) + s * .05], fill=col)
    return img


def entries():
    albums = []
    for i, (name, artist, plays, colors, motif) in enumerate(ALBUMS):
        url = f"demo://{i}"
        albums.append(Entry("album", f"album{i}", f"spotify:album:demo{i}", name, artist, plays, url, url,
                            note="album" if plays > 3 else ""))
    tracks = [Entry("track", f"t{i}", f"spotify:track:t{i}", n, "The Night Shift", duration_ms=d * 1000,
                    album_uri="spotify:album:demo0") for i, (n, d) in enumerate(TRACKS)]
    return albums, tracks


def fake_pcm(pcm: viz.Pcm, t0: float) -> None:
    """A few seconds of a made-up stereo mix, so the scope, vector view and meters draw."""
    frames = pcm.keep
    data = []
    for n in range(frames):
        t = t0 + n / viz.RATE
        kick = math.exp(-((t * 2.1) % 1) * 9) * math.sin(2 * math.pi * 55 * t)
        bass = 0.35 * math.sin(2 * math.pi * 110 * t + math.sin(2 * math.pi * 0.5 * t))
        lead = 0.22 * math.sin(2 * math.pi * 440 * t) * (0.6 + 0.4 * math.sin(2 * math.pi * 3 * t))
        pad = 0.12 * math.sin(2 * math.pi * 330 * t + 0.6)
        left = 0.5 * (kick + bass + lead + pad * 1.4)
        right = 0.5 * (kick + bass + lead * 0.6 + pad * 0.5 + 0.1 * math.sin(2 * math.pi * 660 * t))
        data += [int(max(-1, min(1, left)) * 9000), int(max(-1, min(1, right)) * 9000)]
    pcm.buf = viz.array.array("h", data)
    pcm.at = time.monotonic() + 3600  # counts as live for the whole capture


class Demo(SpinApp):
    def __init__(self, theme: str, visual: str, section: str):
        super().__init__({"client_id": "0" * 32, "redirect_port": 1, "redirect_path": "/",
                          "theme": theme, "visualizer": visual, "fps": 60},
                         {"access_token": "x", "refresh_token": "x", "expires_at": 9e18})
        self.pal = Palette() if theme == "omarchy" else theme_palette(theme)
        self._start_section = section
        albums, tracks = entries()
        for i, (*_, colors, motif) in enumerate(ALBUMS):
            self.art.images[f"demo://{i}"] = cover(colors, motif)
        self.lists.update({"recent": albums, "saved": albums[::-1], "top": albums[2:] + albums[:2],
                           "pinned": [albums[0], albums[1], albums[3], albums[7]], "now": tracks})
        self._detail["album0"] = tracks
        self._detail["album1"] = tracks[:9]
        self.ctx_entry = albums[0]
        self.queue_tracks = tracks[5:10]
        self.sel.update({"recent": 1, "now": 4})
        now = self.now
        now.available, now.status, now.title, now.artist, now.album = True, "Playing", "Neon Tide", "The Night Shift", "Neon Harbor"
        now.art_url, now.url, now.length, now._position, now.volume = "demo://0", "spotify:track:t4", 247, 96, 0.7

    # no network, no real audio
    def load_section(self, key, force=False): pass
    def poll_player(self): pass
    def refresh_context(self): pass
    def fetch_art(self, url): pass


async def shoot(name: str, cols: int, rows: int, theme: str, visual: str, section: str, keys=()) -> Path:
    viz.Levels.available = property(lambda self: False)
    viz.Pcm.available = property(lambda self: False)
    app = Demo(theme, visual, section)
    async with app.run_test(size=(cols, rows)) as pilot:
        app.section = [k for k, _ in app.SECTIONS].index(section)
        fake_pcm(app.pcm, 1.3)
        app.levels._seed = 4.2
        app._status = ("", 0.0)  # hide the "install cava" tip
        for k in keys:
            await pilot.press(k)
        await pilot.pause(1.2)
        svg = app.export_screenshot(title="spin")
    path = OUT / f"{name}.svg"
    path.write_text(svg)
    return path


def to_png(svg: Path) -> None:
    chrome = shutil.which("chromium") or shutil.which("google-chrome-stable")
    if not chrome:
        return
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg.read_text())
    w, h = (math.ceil(float(v)) for v in m.groups())
    html = svg.with_suffix(".html")
    html.write_text(f"<html><body style='margin:0;background:transparent'><img src='{svg.name}' width='{w}' height='{h}'></body></html>")
    subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
                    "--default-background-color=00000000", f"--window-size={w},{h}",
                    f"--screenshot={svg.with_suffix('.png')}", html.resolve().as_uri()],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    html.unlink()
    svg.unlink()  # the PNG is what the README uses; the SVG's web font doesn't load on GitHub


SHOTS = [
    # name, cols, rows, theme, visualizer, section, keys
    ("full", 160, 46, "omarchy", "bars", "recent", ()),
    ("now-playing", 160, 46, "synthwave", "scope", "now", ()),
    ("tall", 78, 46, "phosphor", "mirror", "recent", ()),
    ("wide", 160, 22, "amber", "peaks", "recent", ()),
    ("quarter", 78, 22, "ice", "bars", "pinned", ()),
]


async def main(names: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        if names and shot[0] not in names:
            continue
        svg = await shoot(*shot)
        to_png(svg)
        print("wrote", svg.with_suffix(".png" if svg.with_suffix(".png").exists() else ".svg"))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
