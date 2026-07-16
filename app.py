"""
app.py – Flask + Flask-SocketIO entry point for the Xantech MRC88 controller.

Run on the Raspberry Pi with:
    python app.py

The web UI is served on port 5001 so it doesn't clash with the Pandora
pianobar web interface which occupies port 5000.
"""


import collections
import json
import logging
import os
import socket
import struct
import threading
import time
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO, emit

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App + SocketIO
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = "xantech-mrc88-pi-secret"

# Cache-busting version stamp — changes on every server restart so browsers
# always fetch the latest JS/CSS rather than serving stale cached copies.
_JS_VERSION = int(time.time())

# threading async_mode avoids the eventlet / gevent monkey-patch requirement
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

with open("config.json") as _f:
    config: dict = json.load(_f)

# ---------------------------------------------------------------------------
# MRC88 controller
# ---------------------------------------------------------------------------

from xantech import MRC88Controller

controller = MRC88Controller(
    port=config["system"]["serialport"],
    use_simulator=config["system"].get("usesimulator", False),
    debugging=config["system"].get("debugging", False),
)
controller.set_socketio(socketio)

# ---------------------------------------------------------------------------
# Streaming sources
# ---------------------------------------------------------------------------

from streaming import create_streaming_source

_streaming_sources: dict[int, object] = {}
for _src in config.get("sources", []):
    if _src.get("type") == "streaming" and _src.get("enabled"):
        _streaming_sources[_src["source"]] = create_streaming_source(
            _src, config.get("plex")
        )

# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    appname = config["system"].get("appname", "Xantech MRC88")
    theme = config["system"].get("theme", "").strip()
    theme_file = None
    if theme:
        candidate = os.path.join(app.static_folder, "styles", "themes", f"{theme}.css")
        if os.path.isfile(candidate):
            theme_file = f"styles/themes/{theme}.css"
    return render_template(
        "index.html",
        config_json=json.dumps(config),
        appname=appname,
        theme_file=theme_file,
        js_version=_JS_VERSION,
    )


@app.route("/api/config")
def api_config():
    return jsonify(config)


@app.route("/api/states")
def api_states():
    # JSON keys must be strings
    return jsonify({str(k): v for k, v in controller.get_all_states().items()})


# -- Streaming proxy --------------------------------------------------------


@app.route("/api/streaming/<int:source_id>/status")
def api_streaming_status(source_id: int):
    src = _streaming_sources.get(source_id)
    if src is None:
        return jsonify({"error": "source not found"}), 404
    return jsonify(src.get_status())


@app.route("/api/streaming/<int:source_id>/play", methods=["POST"])
def api_streaming_play(source_id: int):
    src = _streaming_sources.get(source_id)
    if src:
        src.play()
    return jsonify({"ok": True})


@app.route("/api/streaming/<int:source_id>/pause", methods=["POST"])
def api_streaming_pause(source_id: int):
    src = _streaming_sources.get(source_id)
    if src:
        src.pause()
    return jsonify({"ok": True})


@app.route("/api/streaming/<int:source_id>/next", methods=["POST"])
def api_streaming_next(source_id: int):
    src = _streaming_sources.get(source_id)
    if src:
        src.next_track()
    return jsonify({"ok": True})


@app.route("/api/streaming/<int:source_id>/prev", methods=["POST"])
def api_streaming_prev(source_id: int):
    src = _streaming_sources.get(source_id)
    if src:
        src.prev_track()
    return jsonify({"ok": True})


@app.route("/api/streaming/<int:source_id>/playlists")
def api_streaming_playlists(source_id: int):
    src = _streaming_sources.get(source_id)
    if src is None:
        return jsonify([])
    return jsonify(src.get_playlists())


@app.route("/api/streaming/<int:source_id>/playlist", methods=["POST"])
def api_streaming_set_playlist(source_id: int):
    src = _streaming_sources.get(source_id)
    if src:
        data = request.get_json(silent=True) or {}
        src.set_playlist(str(data.get("id", "")))
    return jsonify({"ok": True})

