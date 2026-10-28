# spin

A terminal remote for the Spotify desktop app. It shows your recently played
albums (grouped from your last 50 plays), saved albums, top albums, playlists
and search, and plays them on the desktop app through Spotify Connect. The
now-playing panel has half-block cover art, a spectrum visualizer, a real
waveform scope and L/R level meters. The layout adapts from full screen down to
a quarter tile.

Built on [Omarchy](https://omarchy.org) (Hyprland), where it picks up your
desktop theme's colours, but it runs on any Linux desktop with PipeWire.

## Requirements

- Linux with PipeWire (`pw-record` feeds the scope and meters)
- Python 3.11 or newer
- The Spotify desktop app, running, on a **Premium** account (Spotify only
  allows playback control through its API for Premium)
- [`playerctl`](https://github.com/altdesktop/playerctl), for now playing and
  transport controls
- [`cava`](https://github.com/karlstav/cava) (recommended), for the spectrum
  visualizers; without it they fall back to an animation
- A terminal with truecolor and a font that has box-drawing, block and braille
  characters (most Nerd Fonts and JetBrains Mono do)

On Arch: `sudo pacman -S playerctl cava`

## Install

    pipx install git+https://github.com/conyerbd/spin

or from a clone:

    python3 -m venv .venv && .venv/bin/pip install .

## Setup

spin talks to Spotify through your own developer app, so nobody shares a
client ID or a rate limit:

1. Open <https://developer.spotify.com/dashboard> and choose **Create app**.
2. Give it any name and description.
3. Add the Redirect URI `http://127.0.0.1:19872/login` exactly.
4. Under "Which API/SDKs", tick **Web API**, then save.
5. Run `spin` and paste the app's **Client ID** when asked. It is stored in
   `~/.config/spin/config.json`.

New apps start in Development mode, which only lets accounts listed under the
app's **User Management** sign in; the account that created the app is fine.

## Run

    spin            # asks for your Client ID and signs in on first run
    spin login      # sign in again
    spin logout     # forget the stored tokens

Start the Spotify desktop app first; spin plays through it.

## Keys

`j/k` move · `⏎` play · `1–9` play that row · `l/h` open/close tracks (pane
focus on full screen) · `tab` next pane or section · `[ ]` section ·
`space` play/pause · `n/b` next/previous · `+/-` volume · `a` queue track ·
`p` pin album · `c` jump to now playing · `v` visualizer · `t` theme ·
`/` search · `r` refresh · `?` help · `q` quit

**Visualizers:** bars, peaks and mirror show cava's frequency spectrum; scope
draws the real waveform (triggered like an oscilloscope) and vector is a stereo
goniometer, both from raw audio captured with `pw-record`, which also drives the
L/R meters.

**Themes:** omarchy (follows your Omarchy theme, or a warm default elsewhere),
synthwave, phosphor, amber, ice, mono.

Both choices are remembered in `config.json`. The screen redraws at 60 fps; add
`"fps": 30` there to halve the CPU it uses.

## Troubleshooting

**"Spotify crashed" notifications while music keeps playing.** That is Spotify's
GPU helper process crashing and restarting, not spin. On Hyprland, Spotify runs
through XWayland by default, but its graphics setup picks Wayland, and Mesa
crashes on the mismatch (SIGSEGV in `libEGL_mesa` → `wl_proxy_create_wrapper`,
seen in `coredumpctl`). Run Spotify natively on Wayland instead by creating
`~/.config/spotify-flags.conf`, which the Arch `/usr/bin/spotify` launcher reads
on every start:

    --enable-features=UseOzonePlatform
    --ozone-platform=wayland

Quit Spotify fully and reopen it, then check that `hyprctl clients` shows
`xwayland: 0` for its window. If Spotify misbehaves on Wayland, use
`--disable-gpu` in that file instead.

**Why not play audio in the terminal?** spin is a remote for the desktop app on
purpose. In testing, librespot-based players (spotifyd, cliamp) signed in fine
but Spotify refused them the audio decryption keys (`error audio key 0 1`), so
every track failed.

## Files

- `~/.config/spin/config.json` – your Client ID, theme, visualizer, fps
- `~/.config/spin/auth.json` – Spotify tokens (mode 600)
- `~/.config/spin/pins.json` – pinned albums
- `~/.cache/spin/` – API responses and cover art

## Development

    python3 -m venv .venv && .venv/bin/pip install -e .

## License

MIT
