"""
streaming.py – HTTP proxy handlers for streaming audio sources.

Supported backends
──────────────────
• PandoraSource   – pianobar-webui (or compatible) running on a local host
• PlexSource      – Plex Media Server + PlexAmp via the Plex HTTP API
• StreamingSource – base / fallback (no-ops for unknown streaming types)
"""

import logging
import threading
import time
import requests

logger = logging.getLogger(__name__)

_TIMEOUT = 5  # seconds for all outbound HTTP requests


class StreamingSource:
    """Base class – override methods for each streaming backend."""

    def __init__(self, source_config: dict):
        self.source_id: int = source_config["source"]
        self.name: str = source_config.get("name", "")
        self.url: str = source_config.get("url", "").rstrip("/")
        self._available: bool = True   # flipped to False on any connection error

    def _get(self, path: str, **kwargs):
        try:
            r = requests.get(f"{self.url}{path}", timeout=_TIMEOUT, **kwargs)
            r.raise_for_status()
            self._available = True
            return r.json()
        except Exception as exc:
            # logger.warning("%s GET %s – %s", self.name, path, exc)
            self._available = False
            return None

    def _post(self, path: str, data: dict | None = None, **kwargs):
        try:
            r = requests.post(
                f"{self.url}{path}", json=data, timeout=_TIMEOUT, **kwargs
            )
            r.raise_for_status()
            self._available = True
            try:
                return r.json()
            except Exception:
                return {"ok": True}
        except Exception as exc:
            # logger.warning("%s POST %s – %s", self.name, path, exc)
            self._available = False
            return None

    def get_status(self) -> dict:
        return {"available": False}

    def play(self):
        pass

    def pause(self):
        pass

    def next_track(self):
        pass

    def prev_track(self):
        pass

    def get_playlists(self) -> list[dict]:
        return []

    def set_playlist(self, playlist_id: str):
        pass

    def test_connection(self) -> dict:
        return {"ok": False, "error": "No test implemented for this source type"}


# ---------------------------------------------------------------------------
# Pandora / pianobar-webui
# ---------------------------------------------------------------------------

class PandoraSource(StreamingSource):

    def get_status(self) -> dict:
        data = self._get("/status")
        if not data:
            return {"available": False}
        return {
            "available":   True,
            "title":       data.get("title", ""),
            "artist":      data.get("artist", ""),
            "album":       data.get("album", ""),
            "station":     data.get("station", data.get("stationName", "")),
            "playing":     data.get("playing", False),
            "album_art":   data.get("coverArt", data.get("albumArt", "")),
            "duration_ms": int(data.get("songDuration", data.get("duration", 0)) or 0) * 1000,
            "position_ms": int(data.get("songPlayed",   data.get("position",  0)) or 0) * 1000,
        }

    def play(self):
        # /playpause is a toggle — only send it if not already playing,
        # otherwise switching to Pandora while it's playing would stop it.
        status = self.get_status()
        if status.get("available") and not status.get("playing"):
            return self._post("/playpause")

    def pause(self):
        status = self.get_status()
        if status.get("available") and status.get("playing"):
            return self._post("/playpause")

    def next_track(self):
        return self._post("/next")

    def get_playlists(self) -> list[dict]:
        data = self._get("/stations")
        if not data:
            return []
        stations = data.get("stations", data if isinstance(data, list) else [])
        return [
            {
                "id":   str(s.get("id", s.get("stationId", i))),
                "name": s.get("name", s.get("stationName", "")),
            }
            for i, s in enumerate(stations)
        ]

    def set_playlist(self, station_id: str):
        return self._post("/station", {"id": station_id, "stationId": station_id})

    def test_connection(self) -> dict:
        data = self._get("/status")
        if data is None:
            return {"ok": False, "error": f"Could not reach {self.url}/status"}
        return {"ok": True, "url": self.url, "status_keys": list(data.keys())}


# ---------------------------------------------------------------------------
# Plex / PlexAmp
# ---------------------------------------------------------------------------