# ---------------------------------------------------------------------------
# Zone REST API  (for SmartThings, Home Assistant, Node-RED, etc.)
# ---------------------------------------------------------------------------
# All endpoints return the current zone state as JSON so the caller can
# confirm the result without a separate GET.  Every write goes through the
# same MRC88Controller methods, so Socket.IO events fire and the web UI
# stays in sync automatically.
#
#   GET  /api/zones              → {1: {power, volume, source, mute, name}, ...}
#   GET  /api/zones/<zone>       → {power, volume, source, mute}
#   PUT  /api/zones/<zone>       → update any subset of fields; returns new state
#
# PUT body (all fields optional):
#   { "power": true|false,
#     "volume": 0-38,
#     "volume_delta": ±N,   ← relative change; applied after absolute if both sent
#     "source": 1-8,
#     "muted": true|false }
# ---------------------------------------------------------------------------


def _zone_state_with_name(zone: int) -> dict:
    """Return controller state for a zone with the config name included."""
    zone_cfg = next((z for z in config.get("zones", []) if z["zone"] == zone), {})
    return {**controller.state[zone], "name": zone_cfg.get("name", "") or f"Zone {zone}"}


def _zone_or_404(zone: int):
    """Return (state_dict, None) or (None, error_response)."""
    enabled = {z["zone"] for z in config.get("zones", []) if z.get("enabled")}
    if zone not in enabled:
        return None, (jsonify({"error": f"zone {zone} not found or not enabled"}), 404)
    return controller.state[zone], None


@app.route("/api/zones")
def api_zones():
    """Return state for all enabled zones."""
    zone_configs = {z["zone"]: z for z in config.get("zones", []) if z.get("enabled")}
    result = {}
    for z in sorted(zone_configs):
        state = dict(controller.state[z])
        state["name"] = zone_configs[z].get("name", "") or ("Zone " + str(z))
        result[str(z)] = state
    return jsonify(result)


@app.route("/api/zones/<int:zone>")
def api_zone_get(zone: int):
    """Return state for a single zone."""
    state, err = _zone_or_404(zone)
    if err:
        return err
    return jsonify(_zone_state_with_name(zone))


@app.route("/api/zones/off", methods=["POST"])
def api_zones_all_off():
    """Turn all zones off via the MRC88 !AO+ command."""
    controller.all_off()
    return jsonify({"ok": True})


def _resolve_source(val) -> int:
    """Accept a source number (1-8) or a source name ('Plex', 'Pandora', …)."""
    try:
        return int(val)
    except (ValueError, TypeError):
        name = str(val).lower()
        for src in config.get("sources", []):
            if src.get("name", "").lower() == name:
                return src["source"]
        raise ValueError(f"Unknown source name: {val!r}")


def _parse_bool(val) -> bool:
    """Accept bool, int, or string truthy values from JSON or query strings."""
    if isinstance(val, bool):
        return val
    return str(val).lower() in ("true", "on", "1", "yes")


def _apply_zone_changes(zone: int, params: dict):
    """
    Apply a dict of zone changes.  Used by both the PUT (JSON body) and
    GET /set (query string) endpoints so the logic lives in one place.

    Accepted keys (all optional):
      power         bool / "true"/"on"/"1"  – turn zone on/off
      volume        int                     – set absolute volume (0-38)
      volume_delta  int                     – adjust volume by ±N steps
      source        int                     – select source (1-8)
      muted         bool / "true"/"on"/"1"  – mute/unmute
    """
    if "power" in params:
        turning_on = _parse_bool(params["power"])
        old_source = controller.state[zone].get("source")
        controller.set_power(zone, turning_on)
        if turning_on:
            _nudge_streaming_play(zone)
        elif old_source in _streaming_sources:
            _auto_pause_if_unused(old_source)

    if "source" in params:
        try:
            source = _resolve_source(params["source"])
        except ValueError:
            raise ValueError(
                f"Invalid source {params['source']!r} — use a number (1-8) "
                f"or a name from config.json"
            )
        old_source = controller.state[zone].get("source")
        controller.set_source(zone, source)
        if controller.state[zone].get("power") and source in _streaming_sources:
            _streaming_sources[source].play()
        if old_source != source and old_source in _streaming_sources:
            _auto_pause_if_unused(old_source)

    if "volume" in params:
        try:
            controller.set_volume(zone, int(params["volume"]))
        except (ValueError, TypeError):
            raise ValueError(f"Invalid volume {params['volume']!r} — must be an integer 0-38")

    if "volume_delta" in params:
        try:
            delta = int(params["volume_delta"])
        except (ValueError, TypeError):
            raise ValueError(f"Invalid volume_delta {params['volume_delta']!r} — must be an integer")
        if delta > 0:
            for _ in range(delta):
                controller.volume_up(zone)
        elif delta < 0:
            for _ in range(-delta):
                controller.volume_down(zone)

    if "muted" in params:
        controller.set_mute(zone, _parse_bool(params["muted"]))


