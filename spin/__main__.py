"""Entry point: `spin` runs the player, `spin login` signs in again, `spin logout` forgets the tokens."""

from __future__ import annotations

import re
import sys

from . import auth
from .config import AUTH_FILE, CONFIG_FILE, load_config, save_config

SETUP = """spin needs your own Spotify app (free, takes a couple of minutes):

  1. Open https://developer.spotify.com/dashboard and choose Create app.
  2. Give it any name and description.
  3. Add this Redirect URI exactly:  {redirect}
  4. Under "Which API/SDKs", tick Web API, then save.
  5. Copy the app's Client ID from its settings page.
"""


def ensure_client_id(cfg: dict) -> bool:
    """Ask for a Client ID on first run and store it in config.json."""
    if re.fullmatch(r"[0-9a-f]{32}", cfg.get("client_id", "")):
        return True
    print(SETUP.format(redirect=f"http://127.0.0.1:{cfg['redirect_port']}{cfg['redirect_path']}"))
    if not sys.stdin.isatty():
        print(f"Then put it in {CONFIG_FILE} as \"client_id\".", file=sys.stderr)
        return False
    while True:
        cid = input("Client ID: ").strip().lower()
        if re.fullmatch(r"[0-9a-f]{32}", cid):
            break
        print("That doesn't look like a Client ID (32 letters and digits). Try again, or Ctrl+C to quit.")
    cfg["client_id"] = cid
    save_config(cfg)
    print(f"Saved to {CONFIG_FILE}.\n")
    return True


def main() -> int:
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if cmd == "logout":
        AUTH_FILE.unlink(missing_ok=True)
        print("Signed out.")
        return 0
    try:
        if not ensure_client_id(cfg):
            return 1
    except (KeyboardInterrupt, EOFError):
        print()
        return 1
    tokens = None if cmd == "login" else auth.load()
    if tokens is None:
        try:
            tokens = auth.login(cfg)
        except auth.AuthError as e:
            print(f"Sign-in failed: {e}", file=sys.stderr)
            return 1
        print("Signed in.")
        if cmd == "login":
            return 0

    from .app import SpinApp

    SpinApp(cfg, tokens).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
