"""
xantech.py – MRC88 serial controller.

Protocol summary (from MRC88 RS232 Digital Interface doc):
  Commands:  !{z}PR{0/1}+   power on/off
             !{z}SS{s}+      source select (1-8)
             !{z}VO{v}+      volume set (0-38)
             !{z}VI+  !{z}VD+  volume increment/decrement
             !{z}MU{0/1}+   mute on/off
             !ZA0+           disable zone-activity auto-update  ← always sent on connect
             !ZP0+           disable periodic auto-update       ← always sent on connect
  Queries:   ?{z}ZD+         zone data (returns #ZS line immediately)
  Response:  #{z}ZS PR{0/1} SS{s} VO{v} MU{0/1} TR{bt} BS{bt} BA{b} LS{0/1} PS{0/1}+

Device behaviour (empirically established via testing-xantech.py)
-----------------------------------------------------------------
1. AUTO-UPDATES MUST BE DISABLED
   The MRC88 stores !ZA{0/1}+ in non-volatile memory.  When !ZA1+ is
   active, the device silently drops all incoming commands for ~17 s after
   any zone state change ("blind receive window").  We send !ZA0+ + !ZP0+
   as the first bytes on every connection to clear this setting.

   With auto-updates off, every command is acknowledged (OK/ERROR) within
   ~150 ms and confirmed via ?ZD+ within ~300 ms.

2. POWER-ON TIMING
   After !{z}PR1+ the zone hardware takes ~2 s to start.  A ?{z}ZD+ query
   after 3 s reliably confirms PR1 = 1.  After confirmation, desired
   volume/source/mute are applied (_apply_zone_settings).

3. STATE OWNERSHIP
   The MRC88 stores volume/source/mute per zone in its own non-volatile
   memory.  On startup, _startup_sync() queries all zones via ?ZD+ and
   reads the device's stored values directly into self.state — no separate
   state file is needed.  Power always starts as False (zones off).

   After startup, _parse() never modifies self.state — all changes come
   from the public API (set_power, set_volume, set_source, set_mute).

4. VOLUME TRANSIENTS
   The device resets VO to 0 after power-on, after source switches, and
   ramps from ~VO2 on unmute.  _apply_zone_settings(), _source_task(), and
   _mute_off_task() re-push the desired volume after each of these events.
"""

import logging
import os
import re
import threading
import time

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hardware-less simulator
# ---------------------------------------------------------------------------

class _SerialSimulator:
    """Fake serial port for development / testing without the MRC88."""

    def __init__(self):
        self.is_open    = True
        self.in_waiting = 0
        self._buf       = b""
        self._states    = {
            z: {"power": False, "source": 1, "volume": 10, "mute": False}
            for z in range(1, 9)
        }

    def write(self, data: bytes):
        cmd  = data.decode("ascii", errors="ignore").strip()
        resp = self._dispatch(cmd)
        if resp:
            self._buf      += (resp + "\r").encode("ascii")
            self.in_waiting = len(self._buf)

    def read(self, n: int = 1) -> bytes:
        if self._buf:
            chunk, self._buf = self._buf[:n], self._buf[n:]
            self.in_waiting  = len(self._buf)
            return chunk
        time.sleep(0.02)
        return b""

    def close(self):
        self.is_open = False

    def _dispatch(self, cmd: str):
        m = re.match(r"!(\d+)PR([01])\+", cmd)
        if m:
            z, on = int(m.group(1)), m.group(2) == "1"
            if 1 <= z <= 8:
                self._states[z]["power"] = on
                if on:
                    self._states[z]["volume"] = 0   # simulate VO 0 on power-on
            return self._zs(z) if 1 <= z <= 8 else None

        m = re.match(r"!(\d+)VO(\d+)\+", cmd)
        if m:
            z, v = int(m.group(1)), int(m.group(2))
            if 1 <= z <= 8:
                self._states[z]["volume"] = v
            return None

        m = re.match(r"!(\d+)SS(\d+)\+", cmd)
        if m:
            z, s = int(m.group(1)), int(m.group(2))
            if 1 <= z <= 8:
                self._states[z]["source"] = s
            return None

        m = re.match(r"!(\d+)MU([01])\+", cmd)
        if m:
            z, mu = int(m.group(1)), m.group(2) == "1"
            if 1 <= z <= 8:
                self._states[z]["mute"] = mu
            return self._zs(z) if 1 <= z <= 8 else None

        m = re.match(r"\?(\d+)ZD\+", cmd)
        if m:
            z = int(m.group(1))
            return self._zs(z) if 1 <= z <= 8 else None

        if re.match(r"!Z[AP]\d*\+", cmd):
            return None   # ZA0 / ZP0 — no response

        return None

    def _zs(self, z: int) -> str:
        s = self._states[z]
        return (
            f"#{z}ZS PR{1 if s['power'] else 0} SS{s['source']} "
            f"VO{s['volume']} MU{1 if s['mute'] else 0} "
            f"TR7 BS7 BA32 LS0 PS0+"
        )