@app.route("/api/zones/<int:zone>", methods=["PUT"])
def api_zone_put(zone: int):
    """Update zone properties via PUT with a JSON body."""
    _, err = _zone_or_404(zone)
    if err:
        return err
    try:
        _apply_zone_changes(zone, request.get_json(silent=True) or {})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_zone_state_with_name(zone))


@app.route("/api/zones/<int:zone>/set")
def api_zone_set(zone: int):
    """
    Update zone properties via GET with query string parameters.
    Intended for home automation tools (IFTTT, Home Assistant webhooks, etc.)
    that cannot send PUT requests with a JSON body.

    Examples:
      GET /api/zones/1/set?power=true
      GET /api/zones/1/set?power=on&source=Plex&volume=20
      GET /api/zones/3/set?muted=true
      GET /api/zones/1/set?volume_delta=-5
    """
    _, err = _zone_or_404(zone)
    if err:
        return err
    try:
        _apply_zone_changes(zone, request.args)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_zone_state_with_name(zone))


# -- Connection test --------------------------------------------------------


@app.route("/api/streaming/<int:source_id>/test")
def api_streaming_test(source_id: int):
    """
    Quick diagnostic: hit GET /api/streaming/5/test (Plex is source 5)
    to verify server URL and token are correct before doing anything else.
    """
    src = _streaming_sources.get(source_id)
    if src is None:
        return jsonify({"error": "source not found"}), 404
    return jsonify(src.test_connection())

@app.route('/description.xml')
def description():
    xml = '''<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:xantech:device:AudioController:1</deviceType>
    <friendlyName>Xantech Audio Controller</friendlyName>
    <manufacturer>Xantech</manufacturer>
    <modelName>Audio Controller</modelName>
    <UDN>uuid:xantech-audio-controller-001</UDN>
  </device>
</root>'''
    return xml, 200, {'Content-Type': 'application/xml'}

# ---------------------------------------------------------------------------
# Server-side streaming status push
# ---------------------------------------------------------------------------
# One background thread polls every streaming source every 5 seconds and
# broadcasts the result to all connected browsers via Socket.IO.
# This means only one HTTP request goes out per source regardless of how
# many browser tabs are open, and all clients update simultaneously.

def _streaming_status_pusher():
    while True:
        time.sleep(5)
        for source_id, src in _streaming_sources.items():
            try:
                status = src.get_status()
                # Always emit — even {"available": False} — so the UI can
                # show an unavailable state rather than going silently stale.
                socketio.emit(
                    "streaming_status",
                    {"source_id": source_id, "status": status},
                )
            except Exception as exc:
                logger.warning("Status push error source %s: %s", source_id, exc)

if _streaming_sources:
    threading.Thread(
        target=_streaming_status_pusher,
        daemon=True,
        name="streaming-pusher",
    ).start()

    def _startup_pause():
        # Wait for the MRC88 startup sync to populate zone states,
        # then pause any streaming source that has no active zone.
        time.sleep(8)
        for src_id in list(_streaming_sources):
            _auto_pause_if_unused(src_id)

    threading.Thread(target=_startup_pause, daemon=True, name="startup-pause").start()


# ---------------------------------------------------------------------------
# SSDP announcer (SmartThings hub discovery)
# ---------------------------------------------------------------------------

