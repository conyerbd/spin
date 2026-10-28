"""Turn Spotify API responses into the rows spin shows: albums, playlists and tracks."""

from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import asdict, dataclass

from .config import PINS_FILE


@dataclass
class Entry:
    kind: str  # "album" | "playlist" | "track"
    id: str
    uri: str
    name: str
    sub: str = ""
    plays: int = 0
    image: str = ""  # small (~64px) for list rows
    image_big: str = ""  # ~300px for the detail pane
    note: str = ""  # e.g. "played as album"
    album_uri: str = ""  # tracks: the album they belong to
    duration_ms: int = 0


def _images(images: list[dict]) -> tuple[str, str]:
    if not images:
        return "", ""
    by_size = sorted(images, key=lambda i: i.get("width") or 0)
    small = next((i["url"] for i in by_size if (i.get("width") or 0) >= 64), by_size[-1]["url"])
    big = next((i["url"] for i in by_size if (i.get("width") or 0) >= 300), by_size[-1]["url"])
    return small, big


def _artists(obj: dict) -> str:
    return ", ".join(a.get("name", "") for a in obj.get("artists", []))


def album_entry(album: dict, plays: int = 0, note: str = "") -> Entry:
    small, big = _images(album.get("images", []))
    return Entry("album", album["id"], album["uri"], album.get("name", ""), _artists(album), plays, small, big, note)


def recent_albums(items: list[dict]) -> list[Entry]:
    """Group the last 50 plays by album, newest first.

    An album you played as an album (its context is that album) is marked, so it
    stands apart from albums that only appear because one track came up in a playlist.
    """
    albums: OrderedDict[str, dict] = OrderedDict()
    for it in items:  # Spotify returns newest first
        track = it.get("track") or {}
        album = track.get("album") or {}
        if not album.get("id"):
            continue
        a = albums.setdefault(album["id"], {"album": album, "plays": 0, "as_album": False})
        a["plays"] += 1
        ctx = it.get("context") or {}
        if ctx.get("type") == "album" and ctx.get("uri") == album.get("uri"):
            a["as_album"] = True
    return [album_entry(a["album"], a["plays"], "album" if a["as_album"] else "") for a in albums.values()]


def saved_albums(items: list[dict]) -> list[Entry]:
    return [album_entry(it["album"]) for it in items if it.get("album")]


def top_albums(tracks: list[dict]) -> list[Entry]:
    """Albums ranked by how many of your top tracks they hold."""
    albums: OrderedDict[str, dict] = OrderedDict()
    for tr in tracks:
        album = tr.get("album") or {}
        if album.get("id"):
            albums.setdefault(album["id"], {"album": album, "n": 0})["n"] += 1
    ranked = sorted(albums.values(), key=lambda a: -a["n"])
    return [album_entry(a["album"], a["n"]) for a in ranked]


def playlists(items: list[dict]) -> list[Entry]:
    out = []
    for p in items:
        if not p:
            continue
        small, big = _images(p.get("images") or [])
        total = (p.get("items") or p.get("tracks") or {}).get("total", 0)
        owner = (p.get("owner") or {}).get("display_name", "")
        out.append(Entry("playlist", p["id"], p["uri"], p.get("name", ""), f"{owner} · {total} tracks" if owner else f"{total} tracks", 0, small, big))
    return out


def track_entry(tr: dict, album: dict | None = None) -> Entry:
    album = album or tr.get("album") or {}
    small, big = _images(album.get("images", []))
    return Entry("track", tr.get("id") or "", tr.get("uri", ""), tr.get("name", ""), _artists(tr), 0, small, big,
                 album_uri=album.get("uri", ""), duration_ms=tr.get("duration_ms", 0))


def search_results(data: dict) -> list[Entry]:
    albums = [album_entry(a) for a in (data.get("albums") or {}).get("items", []) if a]
    tracks = [track_entry(t) for t in (data.get("tracks") or {}).get("items", []) if t]
    return albums + tracks


# ---- pins: your rotation, kept locally ------------------------------------

def load_pins() -> list[Entry]:
    try:
        return [Entry(**e) for e in json.loads(PINS_FILE.read_text())]
    except (OSError, ValueError, TypeError):
        return []


def save_pins(pins: list[Entry]) -> None:
    PINS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PINS_FILE.write_text(json.dumps([asdict(p) for p in pins], indent=1))