# ---------------------------------------------------------------------------
# MRC88 controller
# ---------------------------------------------------------------------------

class MRC88Controller:
    """
    Serial controller for the Xantech MRC88.

    Architecture (no auto-updates, UI as source of truth):
    - On connect: !ZA0+ !ZP0+ silence the device.
    - Startup sync: ?{z}ZD+ for all zones; only power state is read from device.
    - All state (volume/source/mute) is owned by the UI and persisted to state.json.
    - Commands use send → delay → ?ZD+ confirm pattern.
    - _device_power[zone] tracks device-confirmed power; used only by retry tasks.
    """

    BAUD = 9600

    def __init__(self, port: str, use_simulator: bool = False, debugging: bool = False):
        self.port          = port
        self.use_simulator = use_simulator
        self.debugging     = debugging
        self.socketio      = None       # injected by app.py after construction

        # Zone state — populated from the device during _startup_sync(), then
        # owned by the UI.  Power always starts False; volume/source/mute are
        # filled in by _parse() as the startup ?ZD+ responses arrive.
        self.state = {
            z: {"power": False, "volume": 10, "source": 1, "mute": False}
            for z in range(1, 9)
        }

        # Device-confirmed power state (updated by _parse; used only by power tasks)
        self._device_power = {z: False for z in range(1, 9)}

        # True while the startup ?ZD+ sync is in progress.  During this window
        # _parse() writes device power into self.state so the UI reflects zones
        # that were left on from a previous session.  After sync completes,
        # _parse() never touches self.state again.
        self._initializing = True

        # Per-zone transmit lock — serialises concurrent commands to same zone
        self._zone_tx_lock = {z: threading.Lock() for z in range(1, 9)}

        # Per-zone debounce timers for volume.  If set_volume is called again
        # before the timer fires, the old timer is cancelled and a new one starts.
        # Only the final value in a rapid sequence actually reaches the device.
        self._vol_timers: dict[int, threading.Timer | None] = {z: None for z in range(1, 9)}

        self._serial  = None
        self._lock    = threading.Lock()    # protects self._serial writes
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_socketio(self, sio):
        self.socketio = sio

    def connect(self):
        self._running = True

        if self.use_simulator:
            logger.info("MRC88: using simulator")
            self._serial = _SerialSimulator()
            threading.Thread(
                target=self._read_loop, daemon=True, name="xantech-reader"
            ).start()
            self._send("!ZA0+")
            self._send("!ZP0+")
            self._emit_serial_status(True)
            # Startup sync runs in background so connect() returns promptly
            threading.Thread(
                target=self._startup_sync, daemon=True, name="xantech-startup"
            ).start()
            return

        threading.Thread(
            target=self._reconnect_loop, daemon=True, name="xantech-reconnect"
        ).start()

    def _reconnect_loop(self):
        import serial as _serial_mod
        _RECONNECT_DELAY = 5

        while self._running:
            ser = None
            try:
                logger.info("MRC88: connecting to /dev/%s …", self.port)
                ser = _serial_mod.Serial(
                    f"/dev/{self.port}",
                    baudrate=self.BAUD,
                    bytesize=_serial_mod.EIGHTBITS,
                    parity=_serial_mod.PARITY_NONE,
                    stopbits=_serial_mod.STOPBITS_ONE,
                    timeout=1,
                )
                with self._lock:
                    self._serial = ser

                logger.info("MRC88: connected on /dev/%s", self.port)

                # Disable auto-updates FIRST — the device persists !ZA1+ across
                # power cycles; previous sessions may have left it enabled.
                self._send("!ZA0+")
                self._send("!ZP0+")
                time.sleep(0.5)   # drain any in-flight packets

                self._emit_serial_status(True)

                reader = threading.Thread(
                    target=self._read_loop, daemon=True, name="xantech-reader"
                )
                reader.start()

                self._startup_sync()   # query all zones; updates power state in UI

                reader.join()
                logger.warning("MRC88: reader thread exited — connection lost")

            except Exception as exc:
                logger.warning("MRC88: connection failed – %s", exc)

            with self._lock:
                if ser and ser.is_open:
                    try:
                        ser.close()
                    except Exception:
                        pass
                self._serial = None

            self._emit_serial_status(False)
            self._initializing = True   # reset for next connection attempt
            for z in range(1, 9):
                self.state[z]["power"] = False
                self._emit_state(z)

            if self._running:
                logger.info("MRC88: retrying in %s s …", _RECONNECT_DELAY)
                time.sleep(_RECONNECT_DELAY)

    def _startup_sync(self):
        """
        Query all zones once to detect zones left on from a previous session.
        During this call _initializing=True so _parse() writes device power
        into self.state.  After queries complete, _initializing is cleared —
        _parse() then becomes a no-op for self.state (UI owns all state).
        """
        logger.info("MRC88: startup sync — querying all zones")
        for z in range(1, 9):
            self._send(f"?{z}ZD+")
            time.sleep(0.15)
        time.sleep(0.5)   # wait for final responses to arrive and be parsed
        self._initializing = False
        logger.info("MRC88: startup sync complete")
        # Emit all zones so the UI reflects current state
        for z in range(1, 9):
            self._emit_state(z)

    def disconnect(self):
        self._running = False
        with self._lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass

    def query_all_zones(self):
        """Send ?ZD+ for every zone (used by Flask on client reconnect)."""
        for z in range(1, 9):
            self._send(f"?{z}ZD+")
            time.sleep(0.1)

    # ------------------------------------------------------------------
    # Control commands (public API)
    # ------------------------------------------------------------------

    def set_power(self, zone: int, on: bool):
        """
        Set zone power.  Updates UI state immediately, then dispatches the
        serial command in a background thread with confirmation.
        """
        self.state[zone]["power"] = on
        self._emit_state(zone)
        target = self._power_on_task if on else self._power_off_task
        threading.Thread(
            target=target, args=(zone,), daemon=True,
            name=f"xantech-pwr-z{zone}"
        ).start()

    def set_volume(self, zone: int, volume: int):
        volume = max(0, min(38, int(volume)))
        self.state[zone]["volume"] = volume
        if self.state[zone]["power"]:
            # Cancel any pending volume command for this zone, then schedule a
            # new one 150 ms out.  If another set_volume arrives before the timer
            # fires, the cycle repeats — so only the final value reaches the device.
            t = self._vol_timers.get(zone)
            if t is not None:
                t.cancel()
            cmd = f"!{zone}VO{volume}+"
            timer = threading.Timer(0.15, self._send_and_query, args=(zone, cmd, 0.2))
            timer.daemon = True
            self._vol_timers[zone] = timer
            timer.start()
        self._emit_state(zone)

    def volume_up(self, zone: int):
        self.set_volume(zone, self.state[zone]["volume"] + 1)

    def volume_down(self, zone: int):
        self.set_volume(zone, self.state[zone]["volume"] - 1)

    def set_source(self, zone: int, source: int):
        source = max(1, min(8, int(source)))
        self.state[zone]["source"] = source
        if self.state[zone]["power"]:
            threading.Thread(
                target=self._source_task,
                args=(zone, source),
                daemon=True, name=f"xantech-src-z{zone}"
            ).start()
        self._emit_state(zone)

    def set_mute(self, zone: int, muted: bool):
        self.state[zone]["mute"] = muted
        if self.state[zone]["power"]:
            target = self._send_and_query if muted else self._mute_off_task
            args   = (zone, f"!{zone}MU1+", 0.2) if muted else (zone,)
            threading.Thread(
                target=target, args=args,
                daemon=True, name=f"xantech-mute-z{zone}"
            ).start()
        self._emit_state(zone)

    def all_off(self):
        self._send("!AO+")
        for z in range(1, 9):
            self.state[z]["power"]  = False
            self._device_power[z]   = False
            self._emit_state(z)

    def get_all_states(self) -> dict:
        return self.state

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _send_and_query(self, zone: int, cmd: str, query_delay: float):
        """Send a command then confirm via ?ZD+ after query_delay seconds."""
        with self._zone_tx_lock[zone]:
            self._send(cmd)
        time.sleep(query_delay)
        self._send(f"?{zone}ZD+")

    def _apply_zone_settings(self, zone: int):
        """
        Push desired VO/SS/MU to device after power-on confirmation.
        The device always reports VO 0 immediately after PR1; this corrects it.
        """
        with self._zone_tx_lock[zone]:
            self._send(f"!{zone}VO{self.state[zone]['volume']}+")
            time.sleep(0.1)
            self._send(f"!{zone}SS{self.state[zone]['source']}+")
            time.sleep(0.1)
            mu = "1" if self.state[zone]["mute"] else "0"
            self._send(f"!{zone}MU{mu}+")
            time.sleep(0.2)
            self._send(f"?{zone}ZD+")   # final confirm (for logging / debugging)

    # ------------------------------------------------------------------
    # Source-switch and unmute tasks (both reset VO; re-push volume after)
    # ------------------------------------------------------------------

    def _source_task(self, zone: int, source: int):
        """
        Send SS then re-push the desired volume.

        The MRC88 resets VO to 0 on every source switch and slowly ramps back
        to its internally saved value for that source (~15 s).  Without the
        re-push the zone would be silent until the ramp completes.  We wait
        500 ms for the transition to settle, then assert our desired volume.
        """
        with self._zone_tx_lock[zone]:
            self._send(f"!{zone}SS{source}+")
        time.sleep(0.5)   # device resets VO to 0 during switch; let it settle
        with self._zone_tx_lock[zone]:
            self._send(f"!{zone}VO{self.state[zone]['volume']}+")
        time.sleep(0.2)
        self._send(f"?{zone}ZD+")

    def _mute_off_task(self, zone: int):
        """
        Send MU0 then re-push the desired volume.
        The MRC88 ramps from near-zero (VO≈2) when unmuting; re-pushing VO
        immediately overrides the ramp so the zone snaps to the correct level.
        """
        with self._zone_tx_lock[zone]:
            self._send(f"!{zone}MU0+")
        time.sleep(0.3)   # device initialises the ramp; let it start before override
        with self._zone_tx_lock[zone]:
            self._send(f"!{zone}VO{self.state[zone]['volume']}+")
        time.sleep(0.2)
        self._send(f"?{zone}ZD+")

    # ------------------------------------------------------------------
    # Power on/off tasks (run in background threads)
    # ------------------------------------------------------------------

    def _power_on_task(self, zone: int):
        """
        Send PR1, wait for hardware power-up (~2 s), confirm via ?ZD+, then
        push the desired volume/source/mute.  Retries up to MAX_RETRIES times.
        """
        MAX_RETRIES     = 5
        POWER_UP_WAIT   = 3.0    # zone hardware takes ~2 s; 3 s gives margin
        CONFIRM_TIMEOUT = 1.5    # ?ZD+ response arrives within ~300 ms

        for attempt in range(1, MAX_RETRIES + 1):
            if not self.state[zone]["power"]:
                return   # user cancelled

            logger.info("Zone %d PR1 attempt %d/%d", zone, attempt, MAX_RETRIES)
            with self._zone_tx_lock[zone]:
                self._send(f"!{zone}PR1+")

            time.sleep(POWER_UP_WAIT)
            self._send(f"?{zone}ZD+")

            deadline = time.time() + CONFIRM_TIMEOUT
            while time.time() < deadline:
                if not self.state[zone]["power"]:
                    return   # user cancelled
                if self._device_power[zone]:
                    logger.info("Zone %d PR1 confirmed", zone)
                    self._apply_zone_settings(zone)
                    return
                time.sleep(0.05)

            logger.warning("Zone %d PR1 not confirmed on attempt %d", zone, attempt)

        logger.error("Zone %d PR1 failed after %d attempts", zone, MAX_RETRIES)
        # Revert optimistic UI state so the card doesn't stay stuck on "on"
        self.state[zone]["power"] = False
        self._emit_state(zone)

    def _power_off_task(self, zone: int):
        """
        Send PR0 and confirm via ?ZD+.  Without !ZA1+, PR0 is accepted
        immediately; this almost always succeeds on the first attempt.
        """
        MAX_RETRIES     = 3
        CONFIRM_WAIT    = 0.5
        CONFIRM_TIMEOUT = 1.0

        for attempt in range(1, MAX_RETRIES + 1):
            if self.state[zone]["power"]:
                return   # user turned it back on

            logger.info("Zone %d PR0 attempt %d/%d", zone, attempt, MAX_RETRIES)
            with self._zone_tx_lock[zone]:
                self._send(f"!{zone}PR0+")

            time.sleep(CONFIRM_WAIT)
            self._send(f"?{zone}ZD+")

            deadline = time.time() + CONFIRM_TIMEOUT
            while time.time() < deadline:
                if self.state[zone]["power"]:
                    return   # user turned it back on
                if not self._device_power[zone]:
                    logger.info("Zone %d PR0 confirmed", zone)
                    return
                time.sleep(0.05)

            logger.warning("Zone %d PR0 not confirmed on attempt %d", zone, attempt)

        logger.error("Zone %d PR0 failed after %d attempts", zone, MAX_RETRIES)
        # Revert optimistic UI state — device never went off
        self.state[zone]["power"] = True
        self._emit_state(zone)

    # ------------------------------------------------------------------
    # Serial send / receive
    # ------------------------------------------------------------------

    def _send(self, cmd: str):
        if self.debugging:
            logger.info("TX: %s", cmd)
        if self._serial and self._serial.is_open:
            try:
                with self._lock:
                    self._serial.write((cmd + "\r").encode("ascii"))
            except Exception as exc:
                logger.warning("MRC88 send error: %s", exc)

    def _emit_serial_status(self, connected: bool):
        if self.socketio:
            self.socketio.emit("serial_status", {"connected": connected})

    def _read_loop(self):
        buf = ""
        while self._running:
            waiting = getattr(self._serial, "in_waiting", 0)
            raw = self._serial.read(waiting or 1)
            if raw:
                try:
                    buf += raw.decode("ascii", errors="ignore")
                    while "\r" in buf or "\n" in buf:
                        for sep in ("\r\n", "\r", "\n"):
                            if sep in buf:
                                line, _, buf = buf.partition(sep)
                                line = line.strip()
                                if line:
                                    if self.debugging:
                                        logger.info("RX: %s", line)
                                    self._parse(line)
                                break
                except Exception as exc:
                    logger.warning("MRC88 parse error: %s", exc)

    def _parse(self, line: str):
        """
        Handle #ZS responses from ?ZD+ queries.

        During startup (_initializing=True):
          - All fields (power, source, volume, mute) are read from the device
            and written into self.state.  The MRC88 stores these in its own
            NVRAM, so the device is the authoritative source on startup.

        After startup (_initializing=False):
          - Only _device_power[z] is updated (used by power tasks for confirmation).
          - self.state is never modified here — all changes come from the public API.
        """
        m = re.match(
            r"#(\d+)ZS PR([01]) SS(\d+) VO(\d+) MU([01]) TR\d+ BS\d+ BA\d+ LS[01] PS[01]\+",
            line,
        )
        if m:
            z = int(m.group(1))
            if not 1 <= z <= 8:
                return

            power_dev = m.group(2) == "1"
            self._device_power[z] = power_dev

            if self._initializing:
                # Startup sync: read all fields from device.
                # Exception: mute is only trusted for powered-ON zones.
                # The MRC88 stores MU1 for every powered-off zone as a
                # hardware artifact of the power-off sequence; that value
                # is meaningless as a user preference and would show all
                # idle zones as muted in the UI.
                self.state[z]["power"]  = power_dev
                self.state[z]["source"] = int(m.group(3))
                self.state[z]["volume"] = int(m.group(4))
                self.state[z]["mute"]   = (m.group(5) == "1") if power_dev else False
                # Emit handled by _startup_sync() after all responses arrive

            # After startup: state is owned by the UI; no changes here.
            # _device_power[z] above is all the power tasks need.
            return

        # Individual power query response (less common)
        m = re.match(r"\?(\d+)PR([01])\+", line)
        if m:
            z = int(m.group(1))
            if 1 <= z <= 8:
                self._device_power[z] = m.group(2) == "1"
                if self._initializing:
                    self.state[z]["power"] = self._device_power[z]
            return

    def _emit_state(self, zone: int):
        if self.socketio:
            self.socketio.emit(
                "zone_state",
                {"zone": zone, "state": self.state[zone]},
            )
