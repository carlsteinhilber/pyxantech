// main.js – Xantech MRC88 web UI logic
// CFG is injected by the HTML template as a global before this script loads.

const ZONES          = CFG.zones.filter(z => z.enabled);
const SOURCES        = CFG.sources.filter(s => s.enabled);
const STREAMING_SRCS = CFG.sources.filter(s => s.type === 'streaming' && s.enabled);
const STREAMING_IDS  = new Set(STREAMING_SRCS.map(s => s.source));

// UI-authoritative zone state
const zoneStates = {};
ZONES.forEach(z => {
  zoneStates[z.zone] = {
    power: false,
    volume: z.default_volume ?? 10,
    source: SOURCES[0]?.source ?? 1,
    mute: false,
  };
});

// ── Progress tickers ──────────────────────────────────────────
// When a playing status arrives we store the server's reported position
// plus a local timestamp, then tick every second to extrapolate progress.
// The next server push resyncs automatically, correcting any drift.

const _progressTimers = {};   // sourceId → intervalId
const _progressState  = {};   // sourceId → { posMs, durationMs, receivedAt }
const _lastStatus     = {};   // sourceId → most recent status payload

function _startProgressTick(sourceId) {
  if (_progressTimers[sourceId]) return;   // already running
  _progressTimers[sourceId] = setInterval(() => {
    // Only update the DOM when this streaming tab is actually visible.
    const page = document.getElementById(`page-stream-${sourceId}`);
    if (!page?.classList.contains('active')) return;

    const ps = _progressState[sourceId];
    if (!ps) return;
    const pos = Math.min(ps.posMs + (Date.now() - ps.receivedAt), ps.durationMs);
    const pct = ps.durationMs > 0 ? Math.round((pos / ps.durationMs) * 100) : 0;
    const prog = document.getElementById(`s-prog-${sourceId}`);
    if (prog) prog.value = pct;
    setText(`s-pos-${sourceId}`, fmtTime(pos));
  }, 1000);
}

function _stopProgressTick(sourceId) {
  clearInterval(_progressTimers[sourceId]);
  delete _progressTimers[sourceId];
}

// ── Transport SVGs ────────────────────────────────────────────
// Defined once here; injected into buttons by buildStreamingPage
// and swapped by updateNowPlaying.  data-state on the play/pause
// button ("playing" | "paused") is the toggle source of truth —
// never textContent.

const SVG_PLAY  = `<svg viewBox="0 0 16 16" fill="currentColor"><path d="M2 1v14l12-7z"/></svg>`;
// Pause paths use ~384x512 coordinate space — viewBox corrected accordingly
const SVG_PAUSE = `<svg viewBox="0 0 384 512" fill="currentColor"><path d="M64 96L160 96 160 416 64 416 64 96ZM224 96L320 96 320 416 224 416 224 96Z"/></svg>`;
const SVG_NEXT  = `<svg viewBox="0 0 24 24"><path d="M6 17L14 12L6 7V17Z" fill="currentColor"/><path d="M18 7H15V12V17H18V7Z" fill="currentColor"/></svg>`;
// Rotate inside a <g> so the transform origin is the icon centre
const SVG_PREV  = `<svg viewBox="0 0 24 24"><g transform="rotate(180 12 12)"><path d="M6 17L14 12L6 7V17Z" fill="currentColor"/><path d="M18 7H15V12V17H18V7Z" fill="currentColor"/></g></svg>`;
// Speaker / muted-speaker for the zone mute button
const SVG_SPEAKER = `<svg viewBox="0 0 16 16" fill="currentColor"><path d="M9 4a.5.5 0 0 0-.812-.39L5.825 5.5H3.5a.5.5 0 0 0-.5.5v4a.5.5 0 0 0 .5.5h2.325l2.363 1.89A.5.5 0 0 0 9 12V4zm2.354 1.646a.5.5 0 0 1 0 .708A3 3 0 0 1 11 8a3 3 0 0 1 .354 1.646.5.5 0 0 1-.708-.708A2 2 0 0 0 11 8a2 2 0 0 0-.354-1.646.5.5 0 0 1 .708-.708z"/></svg>`;
const SVG_MUTED  = `<svg viewBox="0 0 16 16" fill="currentColor"><path d="M9 4a.5.5 0 0 0-.812-.39L5.825 5.5H3.5a.5.5 0 0 0-.5.5v4a.5.5 0 0 0 .5.5h2.325l2.363 1.89A.5.5 0 0 0 9 12V4zm3.025 4L13.5 9.475l-.707.707L11.318 8.707 9.843 10.182l-.707-.707L10.611 8 9.136 6.525l.707-.707 1.475 1.475L12.793 5.843l.707.707L12.025 8z"/></svg>`;

