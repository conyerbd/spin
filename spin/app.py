"""spin: a terminal remote for the Spotify desktop app."""

from __future__ import annotations

import asyncio
import socket
import time

import httpx
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Input, Label

from . import library, player
from .api import ApiError, RateLimited, Spotify
from .art import ArtCache
from .auth import AuthError
from .config import THEME_NAMES, save_config, theme_palette
from .library import Entry
from .ui import draw, mode_for
from .viz import MODES, Levels, Pcm

# Anything a Spotify call can fail with; workers report these instead of crashing.
NET_ERRORS = (ApiError, AuthError, httpx.HTTPError, OSError)


class SpinView(Widget):
    """The whole screen, drawn as one character grid and handed to Textual line by line."""

    can_focus = True

    def __init__(self):
        super().__init__()
        self._strips: list[Strip] | None = None
        self._drawn_size = (0, 0)

    def redraw(self) -> None:
        self._strips = None
        self.refresh()

    def render_line(self, y: int) -> Strip:
        w, h = self.size.width, self.size.height
        if self._strips is None or self._drawn_size != (w, h):
            self._strips = draw(self.app, w, h).to_strips()
            self._drawn_size = (w, h)
        return self._strips[y] if y < len(self._strips) else Strip.blank(w)

    async def on_key(self, event: events.Key) -> None:
        if await self.app.handle_key(event):
            event.stop()
            event.prevent_default()


