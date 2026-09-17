"""RFLord Web Dashboard — Flask + SSE live signal monitor."""

import json
import threading
import time
import os
import sqlite3
from flask import Flask, Response, jsonify, render_template

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RFLord — RF Spectrum Monitor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #1a1a2e;
    color: #eee;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 14px;
    min-height: 100vh;
  }
  header {
    background: #16213e;
    padding: 12px 20px;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 16px;
    border-bottom: 2px solid #0f3460;
  }
  header h1 { font-size: 1.3em; color: #e94560; white-space: nowrap; }
  header .meta { display: flex; gap: 20px; flex-wrap: wrap; font-size: 0.85em; color: #aaa; }
  header .meta span b { color: #eee; }
  .container {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
    padding: 12px;
    max-width: 1800px;
    margin: 0 auto;
  }
  @media (max-width: 900px) {
    .container { grid-template-columns: 1fr; }
  }
  .panel {
    background: #16213e;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid #0f3460;
  }
  .panel-header {
    padding: 10px 14px;
    font-weight: 600;
    font-size: 1em;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .panel-header .count { font-size: 0.8em; opacity: 0.7; }
  .panel.sus .panel-header { background: #3a0a0a; color: #ff6b6b; }
  .panel.known .panel-header { background: #0a3a0a; color: #6bff6b; }
  .panel.spy .panel-header { background: #2a1a00; color: #ffa940; }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left;
    padding: 8px 10px;
    font-size: 0.75em;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #888;
    border-bottom: 1px solid #0f3460;
    position: sticky;
    top: 0;
    background: #16213e;
  }
  td {
    padding: 6px 10px;
    border-bottom: 1px solid #0d1b3e;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 0.85em;
  }
  tr:hover { background: rgba(255,255,255,0.03); }
  .freq { color: #64dfdf; font-weight: 600; }
  .power { color: #ffd166; }
  .std { color: #b8b8b8; }
  .distance { color: #c9b1ff; }
  .type { color: #ff9e7a; }
  .id { color: #eee; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .empty { text-align: center; padding: 30px; color: #555; font-style: italic; }
  .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .status-dot.live { background: #6bff6b; animation: pulse 1.5s infinite; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
  .scroll-wrap { max-height: calc(100vh - 130px); overflow-y: auto; }
  .bottom-row { grid-column: 1 / -1; }
  .timestamp { color: #666; font-size: 0.8em; }
  .threat-0 { color: #ff4444; }
  .threat-1 { color: #ff8844; }
  .threat-2 { color: #ffcc44; }
</style>
</head>
<body>
<header>
  <h1>&#128225; RFLord</h1>
  <div class="meta">
    <span><span class="status-dot live" id="statusDot"></span> <b id="connStatus">Connecting…</b></span>
    <span>Version: <b id="version">—</b></span>
    <span>Uptime: <b id="uptime">—</b></span>
    <span>Alerts: <b id="alerts" style="color:#ff6b6b">0</b></span>
    <span>Last update: <b id="lastUpdate">—</b></span>
  </div>
</header>
<div class="container">
  <div class="panel sus">
    <div class="panel-header">
      &#9888; Suspicious Signals <span class="count" id="susCount">0</span>
    </div>
    <div class="scroll-wrap">
      <table>
        <thead><tr><th>Cnt</th><th>Freq (MHz)</th><th>Power</th><th>Std</th><th>Dist</th><th>Type</th><th>Identification</th></tr></thead>
        <tbody id="susBody"><tr><td colspan="7" class="empty">No suspicious signals</td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="panel known">
    <div class="panel-header">
      &#10003; Known Signals <span class="count" id="knownCount">0</span>
    </div>
    <div class="scroll-wrap">
      <table>
        <thead><tr><th>Cnt</th><th>Freq (MHz)</th><th>Power</th><th>Std</th><th>Dist</th><th>Type</th><th>Identification</th></tr></thead>
        <tbody id="knownBody"><tr><td colspan="7" class="empty">No known signals</td></tr></tbody>
      </table>
    </div>
  </div>
  <div class="panel spy bottom-row">
    <div class="panel-header">
      &#128274; Drone / Hidden Camera Events (30 days) <span class="count" id="spyCount">0</span>
    </div>
    <div class="scroll-wrap" style="max-height: 300px;">
      <table>
        <thead><tr><th>Time</th><th>Freq (MHz)</th><th>Threat</th><th>Device</th><th>Power</th><th>Dist</th><th>Details</th></tr></thead>
        <tbody id="spyBody"><tr><td colspan="7" class="empty">No drone/camera events recorded</td></tr></tbody>
      </table>
    </div>
  </div>
</div>
<script>
function fmtFreq(f) { return (f / 1e6).toFixed(3); }
function fmtPower(p) { return (p != null) ? p.toFixed(1) + ' dB' : '—'; }
function fmtStd(s) { return (s != null) ? s.toFixed(2) : '—'; }
function fmtDist(d) { return (d != null) ? d : '—'; }

function renderRows(tbody, signals) {
  if (!signals || signals.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No signals detected</td></tr>';
    return;
  }
  tbody.innerHTML = signals.map(s => `<tr>
    <td class="count">${s.count > 1 ? 'x' + s.count : ''}</td>
    <td class="freq">${fmtFreq(s.freq)}</td>
    <td class="power">${fmtPower(s.power)}</td>
    <td class="std">${fmtStd(s.std)}</td>
    <td class="distance">${fmtDist(s.distance)}</td>
    <td class="type">${s.type || '—'}</td>
    <td class="id" title="${(s.identification||'').replace(/"/g,'&quot;')}">${s.identification || '—'}</td>
  </tr>`).join('');
}

function renderSpyRows(events) {
  const tbody = document.getElementById('spyBody');
  if (!events || events.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No drone/camera events recorded</td></tr>';
    return;
  }
  document.getElementById('spyCount').textContent = events.length;
  tbody.innerHTML = events.map(e => {
    const threatClass = 'threat-' + (e.threat_level != null ? e.threat_level : 3);
    const threatLabel = ['CRITICAL','HIGH','MEDIUM','LOW'][e.threat_level] || '?';
    return `<tr>
      <td class="timestamp">${e.time || '—'}</td>
      <td class="freq">${e.freq_mhz ? e.freq_mhz.toFixed(3) : '—'}</td>
      <td class="${threatClass}">${threatLabel}</td>
      <td class="type">${e.device_name || '—'}</td>
      <td class="power">${fmtPower(e.peak_dbfs)}</td>
      <td class="distance">${fmtDist(e.distance)}</td>
      <td class="id">${e.details || '—'}</td>
    </tr>`;
  }).join('');
}

function update(data) {
  const signals = data.signals || [];
  const meta = data.metadata || {};
  const sus = signals.filter(s => s.category === 'suspicious');
  const known = signals.filter(s => s.category !== 'suspicious');

  document.getElementById('susCount').textContent = sus.length;
  document.getElementById('knownCount').textContent = known.length;
  renderRows(document.getElementById('susBody'), sus);
  renderRows(document.getElementById('knownBody'), known);

  if (meta.version) document.getElementById('version').textContent = meta.version;
  if (meta.uptime) document.getElementById('uptime').textContent = meta.uptime;
  if (meta.alert_count != null) document.getElementById('alerts').textContent = meta.alert_count;
  else if (meta.alerts != null) document.getElementById('alerts').textContent = meta.alerts;
  document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
}

function loadSpyEvents() {
  fetch('/api/spy?limit=100')
    .then(r => r.json())
    .then(data => renderSpyRows(data.events || []))
    .catch(() => {});
}

function connectSSE() {
  const es = new EventSource('/api/stream');
  document.getElementById('connStatus').textContent = 'Live';
  document.getElementById('statusDot').className = 'status-dot live';

  es.onmessage = (e) => {
    try { update(JSON.parse(e.data)); } catch(err) { console.error('SSE parse error', err); }
  };
  es.onerror = () => {
    document.getElementById('connStatus').textContent = 'Reconnecting…';
    document.getElementById('statusDot').className = 'status-dot';
    es.close();
    setTimeout(connectSSE, 3000);
  };
}

connectSSE();
loadSpyEvents();
setInterval(loadSpyEvents, 60000);
</script>
</body>
</html>"""


class SpyEventDB:
    """SQLite storage for drone/hidden camera detection events. 30-day retention."""

    def __init__(self, db_path=None):
        self.db_path = db_path or os.path.expanduser(
            "~/.local/share/rflord/spy_events.db"
        )
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def init_db(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS spy_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                freq_mhz REAL NOT NULL,
                device_name TEXT,
                threat_level INTEGER,
                peak_dbfs REAL,
                distance TEXT,
                details TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_spy_ts ON spy_events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_spy_freq ON spy_events(freq_mhz);
        """)
        self.conn.commit()

    def record_event(self, freq_mhz, device_name, threat_level,
                     peak_dbfs=None, distance=None, details=None):
        """Record a drone/camera detection event."""
        self.conn.execute(
            """INSERT INTO spy_events
               (timestamp, freq_mhz, device_name, threat_level, peak_dbfs, distance, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (time.time(), freq_mhz, device_name, threat_level,
             peak_dbfs, distance, details),
        )
        self.conn.commit()

    def get_recent(self, limit=100, days=30):
        """Get recent events within the last N days."""
        cutoff = time.time() - days * 86400
        rows = self.conn.execute(
            """SELECT * FROM spy_events WHERE timestamp >= ?
               ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup(self, max_days=30):
        """Delete events older than max_days."""
        cutoff = time.time() - max_days * 86400
        self.conn.execute("DELETE FROM spy_events WHERE timestamp < ?", (cutoff,))
        self.conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


class WebDashboard:
    """Optional Flask + SSE web dashboard for RFLord."""

    def __init__(self, port: int = 8080):
        self.port = port
        self._signals: list[dict] = []
        self._metadata: dict = {}
        self._lock = threading.Lock()
        self._version = 0
        self._thread: threading.Thread | None = None
        # Detect template/static dirs relative to web.py
        _dir = os.path.dirname(os.path.abspath(__file__))
        _tpl = os.path.join(_dir, 'templates')
        _static = os.path.join(_dir, 'static')
        self._has_templates = os.path.isdir(_tpl) and os.path.isfile(os.path.join(_tpl, 'index.html'))
        self._app = Flask(
            __name__,
            static_folder=_static if os.path.isdir(_static) else None,
            template_folder=_tpl if self._has_templates else None,
        )
        self._app.logger.setLevel("WARNING")
        self._running = False
        self._spy_db = SpyEventDB()
        self._spy_db.init_db()
        self._setup_routes()

    def _setup_routes(self):
        app = self._app

        @app.route("/")
        def index():
            if self._has_templates:
                return render_template("index.html")
            return Response(DASHBOARD_HTML, content_type="text/html")

        @app.route("/api/signals")
        def api_signals():
            with self._lock:
                return {"signals": list(self._signals), "metadata": dict(self._metadata)}

        @app.route("/api/spy")
        def api_spy():
            from flask import request
            limit = request.args.get("limit", 100, type=int)
            days = request.args.get("days", 30, type=int)
            events = self._spy_db.get_recent(limit=limit, days=days)
            # Convert timestamps to readable strings
            for e in events:
                if "timestamp" in e:
                    e["time"] = time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(e["timestamp"])
                    )
            return {"events": events}

        @app.route("/api/stream")
        def api_stream():
            def generate():
                last_version = 0
                while True:
                    time.sleep(2)
                    with self._lock:
                        version = self._version
                        if version != last_version:
                            data = json.dumps({
                                "signals": list(self._signals),
                                "metadata": dict(self._metadata),
                            })
                            last_version = version
                            yield f"data: {data}\n\n"
                        else:
                            yield ": keepalive\n\n"

            return Response(
                generate(),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

    def update_signals(self, signals: list[dict], metadata: dict) -> None:
        """Called by the main curses loop each scan cycle."""
        with self._lock:
            self._signals = list(signals)
            self._metadata = dict(metadata)
            self._version += 1

    def record_spy_event(self, freq_mhz, device_name, threat_level,
                         peak_dbfs=None, distance=None, details=None):
        """Record a drone/camera detection event to persistent storage."""
        self._spy_db.record_event(
            freq_mhz, device_name, threat_level,
            peak_dbfs, distance, details,
        )

    def cleanup_spy_events(self, max_days=30):
        """Remove spy events older than max_days."""
        self._spy_db.cleanup(max_days)

    def start(self) -> None:
        """Run Flask in a daemon background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        def run():
            import io, contextlib
            # Suppress Flask startup banner (* Serving Flask app, * Debug mode)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self._app.run(host="0.0.0.0", port=self.port, threaded=True, use_reloader=False)

        self._thread = threading.Thread(target=run, daemon=True, name="rflord-web")
        self._thread.start()

    def stop(self) -> None:
        """Signal the server to stop (daemon thread dies with the process)."""
        self._running = False
