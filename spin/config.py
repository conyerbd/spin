"""Paths, settings and the colour palette (taken from the active Omarchy theme)."""

from __future__ import annotations

import json
import os
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "spin"
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "spin"
CONFIG_FILE = CONFIG_DIR / "config.json"
AUTH_FILE = CONFIG_DIR / "auth.json"
PINS_FILE = CONFIG_DIR / "pins.json"

DEFAULT_CONFIG = {
    # Each user's own Spotify developer app (see README). Its redirect URI must be
    # listed in the app's dashboard: http://127.0.0.1:<redirect_port><redirect_path>
    "client_id": "",
    "redirect_port": 19872,
    "redirect_path": "/login",
}

SCOPES = " ".join([
    "user-read-recently-played",
    "user-library-read",
    "user-library-modify",
    "user-top-read",
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
])


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    try:
        data = json.loads(CONFIG_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    return {**DEFAULT_CONFIG, **data}


def _mix(a: str, b: str, k: float) -> str:
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(pa, pb))


@dataclass(frozen=True)
class Palette:
    fg: str = "#E6DCC6"
    bg: str = "#2B2A27"
    accent: str = "#D9A441"
    dim: str = "#A89F8C"
    line: str = "#4A4740"
    sel: str = "#3A3833"
    green: str = "#9DBF6B"
    red: str = "#D9764A"


def prune_cache(days: int = 30) -> None:
    """Spotify's terms say not to keep its content indefinitely: drop cached API
    responses and cover art that haven't been refreshed in `days` days."""
    cutoff = time.time() - days * 86400
    for sub in ("api", "art"):
        for f in (CACHE_DIR / sub).glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass


def save_config(cfg: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


# Spectrum colours run green → accent → red from low to high, so each theme's
# "green" and "red" are its quiet and loud ends rather than literal greens and reds.
THEMES = {
    "synthwave": Palette(fg="#F4E9FF", bg="#1A1026", accent="#FF4FA3", dim="#9C86B8", line="#4A3566", sel="#2E1F45", green="#3FE0D0", red="#FFB347"),
    "phosphor": Palette(fg="#A8FFA8", bg="#0B140B", accent="#39FF14", dim="#5C9F5C", line="#1F3A1F", sel="#143214", green="#2E8B2E", red="#D4FF6A"),
    "amber": Palette(fg="#FFC66D", bg="#160F05", accent="#FF9F1C", dim="#A07A3A", line="#3D2A10", sel="#2A1C08", green="#B36B00", red="#FFE29A"),
    "ice": Palette(fg="#E5E9F0", bg="#1E2430", accent="#88C0D0", dim="#8F98AA", line="#3B4252", sel="#2E3440", green="#A3BE8C", red="#BF616A"),
    "mono": Palette(fg="#E8E8E8", bg="#111111", accent="#FFFFFF", dim="#8C8C8C", line="#333333", sel="#262626", green="#7A7A7A", red="#FFFFFF"),
}
THEME_NAMES = ["omarchy", *THEMES]


def theme_palette(name: str) -> Palette:
    """`omarchy` follows the desktop theme; the rest are built in."""
    return THEMES.get(name) or load_palette()


def load_palette() -> Palette:
    """Read the current Omarchy theme's colors.toml; fall back to the Aureth defaults."""
    candidates = [
        Path.home() / ".local/state/omarchy/current/theme/colors.toml",
        Path.home() / ".config/omarchy/current/theme/colors.toml",
    ]
    for path in candidates:
        try:
            c = tomllib.loads(path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            continue
        fg = c.get("foreground", Palette.fg)
        bg = c.get("background", Palette.bg)
        return Palette(
            fg=fg,
            bg=bg,
            accent=c.get("accent", Palette.accent),
            dim=_mix(fg, bg, 0.35),
            line=_mix(fg, bg, 0.72),
            sel=c.get("color8", _mix(fg, bg, 0.88)),
            green=c.get("color10", Palette.green),
            red=c.get("color9", Palette.red),
        )
    return Palette()