class SearchScreen(ModalScreen[str]):
    DEFAULT_CSS = """
    SearchScreen { align: center top; background: $background 40%; }
    SearchScreen > Vertical { width: 70; max-width: 90%; height: auto; margin-top: 3; padding: 1 2; border: round $accent; background: $surface; }
    SearchScreen Label { color: $text-muted; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("search albums and tracks  ·  ⏎ search  ·  esc cancel")
            yield Input(placeholder="artist, album or song")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss("")


class SpinApp(App):
    CSS = "Screen { layers: base; } SpinView { width: 100%; height: 100%; }"
    ENABLE_COMMAND_PALETTE = False

    SECTIONS = [
        ("now", "now playing"),
        ("pinned", "pinned"),
        ("recent", "recent albums"),
        ("saved", "saved albums"),
        ("top", "top · last month"),
        ("playlists", "playlists"),
        ("search", "search"),
    ]
    # Sections that `s` switches between albums and songs.
    SONG_SECTIONS = {"pinned": "pinned songs", "recent": "recent songs", "saved": "liked songs", "top": "top songs"}

    def __init__(self, cfg: dict, tokens: dict):
        super().__init__()
        self.cfg = cfg
        self.api = Spotify(cfg, tokens)
        self.art = ArtCache()
        self.levels = Levels()
        self.pcm = Pcm()
        self.theme_name = cfg.get("theme", "omarchy")
        self.pal = theme_palette(self.theme_name)
        self.viz_mode = cfg.get("visualizer", "bars") if cfg.get("visualizer") in MODES else "bars"
        # Redraws per second for the visualizer; cava samples at the same rate.
        self.fps = max(10, min(120, int(cfg.get("fps", 60))))
        self.now = player.NowPlaying()
        self.section = 2
        self.ctx_entry: Entry | None = None  # the album or playlist that is playing
        self.queue_tracks: list[Entry] | None = None
        self._track_url = ""
        self.focus = "list"
        self.songs = cfg.get("view") == "songs"  # albums or songs in the SONG_SECTIONS
        self.pins = library.load_pins()
        self.lists: dict[str, list[Entry]] = {}
        self._split_pins()
        self.loading: set[str] = set()
        self.errors: dict[str, str] = {}
        self.sel: dict[str, int] = {}
        self.scroll: dict[str, int] = {}
        self.detail_open = False
        self.detail_sel = 0
        self._detail: dict[str, list[Entry]] = {}
        self._detail_want = ""
        self.show_help = False
        self._status = ("", 0.0)
        self._device_id = ""

    # ---- lifecycle ---------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield SpinView()

    async def on_mount(self) -> None:
        self.title = "spin"
        self.screen.styles.background = self.pal.bg
        self.query_one(SpinView).focus()
        await self.levels.start(self.fps)
        await self.pcm.start()
        self.set_interval(1.0, self.poll_player)
        self.set_interval(1 / self.fps, self.tick)
        self.poll_player()
        self.load_section("recent")
        if not self.levels.available:
            self.status("tip: install cava so the visualizer follows the real audio", 8)

    async def on_unmount(self) -> None:
        await self.levels.stop()
        await self.pcm.stop()
        await self.api.close()
        await self.art.close()

    def tick(self) -> None:
        for view in self.query(SpinView):  # empty while the app is shutting down
            view.redraw()

    # ---- state the UI reads --------------------------------------------------

    @property
    def section_key(self) -> str:
        return self.SECTIONS[self.section][0]

    def key_for(self, key: str) -> str:
        """Where a section's entries live: `recent` holds albums, `recent:songs` holds songs."""
        return f"{key}:songs" if self.songs and key in self.SONG_SECTIONS else key

    @property
    def list_key(self) -> str:
        return self.key_for(self.section_key)

    def section_label(self, key: str, label: str) -> str:
        return self.SONG_SECTIONS[key] if self.songs and key in self.SONG_SECTIONS else label

    def _split_pins(self) -> None:
        self.lists["pinned"] = [p for p in self.pins if p.kind != "track"]
        self.lists["pinned:songs"] = [p for p in self.pins if p.kind == "track"]

    def section_title(self) -> str:
        key, label = self.SECTIONS[self.section]
        label = self.section_label(key, label)
        if key == "recent":
            return f"{label} · last 50 plays"
        if key == "now":
            return f"now playing · {self.ctx_entry.name}" if self.ctx_entry else "now playing"
        if key == "search" and getattr(self, "_query", ""):
            return f"search · {self._query}"
        return label

    def list_title(self) -> str:
        """Title over the list in the smaller layouts, where tracks replace the list."""
        if self.detail_open:
            if self.section_key == "now":
                return "up next"
            e = self.detail_entry()
            return e.name if e else self.section_title()
        return self.section_title()

    def current_list(self) -> list[Entry]:
        return self.lists.get(self.list_key) or []

    def list_message(self) -> str:
        key = self.section_key
        if self.list_key in self.loading:
            return "loading…"
        if self.list_key in self.errors:
            return self.errors[self.list_key]
        if key == "now":
            return "nothing playing yet" if not self.now.title else "finding what's playing…"
        if key == "pinned":
            what = "a song" if self.songs else "an album"
            return f"nothing pinned yet: press p on {what} to pin it here"
        if key == "search":
            return "press / to search"
        return "nothing here"

    def selected(self) -> Entry | None:
        items = self.current_list()
        if not items:
            return None
        i = min(self.sel.get(self.list_key, 0), len(items) - 1)
        return items[i]

    def detail_entry(self) -> Entry | None:
        if self.section_key == "now":
            return self.ctx_entry
        return self.selected()

    def detail_tracks(self) -> list[Entry] | None:
        """Tracks for the detail pane: the open album's, or "up next" on the now playing tab."""
        if self.section_key == "now":
            return self.queue_tracks
        e = self.detail_entry()
        if e is None:
            return None
        if e.kind == "track":
            return [e]
        tracks = self._detail.get(e.id)
        if tracks is None:
            self.want_detail(e)
        return tracks

    def scroll_to(self, key: str, sel: int, cap: int) -> int:
        top = self.scroll.get(key, 0)
        if sel < top:
            top = sel
        elif sel >= top + cap:
            top = sel - cap + 1
        self.scroll[key] = max(0, top)
        return self.scroll[key]

    def is_pinned(self, e: Entry) -> bool:
        return any(p.id == e.id for p in self.pins)

    def status(self, text: str, seconds: float = 4) -> None:
        self._status = (text, time.monotonic() + seconds)

    def status_text(self) -> str:
        text, until = self._status
        return text if time.monotonic() < until else ""

    def rate_limited(self) -> bool:
        return time.time() < self.api.blocked_until

    def want_art(self, url: str) -> None:
        if url and url not in self.art.images and url not in self.art.pending:
            self.fetch_art(url)

    def want_detail(self, e: Entry) -> None:
        if e.id != self._detail_want:
            self._detail_want = e.id
            self.load_detail(e)

    # ---- workers -----------------------------------------------------------

    @work(group="art")
    async def fetch_art(self, url: str) -> None:
        await self.art.fetch(url)

    @work(exclusive=True, group="player")
    async def poll_player(self) -> None:
        self.now = await player.poll()
        if self.now.url != self._track_url:
            self._track_url = self.now.url
            if self.now.url:
                self.refresh_context()

    @work(group="sections")
    async def load_section(self, key: str, force: bool = False) -> None:
        if key == "now":
            if self.ctx_entry is None or force:
                self.refresh_context()
            return
        songs = self.songs  # fixed for this load even if `s` is pressed meanwhile
        lk = self.key_for(key)
        if key in ("pinned", "search") or (lk in self.lists and not force):
            return
        self.loading.add(lk)
        self.errors.pop(lk, None)
        try:
            if key == "recent":
                items = await self.api.recently_played(force)
                self.lists[lk] = library.recent_songs(items) if songs else library.recent_albums(items)
            elif key == "saved":
                self.lists[lk] = (library.saved_songs(await self.api.saved_tracks(force)) if songs
                                  else library.saved_albums(await self.api.saved_albums(force)))
            elif key == "top":
                tracks = await self.api.top_tracks(force)
                self.lists[lk] = [library.track_entry(t) for t in tracks] if songs else library.top_albums(tracks)
            elif key == "playlists":
                self.lists[lk] = library.playlists(await self.api.playlists(force))
            if force:
                self.status(f"refreshed {self.section_label(key, dict(self.SECTIONS)[key])}")
        except RateLimited as e:
            self.errors[lk] = f"Spotify rate limit, try again in {int(e.retry_after // 60) + 1} min"
        except NET_ERRORS as e:
            self.errors[lk] = f"couldn't load: {e}"
        finally:
            self.loading.discard(lk)

    async def _tracks(self, e: Entry) -> list[Entry] | None:
        """An album's or playlist's tracks, cached; None if Spotify is rate limiting."""
        if e.id in self._detail:
            return self._detail[e.id]
        try:
            if e.kind == "album":
                album = {"uri": e.uri, "images": []}
                tracks = [library.track_entry(tr, album) for tr in await self.api.album_tracks(e.id)]
            else:
                tracks = [library.track_entry(tr) for tr in await self.api.playlist_tracks(e.id)]
        except RateLimited:
            self.status("Spotify rate limit: tracklist unavailable for now")
            return None
        except NET_ERRORS as err:
            self.status(f"couldn't load tracks: {err}")
            tracks = []
        self._detail[e.id] = tracks
        return tracks

    @work(exclusive=True, group="detail")
    async def load_detail(self, e: Entry) -> None:
        await asyncio.sleep(0.25)  # let fast j/k scrolling settle before calling the API
        if e.kind == "track":
            return
        if await self._tracks(e) is None:
            self._detail_want = ""

    def select_current(self) -> None:
        """Point the now playing list at the track that is playing."""
        for i, tr in enumerate(self.lists.get("now") or []):
            if (tr.id and self.now.url.endswith(tr.id)) or tr.name == self.now.title:
                self.sel["now"] = i
                return

    @work(exclusive=True, group="context")
    async def refresh_context(self) -> None:
        """On each track change: what album or playlist is playing, and what's up next."""
        await asyncio.sleep(0.5)  # Spotify's player state lags the desktop app slightly
        try:
            state = await self.api.request("GET", "/me/player")
        except (RateLimited, *NET_ERRORS):
            return
        item = (state or {}).get("item") or {}
        ctx = (state or {}).get("context") or {}
        entry = None
        if ctx.get("type") == "playlist" and ctx.get("uri"):
            pid = ctx["uri"].rsplit(":", 1)[-1]
            entry = next((p for p in self.lists.get("playlists") or [] if p.id == pid), None)
            if entry is None:
                try:
                    data = await self.api.get(f"/playlists/{pid}", {"fields": "id,uri,name,images,owner(display_name)"}, ttl=86400)
                    entry = library.playlists([data])[0]
                except (RateLimited, *NET_ERRORS, IndexError, KeyError):
                    entry = Entry("playlist", pid, ctx["uri"], "playlist")
        elif (item.get("album") or {}).get("id"):
            entry = library.album_entry(item["album"])
        if entry is not None and (self.ctx_entry is None or self.ctx_entry.id != entry.id):
            self.ctx_entry = entry
            self.lists["now"] = await self._tracks(entry) or []
        self.select_current()
        try:
            q = await self.api.request("GET", "/me/player/queue")
            self.queue_tracks = [library.track_entry(t) for t in (q or {}).get("queue", []) if t and t.get("type") == "track"]
        except (RateLimited, *NET_ERRORS):
            pass

    @work(exclusive=True, group="search")
    async def run_search(self, q: str) -> None:
        if not q:
            return
        self._query = q
        self.section = [k for k, _ in self.SECTIONS].index("search")
        self.loading.add("search")
        self.lists["search"] = []
        self.sel["search"] = 0
        try:
            self.lists["search"] = library.search_results(await self.api.search(q))
        except (RateLimited, *NET_ERRORS) as e:
            self.errors["search"] = f"search failed: {e}"
        finally:
            self.loading.discard("search")

    async def _device(self) -> str:
        devices = await self.api.devices()
        host = socket.gethostname().lower()
        for test in (lambda d: d.get("is_active"), lambda d: d.get("name", "").lower() == host,
                     lambda d: d.get("type") == "Computer"):
            for d in devices:
                if test(d) and not d.get("is_restricted"):
                    return d["id"]
        return devices[0]["id"] if devices else ""

    @work(exclusive=True, group="play")
    async def play(self, e: Entry, offset: Entry | None = None) -> None:
        if not self.now.available:
            self.status("start the Spotify desktop app first, then try again")
            return
        context = e.uri if e.kind in ("album", "playlist") else e.album_uri
        offset_uri = offset.uri if offset else (e.uri if e.kind == "track" else None)
        try:
            device = await self._device()
            await self.api.play(device or None, context, offset_uri)
            self.status(f"playing {e.name}")
        except (RateLimited, *NET_ERRORS) as err:
            # The Web API refused; MPRIS can still start the album from the top.
            await player.open_uri(context)
            self.status(f"playing {e.name} (via desktop app: {err})")
        await asyncio.sleep(0.6)
        self.poll_player()

    @work(exclusive=True, group="play")
    async def play_songs(self, songs: list[Entry], i: int) -> None:
        """Play a song from a song list, then carry on down that list."""
        if not self.now.available:
            self.status("start the Spotify desktop app first, then try again")
            return
        uris = [s.uri for s in songs[i:i + 100] if s.uri]
        try:
            await self.api.play_uris(await self._device() or None, uris)
            self.status(f"playing {songs[i].name}")
        except (RateLimited, *NET_ERRORS) as err:
            await player.open_uri(songs[i].uri)
            self.status(f"playing {songs[i].name} (via desktop app: {err})")
        await asyncio.sleep(0.6)
        self.poll_player()

    def play_row(self, i: int) -> None:
        """Play entry i of the current list the way that list calls for."""
        items = self.current_list()
        e = items[i]
        if self.section_key == "now" and self.ctx_entry:
            self.play(self.ctx_entry, e)
        elif e.kind == "track" and self.section_key in self.SONG_SECTIONS:
            self.play_songs(items, i)
        else:
            self.play(e)

    @work(group="queue")
    async def queue(self, tr: Entry) -> None:
        try:
            await self.api.queue(tr.uri)
            self.status(f"queued {tr.name}")
        except (RateLimited, *NET_ERRORS) as err:
            self.status(f"couldn't queue: {err}")

    @work(group="player-cmd")
    async def player_cmd(self, action: str) -> None:
        await player.command(action)
        await asyncio.sleep(0.25)
        self.poll_player()

    @work(group="player-cmd")
    async def change_volume(self, delta: float) -> None:
        await player.volume(delta)
        self.poll_player()

    # ---- input -------------------------------------------------------------

    def switch_section(self, d: int) -> None:
        self.section = (self.section + d) % len(self.SECTIONS)
        self.detail_open = False
        self.detail_sel = 0
        self.load_section(self.section_key)

    def move(self, d: int) -> None:
        if self.focus == "nav":
            self.section = max(0, min(len(self.SECTIONS) - 1, self.section + d))
            self.load_section(self.section_key)
            return
        if self.focus == "detail" or self.detail_open:
            tracks = self.detail_tracks() or []
            if tracks:
                self.detail_sel = max(0, min(len(tracks) - 1, self.detail_sel + d))
            return
        items = self.current_list()
        if items:
            key = self.list_key
            self.sel[key] = max(0, min(len(items) - 1, self.sel.get(key, 0) + d))
            self.detail_sel = 0

    def toggle_pin(self, e: Entry | None) -> None:
        if not e:
            return
        if self.is_pinned(e):
            self.pins = [p for p in self.pins if p.id != e.id]
            self.status(f"unpinned {e.name}")
        else:
            self.pins.append(e)
            self.status(f"pinned {e.name}" + (" (see it under pinned songs)" if e.kind == "track" and not self.songs else ""))
        library.save_pins(self.pins)
        self._split_pins()

    async def handle_key(self, event: events.Key) -> bool:
        k, ch = event.key, event.character or ""
        view = self.query_one(SpinView)
        full = mode_for(view.size.width, view.size.height) == "full"

        if self.show_help:
            self.show_help = False
            return True
        if ch == "q":
            self.exit()
        elif ch == "?":
            self.show_help = True
        elif k in ("j", "down"):
            self.move(1)
        elif k in ("k", "up"):
            self.move(-1)
        elif ch == "g":
            self.move(-10_000)
        elif ch == "G":
            self.move(10_000)
        elif k in ("pagedown", "ctrl+d"):
            self.move(10)
        elif k in ("pageup", "ctrl+u"):
            self.move(-10)
        elif k == "tab":
            if full:
                self.focus = {"nav": "list", "list": "detail", "detail": "nav"}[self.focus]
            else:
                self.switch_section(1)
        elif k == "shift+tab":
            if full:
                self.focus = {"nav": "detail", "list": "nav", "detail": "list"}[self.focus]
            else:
                self.switch_section(-1)
        elif ch == "]":
            self.switch_section(1)
        elif ch == "[":
            self.switch_section(-1)
        elif k in ("l", "right"):
            if full:
                self.focus = {"nav": "list", "list": "detail", "detail": "detail"}[self.focus]
            elif self.selected():
                self.detail_open, self.detail_sel = True, 0
        elif k in ("h", "left", "escape"):
            if full:
                self.focus = {"nav": "nav", "list": "nav", "detail": "list"}[self.focus]
            else:
                self.detail_open = False
        elif k == "enter":
            e = self.selected()
            now_tab = self.section_key == "now"
            if self.focus == "nav" and full:
                self.focus = "list"
            elif now_tab and (self.focus == "detail" or self.detail_open):
                up = self.queue_tracks or []
                if up:
                    self.play(up[min(self.detail_sel, len(up) - 1)])
            elif now_tab and e and self.ctx_entry:
                self.play(self.ctx_entry, e)
            elif e and e.kind != "track" and (self.focus == "detail" or (self.detail_open and not full)):
                tracks = self.detail_tracks() or []
                if tracks:
                    self.play(e, tracks[min(self.detail_sel, len(tracks) - 1)])
            elif e:
                self.play_row(min(self.sel.get(self.list_key, 0), len(self.current_list()) - 1))
        elif ch.isdigit() and ch != "0":
            i = int(ch) - 1
            if i < len(self.current_list()):
                self.sel[self.list_key] = i
                self.play_row(i)
        elif ch == "s":
            self.songs = not self.songs
            self.cfg["view"] = "songs" if self.songs else "albums"
            save_config(self.cfg)
            self.detail_open, self.detail_sel = False, 0
            self.load_section(self.section_key)
            self.status(f"showing {'songs' if self.songs else 'albums'} in pinned, recent, saved and top", 3)
        elif k == "space":
            self.player_cmd("play-pause")
        elif ch == "n":
            self.player_cmd("next")
        elif ch == "b":
            self.player_cmd("previous")
        elif ch in ("+", "="):
            self.change_volume(0.05)
        elif ch in ("-", "_"):
            self.change_volume(-0.05)
        elif ch == "p":
            self.toggle_pin(self.ctx_entry if self.section_key == "now" else self.selected())
        elif ch == "c":
            self.section, self.focus, self.detail_open, self.detail_sel = 0, "list", False, 0
            self.select_current()
            if self.ctx_entry is None:
                self.refresh_context()
        elif ch == "t":
            self.theme_name = THEME_NAMES[(THEME_NAMES.index(self.theme_name) + 1) % len(THEME_NAMES)] if self.theme_name in THEME_NAMES else THEME_NAMES[0]
            self.pal = theme_palette(self.theme_name)
            self.screen.styles.background = self.pal.bg
            self.cfg["theme"] = self.theme_name
            save_config(self.cfg)
            self.status(f"theme: {self.theme_name}", 2)
        elif ch == "v":
            self.viz_mode = MODES[(MODES.index(self.viz_mode) + 1) % len(MODES)]
            self.cfg["visualizer"] = self.viz_mode
            save_config(self.cfg)
            self.status(f"visualizer: {self.viz_mode}", 2)
        elif ch == "a":
            e = self.selected()
            tracks = self.detail_tracks() if e else None
            if e and e.kind == "track":
                self.queue(e)
            elif tracks and (self.focus == "detail" or self.detail_open):
                self.queue(tracks[min(self.detail_sel, len(tracks) - 1)])
            else:
                self.status("open an album's tracks to queue one")
        elif ch == "/":
            self.push_screen(SearchScreen(), self.run_search)
        elif ch == "r":
            if self.section_key == "pinned":
                self.pins = library.load_pins()
                self._split_pins()
            else:
                self.load_section(self.section_key, force=True)
        else:
            return False
        view.redraw()
        return True
