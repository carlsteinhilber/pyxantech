#!/usr/bin/env python3
"""
plex_debug.py – Diagnose why PlexAmp does not start playing on zone power-on.

Walks through every step that _nudge_streaming_play() → PlexSource.play()
performs, printing the raw API response at each stage so you can see exactly
where the logic fails.

Usage (run from the PyXantech5 directory):
    python plex_debug.py           # inspect state only, do not send any commands
    python plex_debug.py --play    # also send the play / set_playlist command
"""

import argparse
import json
import sys

import requests

CONFIG_FILE = "config.json"
TIMEOUT     = 5


# ── Helpers ───────────────────────────────────────────────────────────────────

def hdr(text):
    print(f"\n{'─' * 60}")
    print(f"  {text}")
    print('─' * 60)

def ok(msg):   print(f"  ✓  {msg}")
def fail(msg): print(f"  ✗  {msg}")
def info(msg): print(f"     {msg}")
def raw(label, data):
    print(f"\n  {label}:")
    print(json.dumps(data, indent=4))


# ── Load config ───────────────────────────────────────────────────────────────

try:
    with open(CONFIG_FILE) as f:
        config = json.load(f)
except FileNotFoundError:
    sys.exit(f"Error: {CONFIG_FILE} not found. Run from the PyXantech5 directory.")

plex   = config.get("plex", {})
ip     = plex.get("ip_address", "")
port   = plex.get("port", 32400)
token  = plex.get("token", "")
mid    = plex.get("machine_identifier", "")
defpid = plex.get("default_playlist_id", "")

# PlexAmp URL comes from the Plex source entry
plex_src = next(
    (s for s in config.get("sources", [])
     if s.get("enabled") and "plex" in s.get("name", "").lower()),
    None,
)
if not plex_src:
    sys.exit("Error: no enabled Plex source found in config.json → sources.")

amp_url    = plex_src.get("url", "").rstrip("/")
server_url = f"http://{ip}:{port}"
headers    = {"X-Plex-Token": token, "Accept": "application/json"}

parser = argparse.ArgumentParser()
parser.add_argument("--play", action="store_true",
                    help="Actually send play / set_playlist commands (not just inspect)")
args = parser.parse_args()


# ── Step 1: Reach PMS ────────────────────────────────────────────────────────

