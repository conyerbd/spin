"""Album covers as half-block pixel art: each terminal cell shows two square pixels (▀)."""

from __future__ import annotations

import hashlib
import io
import math

import httpx
from PIL import Image

from .config import CACHE_DIR

ART_DIR = CACHE_DIR / "art"


class ArtCache:
    def __init__(self):
        ART_DIR.mkdir(parents=True, exist_ok=True)
        self.images: dict[str, Image.Image] = {}
        self.pending: set[str] = set()
        self.rendered: dict[tuple, list[list[str]]] = {}
        self.client = httpx.AsyncClient(timeout=15, follow_redirects=True)

    async def close(self) -> None:
        await self.client.aclose()

    def _file(self, url: str):
        return ART_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".img")

    def get(self, url: str) -> Image.Image | None:
        """The image if it is in memory or on disk; None while it is still downloading."""
        if not url:
            return None
        if url in self.images:
            return self.images[url]
        f = self._file(url)
        if f.exists():
            try:
                img = Image.open(f).convert("RGB")
                self.images[url] = img
                return img
            except OSError:
                f.unlink(missing_ok=True)
        return None

    async def fetch(self, url: str) -> bool:
        if not url or url in self.pending or self.get(url) is not None:
            return False
        self.pending.add(url)
        try:
            r = await self.client.get(url)
            if r.status_code == 200:
                img = Image.open(io.BytesIO(r.content)).convert("RGB")
                img.thumbnail((300, 300))
                img.save(self._file(url), format="PNG")
                self.images[url] = img
                return True
        except (httpx.HTTPError, OSError):
            pass
        finally:
            self.pending.discard(url)
        return False

    def pixels(self, url: str, key: str, w: int, h_px: int) -> list[list[str]]:
        """A w × h_px grid of hex colours: the cover if loaded, else a pattern seeded by `key`."""
        img = self.get(url)
        cache_key = (url if img else "pattern:" + key, w, h_px)
        if cache_key in self.rendered:
            return self.rendered[cache_key]
        if img:
            small = img.resize((w, h_px), Image.Resampling.LANCZOS)
            px = small.load()
            grid = [[f"#{px[x, y][0]:02x}{px[x, y][1]:02x}{px[x, y][2]:02x}" for x in range(w)] for y in range(h_px)]
        else:
            grid = pattern(key, w, h_px)
        self.rendered[cache_key] = grid
        return grid


PALETTES = [
    ("#C46B8A", "#F4B6C8"), ("#B5533C", "#F2C14E"), ("#4F7C8A", "#2E4A55"), ("#7A8F4E", "#D9C77A"),
    ("#6B5B95", "#2B2440"), ("#3E8C9A", "#E6DCC6"), ("#9A6B3E", "#5A3E28"), ("#5E8C5A", "#A9C98A"),
]


def pattern(key: str, w: int, h: int) -> list[list[str]]:
    """Placeholder art while a cover loads, or when an item has none."""
    n = int(hashlib.sha1(key.encode()).hexdigest(), 16)
    a, b = PALETTES[n % len(PALETTES)]
    kind = (n >> 8) % 3
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            if kind == 0:
                on = int(math.hypot(x - w / 2 + 0.5, y - h / 2 + 0.5) / max(1, w / 10)) % 2
            elif kind == 1:
                on = ((x + y) // max(1, w // 8)) % 2
            else:
                on = (x % 4 < 2) and (y % 4 < 2)
            row.append(a if on else b)
        out.append(row)
    return out