// ── Socket.IO ─────────────────────────────────────────────────
const socket = io();

socket.on('connect', () => {
  document.getElementById('conn-badge').classList.add('connected');
  document.getElementById('conn-label').textContent = 'Connected';
});
socket.on('disconnect', () => {
  const badge = document.getElementById('conn-badge');
  badge.classList.remove('connected', 'amp-offline');
  document.getElementById('conn-label').textContent = 'Disconnected';
});

// Amplifier serial connection status (separate from Socket.IO connectivity).
// connected=true  → full green   "Connected"
// connected=false → amber        "Amplifier offline"
socket.on('serial_status', ({ connected }) => {
  const badge = document.getElementById('conn-badge');
  const label = document.getElementById('conn-label');
  if (connected) {
    badge.classList.remove('amp-offline');
    label.textContent = 'Connected';
  } else {
    badge.classList.add('amp-offline');
    label.textContent = 'Amplifier offline';
  }
});

// Server pushes now-playing status every 5 s from its background thread.
// All connected browsers update simultaneously without each polling.
socket.on('streaming_status', ({ source_id, status }) => {
  updateNowPlaying(source_id, status);
});

socket.on('zone_state', ({ zone, state }) => {
  if (!zoneStates[zone]) return;
  zoneStates[zone] = { ...zoneStates[zone], ...state };
  renderZoneCard(zone);
  updateStreamingActiveZones();
});

// ── Build UI ──────────────────────────────────────────────────
function buildUI() {
  const tabBar   = document.getElementById('global-tabs');
  const pagesDiv = document.getElementById('tab-pages');

  const allTabs = [
    { id: 'speakers', label: 'Speakers' },
    ...STREAMING_SRCS.map(s => ({ id: `stream-${s.source}`, label: s.name, sourceId: s.source })),
  ];

  allTabs.forEach((tab, i) => {
    // Tab button
    const btn = document.createElement('button');
    btn.className = 'global-tab' + (i === 0 ? ' active' : '');
    btn.textContent = tab.label;
    btn.dataset.tab = tab.id;
    btn.onclick = () => switchTab(tab.id);
    tabBar.appendChild(btn);

    // Tab page
    const page = document.createElement('div');
    page.className = 'tab-page' + (i === 0 ? ' active' : '');
    page.id = `page-${tab.id}`;

    if (tab.id === 'speakers') {
      const grid = document.createElement('div');
      grid.className = 'zone-grid';
      ZONES.forEach(z => grid.appendChild(buildZoneCard(z)));
      page.appendChild(grid);
    } else {
      page.appendChild(buildStreamingPage(tab.sourceId));
    }

    pagesDiv.appendChild(page);
  });
}

function switchTab(tabId) {
  document.querySelectorAll('.global-tab').forEach(b => b.classList.toggle('active', b.dataset.tab === tabId));
  document.querySelectorAll('.tab-page').forEach(p => p.classList.toggle('active', p.id === `page-${tabId}`));

  // When opening a streaming tab: refresh status immediately and re-fetch
  // the playlist list in case pianoflask/Plex was restarted since page load.
  STREAMING_SRCS.forEach(s => {
    if (tabId === `stream-${s.source}`) {
      fetchStatus(s.source);
      loadPlaylists(s.source, s.name);
    }
  });
}