class PlexSource(StreamingSource):
    """
    Queries playlists and now-playing status from the Plex Media Server.
    Playback control is sent to PlexAmp via its local HTTP player API.

    Session filtering
    -----------------
    /status/sessions returns every active client on the server.  We filter
    to music tracks first, then prefer sessions where the Player is PlexAmp.
    This means the now-playing display stays accurate even if someone else
    is watching a movie on a different Plex client at the same time.

    commandID
    ---------
    The Plex player protocol requires a monotonically increasing commandID
    with every request.  We keep a per-instance counter protected by a lock
    so concurrent calls (e.g. rapid skip button presses) never collide.
    """

    def __init__(self, source_config: dict, plex_config: dict):
        super().__init__(source_config)
        self._cfg        = plex_config
        _ip              = plex_config.get("ip_address", "")
        _port            = int(plex_config.get("port", 32400))
        self._server     = f"http://{_ip}:{_port}"
        self._address    = _ip          # used as the 'address' param in playMedia
        self._port       = str(_port)
        self._machine_id = plex_config.get("machine_identifier", "")
        self._token      = plex_config.get("token", "")
        self._headers    = {
            "X-Plex-Token": self._token,
            "Accept":       "application/json",
        }
        self._cmd_id              = 0
        self._cmd_lock            = threading.Lock()
        # Seed from config so the dropdown pre-selects correctly after a reboot.
        # Fill in "default_playlist_id" in config.json with the Plex ratingKey
        # (visible in the playlist dropdown once the page has loaded at least once).
        default = plex_config.get("default_playlist_id", "")
        self._current_playlist_id: str | None = default if default else None

    # -- low-level helpers --------------------------------------------------

    def _next_cmd_id(self) -> int:
        with self._cmd_lock:
            self._cmd_id += 1
            return self._cmd_id

    def _plex_get(self, path: str) -> dict | None:
        try:
            r = requests.get(
                f"{self._server}{path}",
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("Plex GET %s – %s", path, exc)
            return None

    def _plex_command(self, path: str, extra_params: dict | None = None):
        """
        Send a playback command to PlexAmp.
        commandID is incremented automatically on every call.
        """
        params = {"commandID": self._next_cmd_id()}
        if extra_params:
            params.update(extra_params)
        try:
            r = requests.get(
                f"{self.url}{path}",
                params=params,
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
        except Exception as exc:
            logger.warning("Plex command %s – %s", path, exc)

    # -- now-playing --------------------------------------------------------

    def get_status(self) -> dict:
        """
        Return normalised now-playing info for the PlexAmp session.

        Filtering order:
          1. All sessions → keep only music tracks (type == 'track')
          2. Prefer sessions where Player.product contains 'PlexAmp'
          3. Fall back to the first music session if no PlexAmp session found
        """
        data = self._plex_get("/status/sessions")
        if not data:
            return {}

        all_sessions = data.get("MediaContainer", {}).get("Metadata", [])

        # Keep only music tracks
        music = [s for s in all_sessions if s.get("type") == "track"]

        # Prefer PlexAmp player
        plexamp = [
            s for s in music
            if "plexamp" in (s.get("Player") or {}).get("product", "").lower()
        ]

        session = (plexamp or music or [None])[0]
        if session is None:
            return {"available": True, "playing": False}

        thumb   = session.get("thumb", "")
        art_url = (
            f"{self._server}{thumb}?X-Plex-Token={self._token}" if thumb else ""
        )
        player  = session.get("Player") or {}

        return {
            "available":           True,
            "title":               session.get("title", ""),
            "artist":              session.get("grandparentTitle", ""),
            "album":               session.get("parentTitle", ""),
            "playing":             player.get("state", "") == "playing",
            "album_art":           art_url,
            "duration_ms":         int(session.get("duration",   0) or 0),
            "position_ms":         int(session.get("viewOffset", 0) or 0),
            "player_state":        player.get("state", ""),
            "player_name":         player.get("title", ""),
            "current_playlist_id": self._current_playlist_id,
        }

    # -- transport controls -------------------------------------------------

    def play(self):
        status = self.get_status()
        if not status.get("available"):
            return
        if status.get("playing"):
            # "Phantom playing" — PlexAmp reports state=playing but viewOffset
            # is stuck at 0, meaning the audio pipeline has broken down after
            # inactivity (ALSA releasing the device on a headless Pi).
            # Re-queuing the playlist forces PlexAmp to restart the pipeline.
            if status.get("position_ms", -1) == 0 and self._current_playlist_id:
                logger.info("PlexAmp phantom-playing detected (playing but position=0)"
                            " — stopping then re-queuing to force ALSA re-init")
                self._plex_command("/player/playback/stop")
                time.sleep(0.5)
                self.set_playlist(self._current_playlist_id)
            return
        if status.get("title"):
            # Something is loaded but paused — just resume
            self._plex_command("/player/playback/play")
        else:
            # PlexAmp session is fully idle — re-queue before playing
            playlist_id = self._current_playlist_id
            if playlist_id:
                self.set_playlist(playlist_id)
            else:
                # No playlist on record — try a bare play and hope for the best
                self._plex_command("/player/playback/play")

    def pause(self):
        self._plex_command("/player/playback/pause")

    def next_track(self):
        self._plex_command("/player/playback/skipNext")

    def prev_track(self):
        self._plex_command("/player/playback/skipPrevious")

    # -- playlists ----------------------------------------------------------

    def get_playlists(self) -> list[dict]:
        """Fetch all audio playlists from Plex Media Server."""
        data = self._plex_get("/playlists?playlistType=audio")
        if not data:
            return []
        items = data.get("MediaContainer", {}).get("Metadata", [])
        return [
            {
                "id":    str(p.get("ratingKey", "")),
                "name":  p.get("title", ""),
                "count": p.get("leafCount", ""),
            }
            for p in items
            if p.get("ratingKey")
        ]

    def set_playlist(self, playlist_id: str):
        """Queue a playlist on the PlexAmp player."""
        self._current_playlist_id = playlist_id
        self._plex_command(
            "/player/playback/playMedia",
            extra_params={
                "key":               f"/playlists/{playlist_id}/items",
                "uri":               (
                    f"server://{self._machine_id}/com.plexapp.plugins.library"
                    f"/playlists/{playlist_id}/items"
                ),
                "machineIdentifier": self._machine_id,
                "address":           self._address,
                "port":              self._port,
                "token":             self._token,
                "type":              "music",
                "shuffle":           1,
            },
        )

    # -- diagnostics --------------------------------------------------------

    def test_connection(self) -> dict:
        """Verify server URL and token by hitting /identity."""
        try:
            r = requests.get(
                f"{self._server}/identity",
                headers=self._headers,
                timeout=_TIMEOUT,
            )
            r.raise_for_status()
            mc = r.json().get("MediaContainer", {})
            return {
                "ok":      True,
                "server":  self._server,
                "version": mc.get("version", "unknown"),
                "name":    mc.get("friendlyName", ""),
            }
        except requests.exceptions.ConnectionError:
            return {
                "ok":    False,
                "error": f"Cannot reach {self._server} — check server_url in config.json",
            }
        except requests.exceptions.HTTPError as exc:
            if exc.response.status_code == 401:
                return {"ok": False, "error": "Authentication failed — token is wrong or expired"}
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Simulator  (development use — set "simulate": true on a source in config.json)
# ---------------------------------------------------------------------------

class SimulatedStreamingSource(StreamingSource):
    """
    Fake streaming backend for development without real Pandora/Plex servers.

    Maintains its own playback state in memory so all UI interactions
    (play, pause, next, prev, playlist select) work and produce correct
    responses without any network connections.

    Enable per-source in config.json:
        {"source": 1, "name": "Pandora", "type": "streaming",
         "simulate": true, ...}
    """

    _TRACKS = [
        {"title": "Bohemian Rhapsody",  "artist": "Queen",       "album": "A Night at the Opera"},
        {"title": "Hotel California",   "artist": "Eagles",      "album": "Hotel California"},
        {"title": "Stairway to Heaven", "artist": "Led Zeppelin","album": "Led Zeppelin IV"},
        {"title": "Comfortably Numb",   "artist": "Pink Floyd",  "album": "The Wall"},
        {"title": "Superstition",       "artist": "Stevie Wonder","album": "Talking Book"},
    ]

    _PLAYLISTS = [
        {"id": "1", "name": "Classic Rock"},
        {"id": "2", "name": "80s Hits"},
        {"id": "3", "name": "Jazz Standards"},
        {"id": "4", "name": "Chill Vibes"},
    ]

    _DURATION_MS = 240_000   # 4 minutes per track

    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self._playing        = False
        self._track_index    = 0
        self._playlist_id    = self._PLAYLISTS[0]["id"]
        self._track_start    = time.time()
        self._paused_elapsed = 0.0   # ms accumulated while paused

    # -- internal helpers ---------------------------------------------------

    def _elapsed_ms(self) -> int:
        if self._playing:
            live = (time.time() - self._track_start) * 1000
        else:
            live = 0.0
        return min(int(self._paused_elapsed + live), self._DURATION_MS)

    def _reset_track(self):
        self._track_start    = time.time()
        self._paused_elapsed = 0.0

    # -- StreamingSource interface ------------------------------------------

    def get_status(self) -> dict:
        pos_ms = self._elapsed_ms()
        # Auto-advance when the simulated track finishes
        if pos_ms >= self._DURATION_MS:
            self._track_index += 1
            self._reset_track()
            pos_ms = 0
        track    = self._TRACKS[self._track_index % len(self._TRACKS)]
        playlist = next((p for p in self._PLAYLISTS if p["id"] == self._playlist_id),
                        self._PLAYLISTS[0])
        return {
            "available":   True,
            "title":       track["title"],
            "artist":      track["artist"],
            "album":       track["album"],
            "station":     playlist["name"],
            "playing":     self._playing,
            "album_art":   "",
            "duration_ms": self._DURATION_MS,
            "position_ms": pos_ms,
        }

    def play(self):
        if not self._playing:
            self._track_start = time.time()
            self._playing = True

    def pause(self):
        if self._playing:
            self._paused_elapsed += (time.time() - self._track_start) * 1000
            self._playing = False

    def next_track(self):
        self._track_index += 1
        self._reset_track()

    def prev_track(self):
        # Restart current track if more than 3 s in; otherwise go to previous
        if self._elapsed_ms() > 3_000:
            self._reset_track()
        else:
            self._track_index = max(0, self._track_index - 1)
            self._reset_track()

    def get_playlists(self) -> list[dict]:
        return self._PLAYLISTS

    def set_playlist(self, playlist_id: str):
        self._playlist_id = playlist_id
        self._track_index = 0
        self._reset_track()

    def test_connection(self) -> dict:
        return {"ok": True, "simulated": True, "source": self.name}


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_streaming_source(
    source_config: dict, plex_config: dict | None = None
) -> StreamingSource:
    # Per-source simulator flag takes priority over backend type
    if source_config.get("usesimulator"):
        return SimulatedStreamingSource(source_config)
    name = source_config.get("name", "").lower()
    if "pandora" in name:
        return PandoraSource(source_config)
    if "plex" in name and plex_config:
        return PlexSource(source_config, plex_config)
    return StreamingSource(source_config)