def _start_ssdp():
    SSDP_ADDR   = "239.255.255.250"
    SSDP_PORT   = 1900
    DEVICE_PORT = 5001
    DEVICE_TYPE = "urn:xantech:device:AudioController:1"
    USN         = f"uuid:xantech-audio-001::{DEVICE_TYPE}"

    # Auto-detect the Pi's LAN IP
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        DEVICE_IP = _s.getsockname()[0]
        _s.close()
    except Exception:
        DEVICE_IP = "192.168.1.30"

    logger.info("SSDP: advertising %s at %s:%s", DEVICE_TYPE, DEVICE_IP, DEVICE_PORT)

    notify_msg = "\r\n".join([
        "NOTIFY * HTTP/1.1",
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}",
        "CACHE-CONTROL: max-age=1800",
        f"LOCATION: http://{DEVICE_IP}:{DEVICE_PORT}/description.xml",
        f"NT: {DEVICE_TYPE}",
        "NTS: ssdp:alive",
        "SERVER: Linux/1.0 UPnP/1.0 Xantech/1.0",
        f"USN: {USN}",
        "", ""
    ]).encode()

    def make_response():
        return "\r\n".join([
            "HTTP/1.1 200 OK",
            "CACHE-CONTROL: max-age=1800",
            "EXT:",
            f"LOCATION: http://{DEVICE_IP}:{DEVICE_PORT}/description.xml",
            "SERVER: Linux/1.0 UPnP/1.0 Xantech/1.0",
            f"ST: {DEVICE_TYPE}",
            f"USN: {USN}",
            "", ""
        ]).encode()

    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)

    listen_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listen_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    listen_sock.bind(("", SSDP_PORT))
    mreq = struct.pack("4sL", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
    listen_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    listen_sock.settimeout(1.0)

    def notify_loop():
        while True:
            try:
                send_sock.sendto(notify_msg, (SSDP_ADDR, SSDP_PORT))
            except Exception as exc:
                logger.warning("SSDP notify error: %s", exc)
            time.sleep(10)

    def listen_loop():
        while True:
            try:
                data, addr = listen_sock.recvfrom(2048)
                msg = data.decode(errors="ignore")
                if "M-SEARCH" in msg:
                    send_sock.sendto(make_response(), addr)
                    logger.debug("SSDP: M-SEARCH from %s, response sent", addr[0])
            except socket.timeout:
                pass
            except Exception as exc:
                logger.warning("SSDP listen error: %s", exc)

    threading.Thread(target=notify_loop, daemon=True, name="ssdp-notify").start()
    threading.Thread(target=listen_loop, daemon=True, name="ssdp-listen").start()


# ---------------------------------------------------------------------------
# Activity monitor
# ---------------------------------------------------------------------------
# Browse to /monitor to watch live server-side activity: serial TX/RX,
# HTTP requests/responses, and incoming WebSocket events.
#
# Events are broadcast to all connected clients; the normal zone-control
# page simply ignores the 'monitor_event' message type.  A ring buffer of
# the last 200 entries is sent to any browser that opens /monitor so it
# gets an immediate picture of recent activity.

_monitor_buffer: collections.deque = collections.deque(maxlen=200)


def _monitor_emit(cat: str, summary: str, detail: str = ""):
    """Record one monitor event and push it to every connected browser."""
    entry = {
        "ts":      time.strftime("%H:%M:%S"),
        "cat":     cat,
        "summary": summary,
        "detail":  detail,
    }
    _monitor_buffer.append(entry)
    try:
        socketio.emit("monitor_event", entry)
    except Exception:
        pass  # never let the monitor break the app


class _MonitorLogHandler(logging.Handler):
    """Forwards Python log records (including serial TX/RX) to the monitor."""
    def emit(self, record):
        try:
            cat = ("ERR" if record.levelno >= logging.ERROR else
                   "WRN" if record.levelno >= logging.WARNING else "LOG")
            _monitor_emit(cat, record.getMessage())
        except Exception:
            pass


# Attach to the root logger so we catch xantech TX/RX, streaming, everything.
_mon_handler = _MonitorLogHandler()
_mon_handler.setLevel(logging.DEBUG)
logging.getLogger().addHandler(_mon_handler)


_MONITOR_SKIP = {"/monitor", "/favicon.ico"}

@app.before_request
def _monitor_request():
    if request.path in _MONITOR_SKIP or request.path.startswith("/static"):
        return
    detail = ""
    if request.method in ("POST", "PUT") and request.content_length:
        detail = request.get_data(as_text=True)[:500]
    elif request.args:
        detail = str(dict(request.args))
    _monitor_emit("HTTP ↓", f"{request.method} {request.path}", detail)


@app.after_request
def _monitor_response(response):
    if request.path in _MONITOR_SKIP or request.path.startswith("/static"):
        return response
    detail = ""
    if "json" in (response.content_type or ""):
        detail = response.get_data(as_text=True)[:300]
    _monitor_emit("HTTP ↑", f"{response.status} {request.path}", detail)
    return response


@app.route("/monitor")
def monitor_page():
    return render_template("monitor.html", history=json.dumps(list(_monitor_buffer)))


# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------


@socketio.on("connect")
def on_connect():
    """Push full state to a newly connected browser."""
    for zone, state in controller.get_all_states().items():
        emit("zone_state", {"zone": zone, "state": state})
    # Tell the browser whether the amplifier serial link is currently up.
    serial_ok = controller._serial is not None and controller._serial.is_open
    emit("serial_status", {"connected": serial_ok})


def _ws(event: str, data=None):
    """Log an incoming WebSocket event to the monitor."""
    _monitor_emit("WS ↓", event, json.dumps(data) if data else "")


def _source_has_active_zone(source_id: int) -> bool:
    """Return True if at least one powered-on zone is currently using this source."""
    return any(
        st.get("power") and st.get("source") == source_id
        for st in controller.state.values()
    )


def _auto_pause_if_unused(source_id: int):
    """Pause a streaming source when no zone is actively listening to it."""
    if source_id not in _streaming_sources:
        return
    if not _source_has_active_zone(source_id):
        src = _streaming_sources[source_id]
        logger.info("Auto-pausing source %s — no active zones", source_id)
        try:
            src.pause()
        except Exception as exc:
            logger.warning("Auto-pause error for source %s: %s", source_id, exc)


def _nudge_streaming_play(zone: int, delay: float = 3.0):
    """
    After powering on a zone whose source is a streaming service, give the
    player a nudge to start playing.  The delay lets the amplifier finish its
    power-on sequence (PR1 → SS → VO) before we hit the streaming API.

    PlexSource.play() will re-queue the last known playlist if PlexAmp has
    gone idle, so this handles both the 'paused' and 'idle after days' cases.
    """
    source = controller.state[zone].get("source")
    src    = _streaming_sources.get(source)
    if not src:
        return
    def _do():
        # Re-confirm the zone is still on and still on the same source
        # (user might have changed their mind in the intervening seconds)
        st = controller.state[zone]
        if st.get("power") and st.get("source") == source:
            src.play()
    t = threading.Timer(delay, _do)
    t.daemon = True
    t.start()


@socketio.on("set_power")
def on_set_power(data: dict):
    _ws("set_power", data)
    zone = int(data["zone"])
    on   = bool(data["on"])
    source = controller.state[zone].get("source")
    controller.set_power(zone, on)
    if on:
        _nudge_streaming_play(zone)
    elif source in _streaming_sources:
        _auto_pause_if_unused(source)


@socketio.on("set_volume")
def on_set_volume(data: dict):
    _ws("set_volume", data)
    controller.set_volume(int(data["zone"]), int(data["volume"]))


@socketio.on("volume_up")
def on_volume_up(data: dict):
    _ws("volume_up", data)
    controller.volume_up(int(data["zone"]))


@socketio.on("volume_down")
def on_volume_down(data: dict):
    _ws("volume_down", data)
    controller.volume_down(int(data["zone"]))


@socketio.on("set_source")
def on_set_source(data: dict):
    _ws("set_source", data)
    zone       = int(data["zone"])
    source     = int(data["source"])
    old_source = controller.state[zone].get("source")
    controller.set_source(zone, source)
    # If the zone is on and the new source is streaming, nudge it to play.
    if controller.state[zone].get("power") and source in _streaming_sources:
        _streaming_sources[source].play()
    # Pause the old streaming source if nothing else is still using it.
    if old_source != source and old_source in _streaming_sources:
        _auto_pause_if_unused(old_source)


@socketio.on("set_mute")
def on_set_mute(data: dict):
    _ws("set_mute", data)
    controller.set_mute(int(data["zone"]), bool(data["muted"]))


@socketio.on("all_off")
def on_all_off():
    _ws("all_off")
    controller.all_off()
    # All zones off — pause every streaming source.
    for src_id, src in _streaming_sources.items():
        try:
            src.pause()
        except Exception as exc:
            logger.warning("Auto-pause error for source %s: %s", src_id, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    controller.connect()
    # _startup_sync() inside xantech.py queries all zones once the serial
    # port is open; no need to call query_all_zones() here.

    _start_ssdp()

    socketio.run(
        app,
        host="0.0.0.0",
        port=config["system"].get("webuiport", 5000),
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