// ── Zone card ─────────────────────────────────────────────────
function buildZoneCard(zoneCfg) {
  const z   = zoneCfg.zone;
  const el  = document.createElement('div');
  el.className = 'zone-card';
  el.id = `zone-${z}`;
  el.innerHTML = `
    <div class="zone-header">
      <div class="zone-name">${zoneCfg.name}</div>
      <button class="power-btn" id="pwr-${z}" onclick="togglePower(${z})" title="Power on/off">
        <svg viewBox="0 0 24 24"><path d="M18.36 6.64A9 9 0 1 1 5.64 6.64"/><line x1="12" y1="2" x2="12" y2="12"/></svg>
      </button>
    </div>

    <div class="source-row">
      <label class="field-label">Source</label>
      <select class="source-select" id="src-${z}" onchange="setSource(${z}, +this.value)">
        ${SOURCES.map(s => `<option value="${s.source}">${s.name}</option>`).join('')}
      </select>
    </div>

    <label class="field-label">Volume</label>
    <div class="volume-row">
      <button class="vol-btn" onclick="volDown(${z})">−</button>
      <input type="range" class="vol-slider" id="vol-${z}"
             min="0" max="38" value="10"
             oninput="onVolSlider(${z}, +this.value)">
      <button class="vol-btn" onclick="volUp(${z})">+</button>
      <span class="vol-display" id="vdisp-${z}">10</span>
    </div>

    <button class="mute-btn" id="mute-${z}" onclick="toggleMute(${z})">${SVG_SPEAKER} Mute</button>
  `;

  // pointerup fires once when the user releases mouse/touch/stylus.
  // keyup handles arrow-key increments on the slider.
  // Both are more reliable than onchange for range inputs.
  const slider = el.querySelector(`#vol-${z}`);
  const commitVol = () => socket.emit('set_volume', { zone: z, volume: +slider.value });
  slider.addEventListener('pointerup', commitVol);
  slider.addEventListener('keyup',     commitVol);

  return el;
}

function renderZoneCard(z) {
  const st   = zoneStates[z];
  const card = document.getElementById(`zone-${z}`);
  if (!card || !st) return;

  card.classList.toggle('is-on', st.power);
  document.getElementById(`pwr-${z}`).classList.toggle('is-on', st.power);

  const srcEl = document.getElementById(`src-${z}`);
  if (srcEl && +srcEl.value !== st.source) srcEl.value = st.source;

  const slider = document.getElementById(`vol-${z}`);
  if (slider && +slider.value !== st.volume) slider.value = st.volume;
  document.getElementById(`vdisp-${z}`).textContent = st.volume;

  const muteBtn = document.getElementById(`mute-${z}`);
  muteBtn.classList.toggle('is-muted', st.mute);
  // Swap speaker icon to reflect actual mute state
  muteBtn.innerHTML = (st.mute ? SVG_MUTED : SVG_SPEAKER) + ' Mute';
}

// ── Zone controls ─────────────────────────────────────────────
// No optimistic updates — the UI moves only when the server confirms.
function allOff() {
  socket.emit('all_off');
}
function togglePower(zone) {
  socket.emit('set_power', { zone, on: !zoneStates[zone].power });
}
function onVolSlider(zone, value) {
  // oninput: update the number display in real-time while dragging.
  // The actual set_volume emit is sent by the pointerup/keyup listeners
  // added in buildZoneCard, so exactly one command fires per gesture.
  document.getElementById(`vdisp-${zone}`).textContent = value;
}
function volUp(zone) {
  socket.emit('volume_up', { zone });
}
function volDown(zone) {
  socket.emit('volume_down', { zone });
}
function setSource(zone, source) {
  socket.emit('set_source', { zone, source });
}
function toggleMute(zone) {
  socket.emit('set_mute', { zone, muted: !zoneStates[zone].mute });
}

// ── Streaming page ────────────────────────────────────────────
function buildStreamingPage(sourceId) {
  const src  = CFG.sources.find(s => s.source === sourceId);
  const name = src?.name ?? 'Stream';
  const wrap = document.createElement('div');
  wrap.className = 'stream-page';
  wrap.innerHTML = `
    <!-- Which zones are on this source -->
    <div class="active-zones" id="active-zones-${sourceId}">
      <span class="active-zones-label">Active in:</span>
      <span class="no-zones-msg">No zones are using ${name} right now</span>
    </div>

    <!-- Now Playing -->
    <div class="now-playing-card">
      <div class="track-hero">
        <div class="album-art-placeholder" id="art-ph-${sourceId}">♪</div>
        <img class="album-art hidden" id="art-${sourceId}" src="" alt="Album art"
             onerror="this.classList.add('hidden'); document.getElementById('art-ph-${sourceId}').classList.remove('hidden')">
        <div class="track-meta">
          <div class="track-title"  id="s-title-${sourceId}">—</div>
          <div class="track-artist" id="s-artist-${sourceId}">—</div>
          <div class="track-album"  id="s-album-${sourceId}"></div>
        </div>
      </div>

      <div class="transport">
        <button class="transport-btn" onclick="streamCmd(${sourceId},'prev')" title="Previous">${SVG_PREV}</button>
        <button class="transport-btn play-pause" id="s-playpause-${sourceId}"
                onclick="streamCmd(${sourceId},'toggle')" title="Play / Pause"
                data-state="paused">${SVG_PLAY}</button>
        <button class="transport-btn" onclick="streamCmd(${sourceId},'next')" title="Next">${SVG_NEXT}</button>
      </div>

      <div class="progress-row">
        <span id="s-pos-${sourceId}">0:00</span>
        <input type="range" class="progress-slider" id="s-prog-${sourceId}"
               min="0" max="100" value="0" style="pointer-events:none">
        <span id="s-dur-${sourceId}">0:00</span>
      </div>

      <div class="playlist-row">
        <label class="field-label" id="s-playlist-label-${sourceId}">Station / Playlist</label>
        <select class="playlist-select" id="s-playlist-${sourceId}"
                onchange="setPlaylist(${sourceId}, this.value)">
          <option value="">Loading…</option>
        </select>
      </div>
    </div>
  `;

  // Load playlists immediately (doesn't need the tab to be visible)
  loadPlaylists(sourceId, name);
  return wrap;
}

