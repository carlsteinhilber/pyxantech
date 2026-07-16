#!/usr/bin/env python3
"""
testing-xantech.py — Xantech MRC88 RS-232 protocol test harness
================================================================

Connects directly to the device (no Flask, no SocketIO) and runs a
series of timed command sequences, logging every TX byte and every RX
byte with microsecond timestamps.

Usage
-----
Basic run (all tests, zone 3, ttyUSB0):
    python3 testing-xantech.py

Custom port / zone:
    python3 testing-xantech.py --port /dev/ttyUSB0 --zone 3 --source 5

Capture to file:
    python3 testing-xantech.py 2>&1 | tee xantech-test.log
    python3 testing-xantech.py --logfile xantech-test.log

Run only selected test groups (space-separated):
    python3 testing-xantech.py --tests baseline power_on power_off

Available test groups:
    baseline            query all zones
    power_on            power on, watch settle
    source              source change while on
    volume              volume sweep while on
    mute                mute / unmute while on
    power_off           power off, watch settle
    stress_rapid        PR1 then PR0 with zero delay
    stress_off_delay    PR1 then PR0 after 1/2/3/5/8/12/20 s
    stress_src_immed    PR1 then SS immediately
    blind_window        map exact blind window (REVISED v3: waits for fresh
                        auto-update before each probe for accurate timing;
                        focuses on 300/600/900/1200/1400–2000ms range;
                        VO commands used so zone stays on between probes)
    safe_window         PR0 narrow-window sweep 1500–1950ms (REVISED v3:
                        waits for fresh auto-update before each send;
                        established cycle only; 3 trials per offset)
    no_auto_update      HYPOTHESIS TEST: confirms that without !ZA1+ the device
                        is always receptive and PR0/VO/SS/MU all work on the
                        first try, with no safe-window wait required.
    all                 all of the above (default)

Recommended run for blind-window characterisation:
    python3 testing-xantech.py --tests blind_window safe_window 2>&1 | tee blind-window-test.log
"""

import argparse
import re
import sys
import threading
import time
from datetime import datetime

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed.  Run: pip3 install pyserial", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Logging — all output goes through log() so file and stdout stay in sync
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()
_logfile  = None          # set in main() if --logfile given


def log(msg: str = ""):
    ts   = datetime.now().strftime("%H:%M:%S.%f")
    line = f"{ts}  {msg}"
    with _log_lock:
        print(line, flush=True)
        if _logfile:
            _logfile.write(line + "\n")
            _logfile.flush()


# ---------------------------------------------------------------------------
# Serial reader — runs continuously, logs every line, updates shared state
# ---------------------------------------------------------------------------

_running = True
_rx_buf  = ""

# Keyed by zone number; updated each time a #ZS packet arrives for that zone.
# Used by the blind-window tests to time commands precisely relative to the
# device's last transmission.
_last_rx_time: dict[int, float] = {}
_last_rx_lock = threading.Lock()

# Tracks the most recent volume reported by the device for each zone.
_device_vol: dict[int, int] = {}

# Tracks the most recent power state reported by the device for each zone.
_device_power: dict[int, bool] = {}

# Tracks most recent source and mute reported by the device for each zone.
_device_source: dict[int, int]  = {}
_device_mute:   dict[int, bool] = {}

# Event set each time any #ZS packet arrives (used by wait_for_rx)
_rx_event = threading.Event()


def _parse_zs(line: str):
    """Parse a #ZS line and update shared state dicts."""
    m = re.match(
        r"#(\d+)ZS PR([01]) SS(\d+) VO(\d+) MU([01]) TR\d+ BS\d+ BA\d+ LS[01] PS[01]\+",
        line,
    )
    if m:
        z = int(m.group(1))
        with _last_rx_lock:
            _last_rx_time[z]  = time.time()
            _device_power[z]  = m.group(2) == "1"
            _device_source[z] = int(m.group(3))
            _device_vol[z]    = int(m.group(4))
            _device_mute[z]   = m.group(5) == "1"
        _rx_event.set()
        _rx_event.clear()