hdr("Step 1 — Plex Media Server reachability")
info(f"server_url : {server_url}")
try:
    r = requests.get(f"{server_url}/identity", headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    mc = r.json().get("MediaContainer", {})
    ok(f"Reached PMS  version={mc.get('version','?')}  "
       f"machineIdentifier={mc.get('machineIdentifier','?')}")
    if mc.get("machineIdentifier") != mid:
        fail(f"machine_identifier in config ({mid}) does not match server "
             f"({mc.get('machineIdentifier')}) — update config.json")
except requests.exceptions.ConnectionError:
    fail(f"Cannot reach {server_url}")
    sys.exit("Fix: check ip_address and port in config.json, and that PMS is running.")
except requests.exceptions.HTTPError as e:
    fail(f"HTTP {e.response.status_code} — token may be invalid")
    sys.exit()


# ── Step 2: Reach PlexAmp ────────────────────────────────────────────────────

hdr("Step 2 — PlexAmp reachability")
info(f"amp_url : {amp_url}")
try:
    r = requests.get(f"{amp_url}/player/timeline/poll",
                     params={"commandID": 1}, headers=headers, timeout=TIMEOUT)
    # PlexAmp returns 200 or 400 — either means it's alive
    ok(f"PlexAmp responded  status={r.status_code}")
except requests.exceptions.ConnectionError:
    fail(f"Cannot reach PlexAmp at {amp_url}")
    sys.exit("Fix: check the Plex source 'url' in config.json, and that PlexAmp is running.")
except Exception as e:
    fail(f"Unexpected error: {e}")


# ── Step 3: Raw /status/sessions ──────────────────────────────────────────────

hdr("Step 3 — Raw PMS /status/sessions")
try:
    r = requests.get(f"{server_url}/status/sessions", headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    sessions_data = r.json()
    all_sessions  = sessions_data.get("MediaContainer", {}).get("Metadata", [])
    info(f"Total active sessions: {len(all_sessions)}")
    if all_sessions:
        raw("Full session list", all_sessions)
    else:
        info("No active sessions — PlexAmp is completely idle")
except Exception as e:
    fail(f"Could not fetch sessions: {e}")
    all_sessions = []


# ── Step 4: Filter as PlexSource.get_status() does ───────────────────────────

hdr("Step 4 — Filtered sessions (as PlexSource.get_status sees them)")

music = [s for s in all_sessions if s.get("type") == "track"]
info(f"Music-type sessions: {len(music)}")

plexamp_sessions = [
    s for s in music
    if "plexamp" in (s.get("Player") or {}).get("product", "").lower()
]
info(f"PlexAmp sessions:    {len(plexamp_sessions)}")

session = (plexamp_sessions or music or [None])[0]

if session is None:
    info("→ session is None  →  get_status() returns {available: True, playing: False}")
    info("   play() will check status.get('title') which will be falsy")
    info("   → play() will attempt to re-queue the playlist")
    status = {"available": True, "playing": False, "title": ""}
else:
    player = session.get("Player") or {}
    thumb  = session.get("thumb", "")
    status = {
        "available":   True,
        "title":       session.get("title", ""),
        "artist":      session.get("grandparentTitle", ""),
        "album":       session.get("parentTitle", ""),
        "playing":     player.get("state", "") == "playing",
        "album_art":   f"{server_url}{thumb}?X-Plex-Token={token}" if thumb else "",
        "duration_ms": int(session.get("duration",   0) or 0),
        "position_ms": int(session.get("viewOffset", 0) or 0),
        "player_state": player.get("state", ""),
        "player_name":  player.get("title", ""),
    }
    raw("Normalised get_status() result", status)


# ── Step 5: Trace through play() logic ───────────────────────────────────────

hdr("Step 5 — What play() would do given this status")

if not status.get("available"):
    info("→ status['available'] is False → play() returns immediately (no-op)")
    sys.exit()

if status.get("playing"):
    if status.get("position_ms", -1) == 0:
        fail("PHANTOM PLAYING DETECTED")
        info("   PlexAmp reports state=playing but viewOffset=0 — the audio pipeline")
        info("   has broken down after inactivity (ALSA releasing the device on a")
        info("   headless Pi).  The progress bar will loop 0:00→0:04→0:00 with no audio.")
        info("")
        info("   Fix: play() will re-queue the playlist to restart the pipeline.")
        action = "set_playlist"
    else:
        info(f"→ Genuinely playing at position {status['position_ms']}ms → play() is a no-op")
        action = None
        if not args.play:
            sys.exit()

elif status.get("title"):
    info("→ Something is loaded but paused")
    info("   play() will send:  GET /player/playback/play")
    action = "play"
else:
    info("→ PlexAmp is IDLE (no title in session)")
    info("   play() will attempt to re-queue a playlist first")
    action = "set_playlist"
    if defpid:
        info(f"   default_playlist_id from config: {defpid}")
    else:
        info("   ⚠  default_playlist_id is empty in config.json")
        info("      run plex_info.py to find a playlist ID, then set it in config.json")


# ── Step 6: Check playlist (only relevant if idle) ───────────────────────────

if action == "set_playlist":
    hdr("Step 6 — Playlist check")
    playlist_id = defpid or None
    info(f"_current_playlist_id (from config default): {playlist_id or '(none)'}")

    if not playlist_id:
        fail("No playlist_id available — play() will send a bare play command that "
             "will silently fail on an idle PlexAmp")
        info("Fix: set 'default_playlist_id' in config.json (run plex_info.py to find IDs)")
    else:
        # Verify the playlist exists on PMS
        try:
            r = requests.get(
                f"{server_url}/playlists/{playlist_id}",
                headers=headers, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                name = (r.json().get("MediaContainer", {})
                        .get("Metadata", [{}])[0].get("title", "?"))
                ok(f"Playlist {playlist_id} found on PMS: \"{name}\"")
            else:
                fail(f"Playlist {playlist_id} returned HTTP {r.status_code} — "
                     "ID may be stale; run plex_info.py to get current IDs")
        except Exception as e:
            fail(f"Could not verify playlist: {e}")

    if args.play and playlist_id:
        hdr("Step 6a — Sending stop command first (force ALSA re-init)")
        try:
            r = requests.get(
                f"{amp_url}/player/playback/stop",
                params={"commandID": 1}, headers=headers, timeout=TIMEOUT,
            )
            ok(f"stop responded: HTTP {r.status_code}")
        except Exception as e:
            fail(f"stop command failed: {e}")

        info("Waiting 500ms for ALSA to release...")
        import time; time.sleep(0.5)

        hdr("Step 6b — Sending set_playlist command")
        cmd_id = 2
        params = {
            "commandID":         cmd_id,
            "key":               f"/playlists/{playlist_id}/items",
            "uri":               (
                f"server://{mid}/com.plexapp.plugins.library"
                f"/playlists/{playlist_id}/items"
            ),
            "machineIdentifier": mid,
            "address":           ip,
            "port":              str(port),
            "token":             token,
            "type":              "music",
            "shuffle":           1,
        }
        info(f"Sending: GET {amp_url}/player/playback/playMedia")
        raw("Params", params)
        try:
            r = requests.get(
                f"{amp_url}/player/playback/playMedia",
                params=params, headers=headers, timeout=TIMEOUT,
            )
            ok(f"PlexAmp responded: HTTP {r.status_code}")
            if r.text:
                info(f"Response body: {r.text[:300]}")
        except Exception as e:
            fail(f"Command failed: {e}")

        info("")
        info("Wait ~3 seconds, then check whether viewOffset starts advancing:")
        import time; time.sleep(3)
        hdr("Post-command session check")
        try:
            r = requests.get(f"{server_url}/status/sessions",
                             headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            sessions = r.json().get("MediaContainer", {}).get("Metadata", [])
            music = [s for s in sessions if s.get("type") == "track"]
            if music:
                s = music[0]
                player_state = (s.get("Player") or {}).get("state", "?")
                title        = s.get("title", "?")
                offset       = s.get("viewOffset", 0)
                if offset > 0:
                    ok(f"Playing '{title}'  position={offset}ms  state={player_state}")
                    ok("Audio pipeline appears to have restarted successfully")
                else:
                    fail(f"Still stuck: '{title}'  position={offset}ms  state={player_state}")
                    info("The ALSA device may need a deeper reset — consider restarting PlexAmp on the Pi:")
                    info("  ssh pi@192.168.1.29 'sudo systemctl restart plexamp'")
            else:
                info("No active sessions found after command")
        except Exception as e:
            fail(f"Post-check failed: {e}")

elif action == "play" and args.play:
    hdr("Step 6a — Sending play command (--play mode)")
    try:
        r = requests.get(
            f"{amp_url}/player/playback/play",
            params={"commandID": 1}, headers=headers, timeout=TIMEOUT,
        )
        ok(f"PlexAmp responded: HTTP {r.status_code}")
        if r.text:
            info(f"Response body: {r.text[:300]}")
    except Exception as e:
        fail(f"Command failed: {e}")


# ── Summary ───────────────────────────────────────────────────────────────────

hdr("Summary")
if not args.play:
    info("Run with --play to actually send the commands above.")
info("Done.")
print()
