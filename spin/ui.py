"""Draws the whole screen into a character grid.

One adaptive layout, picked from the terminal size:
  full     ≥120 cols, ≥34 rows   library | list | album, now-playing deck below
  wide     ≥120 cols, <34 rows   list | now playing
  tall     <120 cols, ≥34 rows   now playing over list
  quarter  <120 cols, <34 rows   compact now playing over a one-line list
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from .grid import Grid, cut

if TYPE_CHECKING:
    from .app import SpinApp

LOGO = "[ s p i n ]"
CREDIT = "via Spotify"
SPIN = ["▁▃▅", "▃▅▇", "▅▇▅", "▇▅▃", "▅▃▁", "▃▁▃"]

HELP = [
    ("j/k ↑/↓", "move"),
    ("⏎", "play album / track"),
    ("1–9", "play that row"),
    ("l/→  h/←", "open / close tracks (pane focus on full screen)"),
    ("tab", "next pane (full) or section"),
    ("[  ]", "previous / next section"),
    ("space", "play / pause"),
    ("n  b", "next / previous track"),
    ("+  -", "volume"),
    ("a", "add track to queue"),
    ("p", "pin / unpin album"),
    ("c", "jump to now playing"),
    ("s", "albums ⇄ songs (pinned, recent, saved, top)"),
    ("o", "open in the Spotify app"),
    ("i", "cover art on / off"),
    ("v", "change visualizer"),
    ("t", "change theme"),
    ("/", "search"),
    ("r", "refresh this section"),
    ("?", "this help"),
    ("q", "quit"),
]


def clock(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 60}:{s % 60:02d}"


def mode_for(w: int, h: int) -> str:
    if w >= 120:
        return "full" if h >= 34 else "wide"
    return "tall" if h >= 34 else "quarter"


def draw(app: "SpinApp", W: int, H: int) -> Grid:
    P = app.pal
    g = Grid(W, H, P.fg)
    if W < 40 or H < 12:
        g.put(0, 0, cut("spin needs at least 40×12", W), P.dim)
        return g

    t = time.monotonic()
    now = app.now
    C3 = (P.green, P.accent, P.red)
    bars = app.levels.frame(now.playing)
    level = sum(bars) / len(bars)
    half = len(bars) // 2
    gain = 1.8 if app.levels.real else 1.1
    vu_l = min(1.0, sum(bars[:half]) / half * gain) if now.playing else 0.0
    vu_r = min(1.0, sum(bars[half:]) / half * gain * 1.3) if now.playing else 0.0
    pcm = app.pcm
    if pcm.live:  # real per-channel levels from the raw audio
        vu_l, vu_r = pcm.levels()
    spin = SPIN[int(t * 8) % 6] if now.playing else "▮▮"
    mode = mode_for(W, H)
    peaks = app.levels.peaks(bars)

    def visual(x, y, w, h):
        m = app.viz_mode
        if m == "peaks":
            g.spectrum(x, y, w, h, bars, C3, peaks, P.fg)
        elif m == "mirror":
            g.mirror(x, y, w, h, bars, C3, P.line)
        elif m == "scope":
            if pcm.live:
                g.trace(x, y, w, h, pcm.scope(max(480, min(1200, w * 8)), w * 2), C3)
            else:
                g.scope(x, y, w, h, t, bars, C3)
        elif m == "vector":
            if pcm.live:
                g.vector(x, y, w, h, pcm.stereo(1024), C3)
            else:
                g.spectrum(x, y, w, h, bars, C3)
        else:
            g.spectrum(x, y, w, h, bars, C3)

    if not now.available:
        np_title, np_sub = "Spotify isn't running", "start the desktop app, then pick an album"
    elif not now.title:
        np_title, np_sub = "Nothing playing", "pick an album and press ⏎"
    else:
        np_title, np_sub = now.title, f"{now.artist} — {now.album}"
    frac = (now.position / now.length) if now.length else 0.0
    times = f"{clock(now.position)} / {clock(now.length)}" if now.length else ""

    def no_art(x, y, w, h):
        """Stand-in when cover art is turned off (Spotify's guidelines allow omitting it)."""
        if h >= 3 and w >= 6:
            g.box(x, y, w, h, P.line)
            g.put(x + w // 2, y + h // 2, "♪", P.dim)
        elif w >= 2:
            g.put(x + w // 2 - 1, y, "♪", P.dim)

    def cover(x, y, w, h):
        if not app.show_art:
            return no_art(x, y, w, h)
        app.want_art(now.art_url)
        g.art(x, y, app.art.pixels(now.art_url, now.album or "spin", w, h * 2))

    def entry_art(e, x, y, w, h):
        if not app.show_art:
            return no_art(x, y, w, h)
        url = e.image if w <= 8 else e.image_big
        app.want_art(url)
        g.art(x, y, app.art.pixels(url, e.id or e.name, w, h * 2))

    def controls(x, y, compact=False):
        # Plain text glyphs: the ⏮/⏸/⏭ code points render as colour emoji in many fonts.
        icon = "❚❚" if now.playing else "▶ "
        if compact:
            g.put(x, y, "|◀", P.fg)
            g.put(x + 3, y, f" {icon} ", P.bg, P.accent, True)
            g.put(x + 8, y, "▶|", P.fg)
            return x + 11
        g.put(x, y, "|◀", P.fg)
        g.put(x + 4, y, f" {icon} space ", P.bg, P.accent, True)
        g.put(x + 16, y, "▶|", P.fg)
        return x + 20

    def hints(y, items):
        # Spotify's terms ask for content to be credited to Spotify.
        g.put(W - 1 - len(CREDIT), y, CREDIT, P.dim)
        room = W - len(CREDIT) - 3
        if app.status_text():
            g.put(1, y, cut(app.status_text(), room - 1), P.accent, bold=True)
            return
        x = 1
        for k, label in items:
            if x + len(k) + 1 + len(label) > room:
                break
            x = g.put(x, y, k, P.fg, bold=True) + 1
            x = g.put(x, y, label, P.dim) + 3

    def tabs():
        x = g.put(1, 0, LOGO, P.green, bold=True) + 2
        for i, (key, label) in enumerate(app.SECTIONS):
            label = app.section_label(key, label)
            short = label.split(" ")[0] if W < 100 else label.split(" · ")[0]
            s = f" {short} "
            if x + len(s) > W - 10:
                break
            if i == app.section:
                g.put(x, 0, s, P.bg, P.accent, True)
            else:
                g.put(x, 0, s, P.dim)
            x += len(s) + (1 if W >= 100 else 0)
        vol = f"vol {round(now.volume * 100)}%" if now.available else ""
        if vol and W - x > len(vol) + 2:
            g.put(W - 1 - len(vol), 0, vol, P.dim)

    def list_rows(x, y, w, h, two_line, focused):
        """The current section's entries (or the open album's tracks)."""
        if app.detail_open and mode != "full":
            return track_rows(x, y, w, h, focused=True)
        entries = app.current_list()
        key, lk = app.section_key, app.list_key
        per = 3 if two_line else 1

        def fits(rows):
            return max(1, (rows + (1 if two_line else 0)) // per)

        cap = fits(h)
        overflow = len(entries) > cap
        if overflow:  # keep the last row for the "n/total" counter
            cap = fits(h - 1)
        if not entries:
            msg = app.list_message()
            g.put(x + 2, y, cut(msg, w - 4), P.dim)
            return
        if key == "now":
            ctx = app.ctx_entry
            track_list(x, y, w, h, entries, app.sel.get("now", 0), focused, "now", bool(ctx and ctx.kind == "playlist"))
            return
        sel = app.sel.get(lk, 0)
        top = app.scroll_to(lk, sel, cap)
        for vi, i in enumerate(range(top, min(len(entries), top + cap))):
            e, yy, is_sel = entries[i], y + vi * per, i == sel
            if is_sel:
                g.fill(x, yy, w, 2 if two_line else 1, P.sel)
                if focused:
                    for r in range(2 if two_line else 1):
                        g.put(x, yy + r, "▌", P.accent)
            num = str(i + 1) if i < 9 else " "
            g.put(x + 1, yy, num, P.accent, bold=True)
            plays = e.plays and key in ("recent", "top")
            if two_line:
                entry_art(e, x + 3, yy, 4, 2)
                right = 17 if plays else 0
                g.put(x + 9, yy, cut(e.name, w - 11 - right), P.accent if is_sel and focused else P.fg, bold=True)
                if e.kind != "track":
                    sub = e.sub
                elif key == "search":
                    sub = f"{e.sub} · track"  # search mixes albums and tracks
                else:
                    sub = f"{e.sub} · {e.album}" if e.album else e.sub
                g.put(x + 9, yy + 1, cut(sub, w - 11 - right), P.dim)
                if e.kind == "track" and e.duration_ms and not plays:
                    dur = clock(e.duration_ms / 1000)
                    g.put(x + w - 1 - len(dur), yy, dur, P.dim)
                if plays:
                    top_plays = max(en.plays for en in entries) or 1
                    g.put(x + w - 17, yy, "▇" * max(1, round(e.plays / top_plays * 14)), P.accent if is_sel else P.dim)
                    label = f"{e.plays} play{'s' if e.plays != 1 else ''}" + (" · album" if e.note else "")
                    g.put(x + w - 17, yy + 1, cut(label, 16), P.dim)
            else:
                entry_art(e, x + 3, yy, 2, 1)
                tw = int((w - 12) * 0.55)
                g.put(x + 6, yy, cut(e.name, tw), P.accent if is_sel and focused else P.fg, bold=True)
                g.put(x + 7 + tw, yy, cut(e.sub, w - 12 - tw), P.dim)
                if plays:
                    p = str(e.plays)
                    g.put(x + w - 1 - len(p), yy, p, P.dim)
        if overflow:
            g.put(x + w - 8, y + h - 1, f"{sel + 1}/{len(entries)}".rjust(7), P.dim)

    def is_current(tr):
        return bool(now.title) and ((bool(tr.id) and now.url.endswith(tr.id)) or tr.name == now.title)

    def track_list(x, y, w, h, tracks, sel, focused, scroll_key, show_artist):
        top = app.scroll_to(scroll_key, sel, h)
        for vi, i in enumerate(range(top, min(len(tracks), top + h))):
            tr, yy = tracks[i], y + vi
            on = is_current(tr)
            if i == sel and focused:
                g.fill(x, yy, w, 1, P.sel)
                g.put(x, yy, "▌", P.accent)
            g.put(x + 1, yy, "▶" if on else " ", P.accent)
            g.put(x + 3, yy, f"{i + 1:02d}", P.dim)
            dur = clock(tr.duration_ms / 1000) if tr.duration_ms else ""
            name_w = w - 15
            x2 = g.put(x + 7, yy, cut(tr.name, name_w), P.accent if on else P.fg, bold=on)
            if show_artist and x2 + 3 < x + 7 + name_w:
                g.put(x2 + 2, yy, cut(tr.sub, x + 7 + name_w - x2 - 2), P.dim)
            if on and now.playing:
                g.put(x + w - 11, yy, spin, P.accent)
            g.put(x + w - 1 - len(dur), yy, dur, P.dim)

    def track_rows(x, y, w, h, focused):
        e = app.detail_entry()
        if e is None:
            return
        tracks = app.detail_tracks()
        if tracks is None:
            g.put(x + 2, y, "loading tracks…", P.dim)
            return
        if not tracks:
            g.put(x + 2, y, "nothing queued" if app.section_key == "now" else "no tracks", P.dim)
            return
        track_list(x, y, w, h, tracks, app.detail_sel, focused, "__detail",
                   e.kind == "playlist" or app.section_key == "now")

    # ---------------------------------------------------------------- full
    if mode == "full":
        x = g.put(1, 0, LOGO, P.green, bold=True) + 2
        g.put(x, 0, "spotify desktop" if now.available else "spotify not running", P.dim)
        right = f"vol {round(now.volume * 100)}%" if now.available else ""
        if app.rate_limited():
            right = "rate limited · showing cached data   " + right
        g.put(W - 1 - len(right), 0, right, P.dim)

        nav_w, det_w = 24, max(48, int(W * 0.36))
        list_w, pane_h = W - nav_w - det_w, H - 17

        g.box(0, 1, nav_w, pane_h, P.accent if app.focus == "nav" else P.line, "library", P.accent if app.focus == "nav" else P.dim)
        for i, (key, label) in enumerate(app.SECTIONS):
            y = 3 + i * 2
            label = app.section_label(key, label)
            n = len(app.lists.get(app.key_for(key)) or [])
            count = str(n) if n else ""
            if len(label) > nav_w - 5 - len(count):
                label = label.replace("last month", "month")
            if i == app.section:
                g.fill(1, y, nav_w - 2, 1, P.accent if app.focus == "nav" else P.sel)
                c = P.bg if app.focus == "nav" else P.accent
                g.put(2, y, cut(label, nav_w - 5 - len(count)), c, bold=True)
                g.put(nav_w - 2 - len(count), y, count, c, bold=True)
            else:
                g.put(2, y, cut(label, nav_w - 5 - len(count)), P.fg)
                g.put(nav_w - 2 - len(count), y, count, P.dim)
        g.put(2, pane_h - 1, "[ ]  section", P.dim)

        list_focus = app.focus == "list"
        g.box(nav_w, 1, list_w, pane_h, P.accent if list_focus else P.line, app.section_title(), P.accent if list_focus else P.dim)
        list_rows(nav_w + 1, 3, list_w - 2, pane_h - 4, True, list_focus)

        det_focus = app.focus == "detail"
        dx0 = nav_w + list_w
        e = app.detail_entry()
        det_title = "up next" if app.section_key == "now" else (e.kind if e else "album")
        g.box(dx0, 1, det_w, pane_h, P.accent if det_focus else P.line, det_title, P.accent if det_focus else P.dim)
        if e:
            dx = dx0 + 2
            entry_art(e, dx, 3, 22, 11)
            info_w = det_w - 28
            g.put(dx + 24, 3, cut(e.name, info_w), P.fg, bold=True)
            g.put(dx + 24, 4, cut(e.sub, info_w), P.dim)
            tracks = app.detail_tracks()
            if e.kind == "track":
                g.put(dx + 24, 5, cut(e.album, info_w), P.dim)
                if e.duration_ms:
                    g.put(dx + 24, 6, clock(e.duration_ms / 1000), P.dim)
            elif tracks is not None:
                g.put(dx + 24, 5, f"{len(tracks)} up next" if app.section_key == "now" else f"{len(tracks)} tracks", P.dim)
            g.put(dx + 24, 8, " ⏎ play ", P.bg, P.accent, True)
            g.put(dx + 33, 8, " p pin " if not app.is_pinned(e) else " p unpin ", P.fg, P.sel)
            if e.kind == "track":
                g.put(dx + 24, 10, " a queue ", P.fg, P.sel)
            track_rows(dx0 + 1, 15, det_w - 2, pane_h - 15, det_focus)

        dy = H - 16
        g.box(0, dy, W, 14, P.line, "now playing", P.dim)
        spec_w = min(60, int(W * 0.34) // 3 * 3)
        visual(2, dy + 2, spec_w, 10)
        rx = spec_w + 6
        rw = W - rx - 3
        cw = 20
        cover(rx, dy + 2, cw, 10)
        ix, iw = rx + cw + 3, rw - cw - 3
        x = g.put(ix, dy + 2, cut(np_title, iw - 5), P.fg, bold=True)
        g.put(x + 2, dy + 2, spin, P.accent)
        g.put(ix, dy + 3, cut(np_sub, iw), P.dim)
        g.put(ix, dy + 5, times, P.dim)
        g.progress(ix + len(times) + 2, dy + 5, iw - len(times) - 2, frac, P.accent, P.line)
        controls(ix, dy + 7)
        g.meter(ix, dy + 9, iw - 3, vu_l, "L", P.dim, P.line, C3)
        g.meter(ix, dy + 10, iw - 3, vu_r, "R", P.dim, P.line, C3)
        if pcm.live:
            g.trace(ix, dy + 11, iw, 1, pcm.scope(720, iw * 2), (P.line, P.dim, P.dim))
        else:
            g.wave(ix, dy + 11, iw, 1, t, level, P.line)
        hints(H - 2, [("j/k", "move"), ("⏎", "play"), ("1–9", "play row"), ("tab", "pane"), ("[ ]", "section"),
                      ("space", "play/pause"), ("n/b", "next/prev"), ("c", "now playing"), ("s", "albums/songs"), ("/", "search"), ("p", "pin"),
                      ("v", "visual"), ("t", "theme"), ("?", "keys"), ("q", "quit")])

    # ---------------------------------------------------------------- wide
    elif mode == "wide":
        tabs()
        lw = int(W * 0.48)
        title = app.list_title()
        g.box(0, 1, lw, H - 2, P.accent, title, P.accent)
        list_rows(1, 2, lw - 2, H - 4, False, True)
        g.box(lw, 1, W - lw, H - 2, P.line, "now playing", P.dim)
        rx = lw + 2
        ch = max(3, min(8, H - 14))
        cover(rx, 2, ch * 2, ch)
        ix, iw = rx + ch * 2 + 2, W - (rx + ch * 2 + 2) - 3
        x = g.put(ix, 2, cut(np_title, iw - 5), P.fg, bold=True)
        g.put(x + 2, 2, spin, P.accent)
        g.put(ix, 3, cut(np_sub, iw), P.dim)
        g.put(ix, 5, times, P.dim)
        g.progress(ix + len(times) + 2, 5, iw - len(times) - 2, frac, P.accent, P.line)
        controls(ix, 7)
        if ch >= 8:
            g.meter(ix, 9, iw - 3, vu_l, "L", P.dim, P.line, C3)
            g.meter(ix, 10, iw - 3, vu_r, "R", P.dim, P.line, C3)
        sy = 3 + ch
        if H - 3 - sy > 0:
            visual(rx, sy, W - lw - 4, H - 3 - sy)
        hints(H - 1, [("⏎", "play"), ("1–9", "play row"), ("l/h", "tracks"), ("tab", "section"), ("space", "play/pause"),
                      ("n/b", "next/prev"), ("c", "now"), ("/", "search"), ("?", "keys")])

    # ---------------------------------------------------------------- tall
    elif mode == "tall":
        tabs()
        g.box(0, 1, W, 17, P.line, "now playing", P.dim)
        cover(2, 2, 20, 10)
        ix, iw = 24, W - 27
        x = g.put(ix, 2, cut(np_title, iw - 5), P.fg, bold=True)
        g.put(x + 2, 2, spin, P.accent)
        g.put(ix, 3, cut(now.artist or np_sub, iw), P.dim)
        g.put(ix, 4, cut(now.album, iw), P.dim)
        g.put(ix, 6, times, P.dim)
        g.progress(ix + len(times) + 2, 6, iw - len(times) - 2, frac, P.accent, P.line)
        controls(ix, 8)
        g.meter(ix, 10, iw - 3, vu_l, "L", P.dim, P.line, C3)
        g.meter(ix, 11, iw - 3, vu_r, "R", P.dim, P.line, C3)
        visual(2, 12, W - 4, 5)
        title = app.list_title()
        g.box(0, 18, W, H - 20, P.accent, title, P.accent)
        list_rows(1, 20, W - 2, H - 23, not app.detail_open, True)
        hints(H - 1, [("⏎", "play"), ("1–9", "row"), ("c", "now"), ("l/h", "tracks"), ("tab", "section"), ("space", "play/pause"), ("?", "keys")])

    # ---------------------------------------------------------------- quarter
    else:
        tabs()
        cover(1, 1, 8, 4)
        ix, iw = 11, W - 12
        x = g.put(ix, 1, cut(np_title, iw - 5), P.fg, bold=True)
        g.put(x + 2, 1, spin, P.accent)
        g.put(ix, 2, cut(np_sub, iw), P.dim)
        g.put(ix, 3, times, P.dim)
        g.progress(ix + len(times) + 2, 3, iw - len(times) - 2, frac, P.accent, P.line)
        x = controls(ix, 4, compact=True)
        if now.available:
            g.put(x + 2, 4, f"vol {round(now.volume * 100)}%", P.dim)
        visual(1, 5, W - 2, 2)
        g.put(0, 7, "─" * W, P.line)
        title = app.list_title()
        g.put(2, 7, " " + cut(title, W - 6) + " ", P.dim, bold=True)
        list_rows(0, 8, W, H - 9, False, True)
        hints(H - 1, [("⏎", "play"), ("c", "now"), ("l/h", "tracks"), ("tab", "section"), ("?", "keys")])

    if app.show_help:
        bw, bh = min(W - 4, 62), min(H - 2, len(HELP) + 4)
        bx, by = (W - bw) // 2, (H - bh) // 2
        g.fill(bx, by, bw, bh, P.bg)
        for yy in range(by, by + bh):
            g.put(bx, yy, " " * bw, P.fg, P.bg)
        g.box(bx, by, bw, bh, P.accent, "keys", P.accent)
        for i, (k, label) in enumerate(HELP[: bh - 4]):
            g.put(bx + 3, by + 2 + i, k, P.fg, bold=True)
            g.put(bx + 16, by + 2 + i, cut(label, bw - 19), P.dim)
    return g
