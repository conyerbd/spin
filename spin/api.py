"""A small Spotify Web API client: token refresh, disk cache, and polite rate-limit handling."""

from __future__ import annotations

import hashlib
import json
import time

import httpx

from . import auth
from .config import CACHE_DIR

API = "https://api.spotify.com/v1"


class RateLimited(Exception):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited for {int(retry_after)}s")
        self.retry_after = retry_after


class ApiError(Exception):
    pass


class Spotify:
    def __init__(self, cfg: dict, tokens: dict):
        self.cfg = cfg
        self.tokens = tokens
        self.client = httpx.AsyncClient(timeout=15)
        self.blocked_until = 0.0
        (CACHE_DIR / "api").mkdir(parents=True, exist_ok=True)

    async def close(self) -> None:
        await self.client.aclose()

    async def _token(self) -> str:
        if time.time() >= self.tokens.get("expires_at", 0):
            self.tokens = await auth.refresh(self.cfg, self.tokens, self.client)
        return self.tokens["access_token"]

    def _cache_path(self, key: str):
        return CACHE_DIR / "api" / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    async def request(self, method: str, path: str, *, params=None, json_body=None, ttl: float = 0, force=False):
        key = path + ("?" + json.dumps(params, sort_keys=True) if params else "")
        cp = self._cache_path(key)
        entry = None
        if method == "GET" and ttl:
            try:
                entry = json.loads(cp.read_text())
            except (OSError, ValueError):
                entry = None
            if entry and not force and time.time() - entry.get("at", 0) < ttl:
                return entry["data"]
        if time.time() < self.blocked_until:
            # Rate limited: stale data beats an empty screen.
            if entry:
                return entry["data"]
            raise RateLimited(self.blocked_until - time.time())

        for _ in range(2):
            headers = {"Authorization": f"Bearer {await self._token()}"}
            r = await self.client.request(method, API + path, params=params, json=json_body, headers=headers)
            if r.status_code == 401:
                self.tokens["expires_at"] = 0
                continue
            break
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", "30") or 30)
            self.blocked_until = time.time() + wait
            if entry:
                return entry["data"]
            raise RateLimited(wait)
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:120])
            except ValueError:
                msg = r.text[:120]
            raise ApiError(f"{method} {path}: HTTP {r.status_code} {msg}")
        data = r.json() if r.content else {}
        if method == "GET" and ttl:
            cp.write_text(json.dumps({"at": time.time(), "data": data}))
        return data

    async def get(self, path, params=None, ttl=0, force=False):
        return await self.request("GET", path, params=params, ttl=ttl, force=force)

    async def paged(self, path, params=None, ttl=0, force=False, limit=200):
        """Follow `next` links until `limit` items."""
        params = dict(params or {})
        params.setdefault("limit", 50)
        items, offset = [], 0
        while len(items) < limit:
            params["offset"] = offset
            page = await self.get(path, params=dict(params), ttl=ttl, force=force)
            batch = page.get("items", [])
            items += batch
            if not page.get("next") or not batch:
                break
            offset += len(batch)
        return items[:limit]

    # ---- endpoints -------------------------------------------------------

    async def recently_played(self, force=False):
        return (await self.get("/me/player/recently-played", {"limit": 50}, ttl=90, force=force)).get("items", [])

    async def saved_albums(self, force=False):
        return await self.paged("/me/albums", ttl=6 * 3600, force=force)

    async def saved_tracks(self, force=False):
        return await self.paged("/me/tracks", ttl=6 * 3600, force=force)

    async def top_tracks(self, force=False):
        return (await self.get("/me/top/tracks", {"time_range": "short_term", "limit": 50}, ttl=6 * 3600, force=force)).get("items", [])

    async def playlists(self, force=False):
        return await self.paged("/me/playlists", ttl=6 * 3600, force=force)

    async def album_tracks(self, album_id):
        return await self.paged(f"/albums/{album_id}/tracks", ttl=7 * 86400)

    async def playlist_tracks(self, playlist_id):
        try:
            items = await self.paged(f"/playlists/{playlist_id}/items", ttl=3600, limit=100)
        except ApiError:
            items = await self.paged(f"/playlists/{playlist_id}/tracks", ttl=3600, limit=100)
        return [it.get("item") or it.get("track") for it in items if (it.get("item") or it.get("track"))]

    async def search(self, q):
        return await self.get("/search", {"q": q, "type": "album,track", "limit": 10}, ttl=600)

    async def devices(self):
        return (await self.get("/me/player/devices")).get("devices", [])

    async def play(self, device_id, context_uri, offset_uri=None):
        body = {"context_uri": context_uri}
        if offset_uri:
            body["offset"] = {"uri": offset_uri}
        params = {"device_id": device_id} if device_id else None
        await self.request("PUT", "/me/player/play", params=params, json_body=body)

    async def play_uris(self, device_id, uris):
        """Play a list of tracks in order (the selected song, then the ones after it)."""
        params = {"device_id": device_id} if device_id else None
        await self.request("PUT", "/me/player/play", params=params, json_body={"uris": uris})

    async def queue(self, uri, device_id=None):
        params = {"uri": uri}
        if device_id:
            params["device_id"] = device_id
        await self.request("POST", "/me/player/queue", params=params)
