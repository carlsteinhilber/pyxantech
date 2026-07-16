#!/usr/bin/env python3
"""
plex_info.py – Fetch Plex server values needed for config.json.

Reads server_url and token from config.json and prints the
machine_identifier and address values to copy into the plex section.

Usage (run from the PyXantech5 directory):
    python plex_info.py
"""

import json
import sys

import requests

CONFIG_FILE = "config.json"
TIMEOUT = 5


def main():
    # ── Load config ───────────────────────────────────────────────────────────
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
    except FileNotFoundError:
        sys.exit(f"Error: {CONFIG_FILE} not found. Run from the PyXantech5 directory.")

    plex = config.get("plex", {})
    ip_address = plex.get("ip_address", "")
    token      = plex.get("token", "")

    if not ip_address:
        sys.exit("Error: 'ip_address' not set in config.json under 'plex'.")
    if not token:
        sys.exit("Error: 'token' not set in config.json under 'plex'.")

    port       = plex.get("port", 32400)
    server_url = f"http://{ip_address}:{port}"

    # ── Fetch machine_identifier from /identity ───────────────────────────────
    print(f"\nConnecting to {server_url} ...")
    try:
        r = requests.get(
            f"{server_url}/identity",
            headers={"X-Plex-Token": token, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        machine_id = r.json().get("MediaContainer", {}).get("machineIdentifier", "")
    except requests.exceptions.ConnectionError:
        sys.exit(f"Error: cannot reach {server_url} — is Plex running and is the IP correct?")
    except requests.exceptions.HTTPError as exc:
        if exc.response.status_code == 401:
            sys.exit("Error: token is invalid or expired.")
        sys.exit(f"HTTP error: {exc}")
    except Exception as exc:
        sys.exit(f"Unexpected error: {exc}")

    if not machine_id:
        sys.exit("Error: /identity response did not contain a machineIdentifier.")

    # ── Fetch audio playlists ─────────────────────────────────────────────────
    headers = {"X-Plex-Token": token, "Accept": "application/json"}
    try:
        r = requests.get(
            f"{server_url}/playlists?playlistType=audio",
            headers=headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        playlists = r.json().get("MediaContainer", {}).get("Metadata", [])
    except Exception:
        playlists = []

    # ── Report ────────────────────────────────────────────────────────────────
    print("\nValues for config.json → \"plex\":\n")
    print(f'  "ip_address":         "{ip_address}"')
    print(f'  "machine_identifier": "{machine_id}"')

    # Compare against what's currently in config.json
    current_id = plex.get("machine_identifier", "")
    print()
    if current_id == machine_id:
        print("  ✓ machine_identifier matches what's in config.json")
    else:
        print(f"  ✗ machine_identifier DIFFERS — update config.json to: \"{machine_id}\"")

    # ── Playlist listing ──────────────────────────────────────────────────────
    current_default = plex.get("default_playlist_id", "")
    print()
    if playlists:
        print("Audio playlists (for \"default_playlist_id\"):\n")
        for p in playlists:
            pid   = str(p.get("ratingKey", ""))
            name  = p.get("title", "")
            count = p.get("leafCount", "?")
            marker = " ◀  current default" if pid == str(current_default) else ""
            print(f'  {pid:<10}  {name}  ({count} tracks){marker}')
    else:
        print("  (no audio playlists found, or request failed)")

    print()


if __name__ == "__main__":
    main()
