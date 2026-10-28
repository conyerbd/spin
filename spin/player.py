"""Now-playing state and transport controls for the Spotify desktop app, over MPRIS (playerctl).

Reading playback locally keeps the Web API free for library calls, so the
progress bar can tick every second without spending rate limit.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

PLAYER = "spotify"
FIELDS = ["status", "position", "mpris:length", "xesam:title", "xesam:artist", "xesam:album", "mpris:artUrl", "xesam:url", "volume"]
FORMAT = "\x1f".join("{{" + f + "}}" for f in FIELDS)


@dataclass
class NowPlaying:
    available: bool = False
    status: str = "Stopped"
    title: str = ""
    artist: str = ""
    album: str = ""
    art_url: str = ""
    url: str = ""
    length: float = 0.0
    volume: float = 0.0
    _position: float = 0.0
    _at: float = field(default_factory=time.monotonic)

    @property
    def playing(self) -> bool:
        return self.status == "Playing"

    @property
    def position(self) -> float:
        """Interpolated between polls so the progress bar moves smoothly."""
        p = self._position + (time.monotonic() - self._at if self.playing else 0)
        return min(p, self.length) if self.length else p


async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "playerctl", "-p", PLAYER, *args,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return out.decode(errors="replace").strip() if proc.returncode == 0 else ""


async def poll() -> NowPlaying:
    raw = await _run("metadata", "--format", FORMAT)
    if not raw:
        return NowPlaying()
    parts = raw.split("\x1f")
    parts += [""] * (len(FIELDS) - len(parts))
    status, pos, length, title, artist, album, art, url, vol = parts[:9]

    def num(s, scale=1.0):
        try:
            return float(s) / scale
        except ValueError:
            return 0.0

    return NowPlaying(
        available=True, status=status or "Stopped", title=title, artist=artist, album=album,
        art_url=art, url=url, length=num(length, 1e6), volume=num(vol),
        _position=num(pos, 1e6), _at=time.monotonic(),
    )


async def command(action: str) -> None:
    await _run(action)


async def open_uri(uri: str) -> None:
    await _run("open", uri)


async def volume(delta: float) -> None:
    await _run("volume", f"{abs(delta)}{'+' if delta > 0 else '-'}")