function updateStreamingActiveZones() {
  STREAMING_SRCS.forEach(src => {
    const container = document.getElementById(`active-zones-${src.source}`);
    if (!container) return;

    const activeZones = ZONES.filter(z => {
      const st = zoneStates[z.zone];
      return st && st.source === src.source;
    });

    // Rebuild children after the label; keep first child (label span)
    while (container.children.length > 1) container.removeChild(container.lastChild);

    if (activeZones.length === 0) {
      const msg = document.createElement('span');
      msg.className = 'no-zones-msg';
      msg.textContent = `No zones are using ${src.name} right now`;
      container.appendChild(msg);
    } else {
      activeZones.forEach(z => {
        const pill = document.createElement('span');
        pill.className = 'zone-pill';
        pill.textContent = z.name;
        container.appendChild(pill);
      });
    }
  });
}

// ── Streaming controls ────────────────────────────────────────
async function streamCmd(sourceId, action) {
  if (action === 'toggle') {
    const btn = document.getElementById(`s-playpause-${sourceId}`);
    action = btn?.dataset.state === 'playing' ? 'pause' : 'play';
  }
  await fetch(`/api/streaming/${sourceId}/${action}`, { method: 'POST' });
  setTimeout(() => fetchStatus(sourceId), 500);
}

async function loadPlaylists(sourceId, sourceName) {
  try {
    const r     = await fetch(`/api/streaming/${sourceId}/playlists`);
    const items = await r.json();
    const sel   = document.getElementById(`s-playlist-${sourceId}`);
    const label = document.getElementById(`s-playlist-label-${sourceId}`);
    if (!sel) return;
    // Build new HTML before touching the DOM so the dropdown never
    // flickers to "Loading…" during a tab-switch refresh.
    if (!Array.isArray(items) || items.length === 0) {
      sel.innerHTML = '<option value="">No playlists found</option>';
      return;
    }
    const current = sel.value;
    sel.innerHTML = items.map(p =>
      `<option value="${esc(p.id)}">${esc(p.name)}</option>`
    ).join('');
    // Restore the previously selected item if it still exists.
    if (current && sel.querySelector(`option[value="${CSS.escape(current)}"]`)) {
      sel.value = current;
    }
    if (label) {
      label.textContent = sourceName.toLowerCase().includes('pandora')
        ? 'Station' : 'Playlist';
    }
    // Sync selection to currently playing station/playlist now that the
    // dropdown is fully built.  _lastStatus may already be populated from
    // a status push that arrived while the list was still loading.
    _syncPlaylistSelection(sourceId);
  } catch {
    const sel = document.getElementById(`s-playlist-${sourceId}`);
    if (sel) sel.innerHTML = '<option value="">Unavailable</option>';
  }
}

// Sync the playlist/station dropdown to what is actually playing.
// Called from both updateNowPlaying (data arrives) and loadPlaylists
// (dropdown is rebuilt) so the later of the two always wins the race.
function _syncPlaylistSelection(sourceId) {
  const data = _lastStatus[sourceId];
  if (!data) return;
  const sel = document.getElementById(`s-playlist-${sourceId}`);
  if (!sel || sel.options.length === 0) return;

  // Plex: server tracks the last playlist we queued and returns its ID.
  if (data.current_playlist_id) {
    const id = String(data.current_playlist_id);
    if (sel.querySelector(`option[value="${CSS.escape(id)}"]`)) {
      sel.value = id;
      return;
    }
  }

  // Pandora: /status returns the station name; match against option text.
  if (data.station) {
    for (const opt of sel.options) {
      if (opt.text === data.station) {
        sel.value = opt.value;
        return;
      }
    }
  }
}