def _read_loop(ser: serial.Serial):
    global _rx_buf, _running
    while _running:
        try:
            waiting = ser.in_waiting
            raw     = ser.read(waiting or 1)
        except Exception as exc:
            log(f"RX-ERROR  {exc}")
            break
        if not raw:
            continue
        try:
            _rx_buf += raw.decode("ascii", errors="ignore")
        except Exception:
            continue
        while True:
            found = False
            for sep in ("\r\n", "\r", "\n"):
                if sep in _rx_buf:
                    line, _, _rx_buf = _rx_buf.partition(sep)
                    line = line.strip()
                    if line:
                        log(f"RX  {line}")
                        _parse_zs(line)
                    found = True
                    break
            if not found:
                break


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def tx(ser: serial.Serial, cmd: str):
    log(f"TX  {cmd}")
    ser.write((cmd + "\r").encode("ascii"))


def pause(seconds: float, label: str = ""):
    note = f"--- pause {seconds:.1f}s"
    if label:
        note += f"  [{label}]"
    log(note)
    time.sleep(seconds)


def banner(title: str):
    log()
    log("=" * 70)
    log(f"    {title}")
    log("=" * 70)


def wait_for_zs(zone: int, power: bool, timeout: float = 8.0) -> bool:
    """
    Block until a #ZS packet arrives for zone with the given power state,
    or until timeout.  Returns True on success, False on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _last_rx_lock:
            if zone in _device_power and _device_power[zone] == power:
                return True
        time.sleep(0.05)
    return False


def wait_for_n_zs(zone: int, n: int, timeout: float = 15.0) -> bool:
    """
    Block until n *new* #ZS packets arrive for zone after this call.
    Returns True if n packets were seen within timeout.
    """
    with _last_rx_lock:
        prev = _last_rx_time.get(zone, 0.0)
    count    = 0
    deadline = time.time() + timeout
    while count < n and time.time() < deadline:
        with _last_rx_lock:
            curr = _last_rx_time.get(zone, 0.0)
        if curr > prev:
            count += 1
            prev = curr
        time.sleep(0.02)
    return count >= n


def elapsed_since_last_rx(zone: int) -> float:
    with _last_rx_lock:
        t = _last_rx_time.get(zone, 0.0)
    return time.time() - t


def _power_on_safe(ser: serial.Serial, zone: int, safe_gap_ms: float = 1200.0,
                   max_attempts: int = 3) -> bool:
    """
    Power a zone on by waiting for a safe transmit window (> safe_gap_ms since
    the last #ZS packet) before sending PR1, then confirming via auto-update.

    This avoids the PR0 auto-update blind window (measured at > 726 ms) that
    caused PR1 to be silently dropped in earlier tests.

    Returns True if zone confirmed ON within timeout.
    """
    for attempt in range(1, max_attempts + 1):
        # Wait until the device has been quiet long enough.
        log(f"--- power-on attempt {attempt}/{max_attempts}: "
            f"waiting for >{safe_gap_ms:.0f}ms gap since last #ZS...")
        deadline = time.time() + 30.0
        while time.time() < deadline:
            if elapsed_since_last_rx(zone) * 1000 >= safe_gap_ms:
                break
            time.sleep(0.05)
        else:
            log("--- WARNING: auto-update cycle still active after 30s; sending anyway")

        actual_ms = elapsed_since_last_rx(zone) * 1000
        with _last_rx_lock:
            _device_power.pop(zone, None)
        log(f"TX  !{zone}PR1+  [{actual_ms:.0f}ms since last #ZS — safe window]")
        ser.write((f"!{zone}PR1+\r").encode("ascii"))

        if wait_for_zs(zone, power=True, timeout=6.0):
            return True
        log(f"--- PR1 not confirmed on attempt {attempt}")

    return False


def send_at_offset(ser: serial.Serial, zone: int, cmd: str,
                   target_offset_ms: float) -> float:
    """
    Wait until exactly target_offset_ms have elapsed since the last #ZS
    from zone, then send cmd.  Returns the actual elapsed time (ms) at
    the moment of send.  Polls in a tight loop for precision.
    """
    target_s = target_offset_ms / 1000.0
    while True:
        e = elapsed_since_last_rx(zone)
        if e >= target_s:
            break
        remaining = target_s - e
        if remaining > 0.005:
            time.sleep(remaining - 0.004)   # sleep most of the wait
        # Spin for the last few ms for precision
    actual_ms = elapsed_since_last_rx(zone) * 1000
    log(f"TX  {cmd}  [offset={actual_ms:.1f}ms after last #ZS]")
    ser.write((cmd + "\r").encode("ascii"))
    return actual_ms


# ---------------------------------------------------------------------------
# Individual test routines
# ---------------------------------------------------------------------------

def test_baseline(ser, zone, **_):
    banner("BASELINE — query all zones")
    tx(ser, "!ZA1+")
    pause(0.5, "enable auto-update")
    for z in range(1, 9):
        tx(ser, f"?{z}ZD+")
        time.sleep(0.1)
    pause(3.0, "collect responses")


def test_power_on(ser, zone, **_):
    banner(f"POWER ON — zone {zone}")
    tx(ser, f"!{zone}PR1+")
    pause(12.0, "watch settle — how long do auto-updates run?")


def test_source(ser, zone, source, **_):
    banner(f"SOURCE CHANGE — zone {zone}  →  source {source}")
    tx(ser, f"!{zone}SS{source}+")
    pause(5.0, "collect responses")


def test_volume(ser, zone, **_):
    banner(f"VOLUME SWEEP — zone {zone}")
    for v in [0, 5, 10, 15, 20, 15, 10, 5, 0]:
        tx(ser, f"!{zone}VO{v}+")
        pause(1.5, f"vol={v}")


def test_mute(ser, zone, **_):
    banner(f"MUTE / UNMUTE — zone {zone}")
    tx(ser, f"!{zone}MU1+")
    pause(4.0, "muted")
    tx(ser, f"!{zone}MU0+")
    pause(4.0, "unmuted")


def test_power_off(ser, zone, **_):
    banner(f"POWER OFF — zone {zone}")
    tx(ser, f"!{zone}PR0+")
    pause(12.0, "watch settle — does device confirm PR0?")


def test_stress_rapid(ser, zone, **_):
    banner(f"STRESS — rapid PR1 then PR0 (zero delay) — zone {zone}")
    tx(ser, f"!{zone}PR1+")
    tx(ser, f"!{zone}PR0+")
    pause(12.0, "what does device report?")


def test_stress_off_delay(ser, zone, **_):
    for delay in [1, 2, 3, 5, 8, 12, 20]:
        banner(f"STRESS — PR1 then PR0 after {delay}s — zone {zone}")
        tx(ser, f"!{zone}PR1+")
        pause(delay, f"wait before PR0")
        tx(ser, f"!{zone}PR0+")
        pause(12.0, "collect PR0 response")
        pause(3.0, "cooling-off before next iteration")


def test_stress_src_immed(ser, zone, source, **_):
    banner(f"STRESS — PR1 then immediate SS{source} — zone {zone}")
    tx(ser, f"!{zone}PR1+")
    tx(ser, f"!{zone}SS{source}+")
    pause(12.0, "collect responses")
    tx(ser, f"!{zone}PR0+")
    pause(5.0, "power off after stress test")


def test_blind_window(ser, zone, **_):
    """
    Map the exact blind window boundary — REVISED.

    Previous run showed the safe window is NOT at 650ms but somewhere around
    1650–1900ms.  This version:
      1. Focuses probes on the 1400–2000ms range where the boundary lies.
      2. WAITS FOR A FRESH AUTO-UPDATE before each probe so the timing is
         always relative to the most recent #ZS (fixes the offset drift bug
         in the previous run where probes fired at wrong offsets).
      3. Still uses VO commands (not PR0) so dropped probes leave the zone on.

    After each probe we wait for the device's next #ZS before timing the
    next probe, ensuring consistent and accurate offset measurements.

    Offsets tested (ms): 300, 600, 900, 1200, 1400, 1500, 1550, 1600,
                         1650, 1700, 1750, 1800, 1850, 1900, 1950, 2000
    """
    banner(f"BLIND WINDOW MAPPING — zone {zone}")

    if not _power_on_safe(ser, zone):
        log("ERROR: could not power zone on — aborting blind window test")
        return

    log("--- zone ON; waiting for 3 auto-updates to establish the cycle...")
    if not wait_for_n_zs(zone, n=3, timeout=10.0):
        log("ERROR: did not see 3 established auto-updates — aborting")
        tx(ser, f"!{zone}PR0+")
        pause(5.0, "power off after error")
        return
    log("--- auto-update cycle established")

    # Alternate between two volumes so acceptance is detectable.
    vol_a, vol_b = 8, 15

    # Prime device at vol_a.
    tx(ser, f"!{zone}VO{vol_a}+")
    log("--- waiting 2 auto-updates for vol_a to appear in #ZS...")
    wait_for_n_zs(zone, n=2, timeout=6.0)
    current_vol = _device_vol.get(zone, vol_a)
    log(f"--- device currently reports VO{current_vol}")

    # Sparse coverage of the blind zone + fine-grained coverage of the
    # expected safe window (1400–2000ms).
    offsets_ms = [300, 600, 900, 1200,
                  1400, 1500, 1550, 1600,
                  1650, 1700, 1750, 1800, 1850,
                  1900, 1950, 2000]

    for offset_ms in offsets_ms:
        probe_vol = vol_b if current_vol == vol_a else vol_a

        # KEY FIX: explicitly wait for the NEXT auto-update before starting
        # the timer.  This ensures send_at_offset always times from a fresh
        # packet, giving accurate and repeatable offsets.
        log(f"--- waiting for fresh auto-update to time {offset_ms}ms probe against...")
        if not wait_for_n_zs(zone, n=1, timeout=5.0):
            log("ERROR: auto-updates stopped — aborting")
            break

        actual_ms = send_at_offset(ser, zone, f"!{zone}VO{probe_vol}+", offset_ms)

        # Wait for the next #ZS and read the result.  If the command was
        # accepted, the device resets its timer and the next #ZS carries the
        # new volume.  If dropped, the next #ZS arrives at ~2100ms from the
        # one we timed against.
        if not wait_for_n_zs(zone, n=1, timeout=4.0):
            log(f"--- WARNING: no #ZS received after probe at {actual_ms:.0f}ms")

        reported_vol = _device_vol.get(zone, -1)
        accepted     = (reported_vol == probe_vol)
        result_tag   = "ACCEPTED" if accepted else "DROPPED "
        log(f">>> offset={actual_ms:.0f}ms  sent=VO{probe_vol}  "
            f"device_reported=VO{reported_vol}  {result_tag}")

        if accepted:
            current_vol = probe_vol

    log("--- blind window mapping complete; powering off")
    # Wait for safe window before PR0 so cleanup actually works.
    log("--- waiting for idle or safe window before PR0...")
    deadline = time.time() + 5.0
    while time.time() < deadline:
        e = elapsed_since_last_rx(zone)
        if e > 2.1 or (1.65 <= e <= 1.90):
            break
        time.sleep(0.02)
    tx(ser, f"!{zone}PR0+")
    pause(5.0, "power off")


def test_safe_window(ser, zone, **_):
    """
    Confirm PR0 works in the narrow 1650–1900ms safe window.

    REVISED based on blind_window findings: the accepted VO command was at
    1701ms.  This test now sweeps PR0 specifically through the 1500–2000ms
    range (established cycle only — fresh power-on is handled by retry).

    KEY FIX: waits for a fresh auto-update before each PR0 attempt so the
    offset is timed from a known, just-received #ZS (same fix applied to
    test_blind_window).

    Offsets: 1500, 1550, 1600, 1650, 1700, 1750, 1800, 1850, 1900, 1950ms
    Trials:  3 per offset
    """
    banner(f"SAFE WINDOW — PR0 NARROW SWEEP 1500–1950ms — zone {zone}")

    OFFSETS_MS  = [1500, 1550, 1600, 1650, 1700, 1750, 1800, 1850, 1900, 1950]
    TRIALS_EACH = 3
    results     = {}

    for offset_ms in OFFSETS_MS:
        confirmed = 0
        log(f"\n--- PR0 at {offset_ms}ms (established cycle, {TRIALS_EACH} trials)")

        for trial in range(1, TRIALS_EACH + 1):
            # Power on in safe window.
            if not _power_on_safe(ser, zone):
                log(f"   trial {trial}: ERROR — could not power on")
                pause(5.0, "cooling off")
                continue

            # Wait for 3 auto-updates to establish the cycle.
            log(f"   trial {trial}: waiting for 3 auto-updates...")
            if not wait_for_n_zs(zone, n=3, timeout=10.0):
                log(f"   trial {trial}: ERROR — cycle not established")
                tx(ser, f"!{zone}PR0+")
                pause(5.0, "cooling off")
                continue

            # KEY FIX: wait for a fresh auto-update to time against.
            log(f"   trial {trial}: waiting for fresh #ZS to time {offset_ms}ms from...")
            if not wait_for_n_zs(zone, n=1, timeout=4.0):
                log(f"   trial {trial}: ERROR — no auto-update to time against")
                tx(ser, f"!{zone}PR0+")
                pause(5.0, "cooling off")
                continue

            actual_ms = send_at_offset(ser, zone, f"!{zone}PR0+", offset_ms)

            # Wait for the next #ZS — if PR0 was accepted it will be PR0,
            # if dropped the cycle continues with PR1.
            wait_for_n_zs(zone, n=1, timeout=4.0)
            ok  = not _device_power.get(zone, True)   # True if zone is now OFF
            tag = "CONFIRMED ✓" if ok else "NOT CONFIRMED ✗"
            log(f"   trial {trial}: PR0 at {actual_ms:.0f}ms → {tag}")
            if ok:
                confirmed += 1
            else:
                # Zone is still on; power it off for cooldown.
                # Wait for safe window then PR0.
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    e = elapsed_since_last_rx(zone)
                    if e > 2.1 or (1.65 <= e <= 1.90):
                        break
                    time.sleep(0.02)
                tx(ser, f"!{zone}PR0+")

            pause(5.0, "cooling off")

        results[offset_ms] = confirmed
        log(f">>> {offset_ms}ms: {confirmed}/{TRIALS_EACH} confirmed")

    # ---- SUMMARY ----
    log("\n" + "=" * 60)
    log("  SUMMARY — PR0 narrow window sweep")
    log("=" * 60)
    log(f"  {'Offset':>8}  {'Result':>12}  {'Status'}")
    log(f"  {'-'*8}  {'-'*12}  {'-'*20}")
    for offset_ms in OFFSETS_MS:
        c   = results.get(offset_ms, 0)
        pct = c / TRIALS_EACH
        status = "RELIABLE ✓" if pct == 1.0 else ("PARTIAL" if pct > 0 else "DROPPED ✗")
        log(f"  {offset_ms:>7}ms  {str(c)+'/'+str(TRIALS_EACH):>12}  {status}")
    log("--- safe window PR0 sweep complete")


def query_zone(ser: serial.Serial, zone: int, timeout: float = 1.0) -> bool:
    """
    Send ?{zone}ZD+ and wait up to timeout seconds for the #ZS response.
    Returns True if a #ZS arrived (state dicts updated by _parse_zs).
    """
    with _last_rx_lock:
        prev = _last_rx_time.get(zone, 0.0)
    tx(ser, f"?{zone}ZD+")
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _last_rx_lock:
            if _last_rx_time.get(zone, 0.0) > prev:
                return True
        time.sleep(0.02)
    return False


def test_no_auto_update(ser, zone, source, **_):
    """
    Confirm all commands work immediately when neither !ZA1+ nor !ZP{x}+
    is ever sent.

    HYPOTHESIS: the blind-receive-window is an artifact of the !ZA1+ auto-
    update cycle.  Without it the device is always idle between our own
    query responses, and every command should be accepted on the first try.

    PROTOCOL DOC SUMMARY (page 3):
      !ZA{0/1}+    Zone Activity Auto Update — sends #ZS burst for ~17 s
                   after any state change.  This is what we've been using.
      !ZP{secs}+   Zone Periodic Auto Update — broadcasts all 8 zones every
                   X seconds.  Would have the same blind-window problem.
      ?ZA+, ?ZP+   Query-only: return current enable state, do NOT enable.

    STEPS:
      1. Query ?ZA+ and ?ZP+ to see current device state (no enabling).
      2. PR1 — power on, wait 3 s, query with ?ZD+, confirm ON.
      3. VO20 — send immediately, wait 200 ms, query, confirm VO=20.
      4. VO10 — send immediately, wait 200 ms, query, confirm VO=10.
      5. SS{source} — send, wait 200 ms, query.
      6. MU1 / MU0 — send, wait 200 ms, query each.
      7. PR0 — THE KEY TEST.
           Log elapsed time since last ?ZD+ response (expected ~300-500 ms).
           In the !ZA1+ world this would be mid-blind-window → always dropped.
           Without !ZA1+ it should be confirmed on the first try.

    SUCCESS:  PR0 confirmed within 1 s, on the first attempt, with no retry.
    """
    banner(f"NO-AUTO-UPDATE TEST — zone {zone}")

    # ---- 0. Disable auto-updates (device persists !ZA1+ across sessions) ----
    log("--- Step 0: DISABLING auto-updates (!ZA0+ !ZP0+)")
    log("---   The device stores !ZA1+ in non-volatile memory.")
    log("---   Previous test sessions left it enabled — we must explicitly clear it.")
    tx(ser, "!ZA0+")
    time.sleep(0.3)
    tx(ser, "!ZP0+")
    time.sleep(0.3)
    # Confirm disabled
    tx(ser, "?ZA+")
    time.sleep(0.3)
    tx(ser, "?ZP+")
    pause(2.0, "wait for any in-flight auto-updates to clear")

    # ---- 1. Check device's current auto-update settings ----
    log("--- Step 1: confirming auto-update settings")

    # ---- 2. PR1 ----
    log(f"\n--- Step 2: PR1 (sent immediately, no safe-window wait)")
    with _last_rx_lock:
        _device_power.pop(zone, None)
    t_pr1 = time.time()
    tx(ser, f"!{zone}PR1+")
    log("--- Waiting 3.0 s for zone hardware to power up...")
    time.sleep(3.0)
    pr1_ok = query_zone(ser, zone, timeout=1.0)
    power_on = _device_power.get(zone, False)
    log(f">>> PR1: device reports power={'ON' if power_on else 'OFF'}  "
        f"query_ok={pr1_ok}  elapsed={time.time()-t_pr1:.2f}s  "
        f"{'✓' if power_on else '✗ FAILED'}")

    if not power_on:
        log("--- Cannot continue test without power — aborting")
        tx(ser, f"!{zone}PR0+")
        pause(2.0, "cleanup")
        return

    # ---- 3. VO20 ----
    log(f"\n--- Step 3: VO20 (sent immediately after PR1 confirm)")
    with _last_rx_lock:
        _device_vol.pop(zone, None)
    tx(ser, f"!{zone}VO20+")
    time.sleep(0.2)
    query_zone(ser, zone, timeout=1.0)
    vol = _device_vol.get(zone, -1)
    log(f">>> VO20: device reports VO{vol}  {'✓' if vol == 20 else '✗'}")

    # ---- 4. VO10 ----
    log(f"\n--- Step 4: VO10")
    with _last_rx_lock:
        _device_vol.pop(zone, None)
    tx(ser, f"!{zone}VO10+")
    time.sleep(0.2)
    query_zone(ser, zone, timeout=1.0)
    vol = _device_vol.get(zone, -1)
    log(f">>> VO10: device reports VO{vol}  {'✓' if vol == 10 else '✗'}")

    # ---- 5. SS ----
    log(f"\n--- Step 5: SS{source}")
    with _last_rx_lock:
        _device_source.pop(zone, None)
    tx(ser, f"!{zone}SS{source}+")
    time.sleep(0.2)
    query_zone(ser, zone, timeout=1.0)
    src = _device_source.get(zone, -1)
    log(f">>> SS{source}: device reports SS{src}  {'✓' if src == source else '✗'}")

    # ---- 6. MU1 / MU0 ----
    log(f"\n--- Step 6a: MU1")
    with _last_rx_lock:
        _device_mute.pop(zone, None)
    tx(ser, f"!{zone}MU1+")
    time.sleep(0.2)
    query_zone(ser, zone, timeout=1.0)
    muted = _device_mute.get(zone, None)
    log(f">>> MU1: device reports mute={muted}  {'✓' if muted is True else '✗'}")

    log(f"\n--- Step 6b: MU0")
    with _last_rx_lock:
        _device_mute.pop(zone, None)
    tx(ser, f"!{zone}MU0+")
    time.sleep(0.2)
    query_zone(ser, zone, timeout=1.0)
    muted = _device_mute.get(zone, None)
    log(f">>> MU0: device reports mute={muted}  {'✓' if muted is False else '✗'}")

    # ---- 7. PR0 — THE KEY TEST ----
    gap_ms = elapsed_since_last_rx(zone) * 1000
    log(f"\n--- Step 7: PR0 — KEY TEST")
    log(f"--- Time since last ?ZD+ response: {gap_ms:.0f} ms")
    log(f"--- (In !ZA1+ world this offset would be mid-blind-window → dropped)")
    with _last_rx_lock:
        _device_power.pop(zone, None)
    t_pr0 = time.time()
    tx(ser, f"!{zone}PR0+")
    time.sleep(0.5)
    pr0_query_ok = query_zone(ser, zone, timeout=1.0)
    power_off    = not _device_power.get(zone, True)
    pr0_elapsed  = time.time() - t_pr0
    log(f">>> PR0 attempt 1: device reports power={'OFF' if power_off else 'ON'}  "
        f"elapsed={pr0_elapsed:.2f}s  {'✓ CONFIRMED' if power_off else '✗ NOT CONFIRMED'}")

    if not power_off:
        log("--- PR0 not confirmed — retrying once (unexpected; would mean hypothesis wrong)")
        tx(ser, f"!{zone}PR0+")
        time.sleep(0.5)
        query_zone(ser, zone, timeout=1.0)
        power_off = not _device_power.get(zone, True)
        log(f">>> PR0 retry: {'OFF ✓' if power_off else 'ON ✗'}")

    # ---- Summary ----
    log(f"\n{'='*60}")
    log(f"  NO-AUTO-UPDATE TEST SUMMARY — zone {zone}")
    log(f"{'='*60}")
    log(f"  PR1 confirmed on 1st try:  {'YES ✓' if pr1_ok and power_on else 'NO ✗'}")
    log(f"  VO20 confirmed:            {'YES ✓' if vol == 20 else 'NO ✗ (check above)'}")
    log(f"  SS{source} confirmed:           {'YES ✓' if src == source else 'NO ✗'}")
    log(f"  MU1/MU0 confirmed:         {'YES ✓' if muted is False else 'NO ✗'}")
    log(f"  PR0 confirmed on 1st try:  {'YES ✓' if power_off else 'NO ✗'}")
    if power_off:
        log(f"  --> HYPOTHESIS CONFIRMED: blind window is a !ZA1+ artifact.")
        log(f"      Drop !ZA1+, use ?ZD+ polling.  All commands work immediately.")
    else:
        log(f"  --> HYPOTHESIS WRONG: device is deaf even without !ZA1+.")
        log(f"      Need to investigate further.")
    log(f"{'='*60}")


# ---------------------------------------------------------------------------
# Test registry
# ---------------------------------------------------------------------------

ALL_TESTS = [
    ("baseline",         test_baseline),
    ("power_on",         test_power_on),
    ("source",           test_source),
    ("volume",           test_volume),
    ("mute",             test_mute),
    ("power_off",        test_power_off),
    ("stress_rapid",     test_stress_rapid),
    ("stress_off_delay", test_stress_off_delay),
    ("stress_src_immed", test_stress_src_immed),
    ("blind_window",     test_blind_window),
    ("safe_window",      test_safe_window),
    ("no_auto_update",   test_no_auto_update),
]

TEST_MAP = {name: fn for name, fn in ALL_TESTS}


def cleanup(ser, zone):
    banner("CLEANUP — ensure zone is off")
    tx(ser, f"!{zone}PR0+")
    pause(5.0, "final state")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _running, _logfile

    parser = argparse.ArgumentParser(
        description="Xantech MRC88 RS-232 protocol test harness",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port",    default="/dev/ttyUSB0")
    parser.add_argument("--baud",    type=int, default=9600)
    parser.add_argument("--zone",    type=int, default=3)
    parser.add_argument("--source",  type=int, default=5)
    parser.add_argument("--logfile", default=None)
    parser.add_argument(
        "--tests", nargs="+", default=["all"], metavar="TEST",
        help=f"Choices: {', '.join(TEST_MAP)} (or 'all')"
    )
    args = parser.parse_args()

    if args.logfile:
        try:
            _logfile = open(args.logfile, "w", encoding="utf-8")
        except OSError as exc:
            print(f"Cannot open logfile {args.logfile}: {exc}", file=sys.stderr)
            sys.exit(1)

    if "all" in args.tests:
        tests_to_run = ALL_TESTS
    else:
        unknown = [t for t in args.tests if t not in TEST_MAP]
        if unknown:
            print(f"Unknown test(s): {unknown}.  Valid: {list(TEST_MAP)}", file=sys.stderr)
            sys.exit(1)
        tests_to_run = [(name, TEST_MAP[name]) for name in args.tests]

    log("=" * 70)
    log("  Xantech MRC88 test harness  START")
    log(f"  port={args.port}  baud={args.baud}  zone={args.zone}  source={args.source}")
    log(f"  tests={[n for n, _ in tests_to_run]}")
    log("=" * 70)

    try:
        ser = serial.Serial(
            args.port,
            baudrate=args.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1,
        )
    except serial.SerialException as exc:
        log(f"FATAL: cannot open {args.port}: {exc}")
        sys.exit(1)

    log(f"Serial port opened: {args.port}")

    reader = threading.Thread(target=_read_loop, args=(ser,), daemon=True, name="rx-reader")
    reader.start()

    kwargs = dict(ser=ser, zone=args.zone, source=args.source)

    try:
        for name, fn in tests_to_run:
            fn(**kwargs)
            pause(2.0, "between tests")
    except KeyboardInterrupt:
        log()
        log(">>> Interrupted by user <<<")
    finally:
        cleanup(ser, args.zone)
        _running = False
        time.sleep(0.3)
        ser.close()
        log()
        log("Serial port closed")
        log("=== Xantech MRC88 test harness END ===")
        if _logfile:
            _logfile.close()


if __name__ == "__main__":
    main()