async function setPlaylist(sourceId, playlistId) {
  if (!playlistId) return;
  await fetch(`/api/streaming/${sourceId}/playlist`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: playlistId }),
  });
  setTimeout(() => fetchStatus(sourceId), 800);
}

// ── Status fetch ──────────────────────────────────────────────
async function fetchStatus(sourceId) {
  try {
    const r = await fetch(`/api/streaming/${sourceId}/status`);
    if (!r.ok) return;
    updateNowPlaying(sourceId, await r.json());
  } catch { /* network error – skip */ }
}

function updateNowPlaying(sourceId, data) {
  if (!data || typeof data !== 'object') return;

  const card = document.querySelector(`#page-stream-${sourceId} .now-playing-card`);

  // Source is unreachable — show unavailable state and bail out.
  if (data.available === false) {
    _stopProgressTick(sourceId);
    delete _progressState[sourceId];
    if (card) card.classList.add('source-unavailable');
    setText(`s-title-${sourceId}`,  'Source unavailable');
    setText(`s-artist-${sourceId}`, 'Check that the service is running');
    setText(`s-album-${sourceId}`,  '');
    setText(`s-pos-${sourceId}`,    '—');
    setText(`s-dur-${sourceId}`,    '—');
    const prog = document.getElementById(`s-prog-${sourceId}`);
    if (prog) prog.value = 0;
    const btn = document.getElementById(`s-playpause-${sourceId}`);
    if (btn) { btn.innerHTML = SVG_PLAY; btn.dataset.state = 'paused'; }
    return;
  }

  // Source is back — clear unavailable styling if it was set.
  if (card) card.classList.remove('source-unavailable');

  const artEl = document.getElementById(`art-${sourceId}`);
  const artPh = document.getElementById(`art-ph-${sourceId}`);
  if (data.album_art) {
    artEl.src = data.album_art;
    artEl.classList.remove('hidden');
    artPh?.classList.add('hidden');
  } else {
    artEl?.classList.add('hidden');
    artPh?.classList.remove('hidden');
  }

  setText(`s-title-${sourceId}`,  data.title  || '—');
  setText(`s-artist-${sourceId}`, data.artist || '—');
  setText(`s-album-${sourceId}`,  data.album  || '');

  const btn = document.getElementById(`s-playpause-${sourceId}`);
  if (btn) {
    btn.innerHTML = data.playing ? SVG_PAUSE : SVG_PLAY;
    btn.dataset.state = data.playing ? 'playing' : 'paused';
  }

  const durMs = data.duration_ms || 0;
  const posMs = data.position_ms || 0;

  setText(`s-dur-${sourceId}`, fmtTime(durMs));

  // Smooth scrubber: don't snap to the server's position on every push —
  // Plex's viewOffset is jittery enough to cause visible jumps.
  // Keep the locally-interpolated position unless the server is more than
  // 3 s away from what we expected (seek, track change, or first update).
  const ps = _progressState[sourceId];
  const interpolatedPos = (ps && data.playing)
    ? Math.min(ps.posMs + (Date.now() - ps.receivedAt), ps.durationMs)
    : -1;
  const SNAP_MS = 3000;
  const effectivePos = (interpolatedPos < 0 || Math.abs(posMs - interpolatedPos) > SNAP_MS)
    ? posMs
    : interpolatedPos;

  _progressState[sourceId] = { posMs: effectivePos, durationMs: durMs, receivedAt: Date.now() };

  if (data.playing) {
    _startProgressTick(sourceId);
  } else {
    // Paused/stopped: ticker is off so write position directly.
    _stopProgressTick(sourceId);
    const pct = durMs > 0 ? Math.round((posMs / durMs) * 100) : 0;
    const prog = document.getElementById(`s-prog-${sourceId}`);
    if (prog) prog.value = pct;
    setText(`s-pos-${sourceId}`, fmtTime(posMs));
  }

  // Cache the status and sync the playlist dropdown selection.
  // Must run after the dropdown is populated, so _syncPlaylistSelection
  // is also called at the end of loadPlaylists for the race where the
  // dropdown loads after the status push.
  _lastStatus[sourceId] = data;
  _syncPlaylistSelection(sourceId);
}

// ── Helpers ───────────────────────────────────────────────────
function setText(id, txt) { const el = document.getElementById(id); if (el) el.textContent = txt; }
function fmtTime(ms) {
  if (!ms || ms <= 0) return '0:00';
  const s = Math.floor(ms / 1000), m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}
function esc(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Boot ──────────────────────────────────────────────────────
buildUI();
