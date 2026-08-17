"""RFLord Web Dashboard — Flask + SSE live signal monitor."""

import json
import threading
import time
from flask import Flask, Response

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
  document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
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
</script>
</body>
</html>"""


class WebDashboard:
    """Optional Flask + SSE web dashboard for RFLord."""

    def __init__(self, port: int = 8080):
        self.port = port
        self._signals: list[dict] = []
        self._metadata: dict = {}
        self._lock = threading.Lock()
        self._sse_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._app = Flask(__name__)
        self._app.logger.setLevel("WARNING")
        self._running = False
        self._setup_routes()

    def _setup_routes(self):
        app = self._app

        @app.route("/")
        def index():
            return Response(DASHBOARD_HTML, content_type="text/html")

        @app.route("/api/signals")
        def api_signals():
            with self._lock:
                return {"signals": list(self._signals), "metadata": dict(self._metadata)}

        @app.route("/api/stream")
        def api_stream():
            def generate():
                last_id = -1
                while True:
                    self._sse_event.wait(timeout=5.0)
                    self._sse_event.clear()
                    with self._lock:
                        current_id = id(self._signals)
                        data = json.dumps({
                            "signals": list(self._signals),
                            "metadata": dict(self._metadata),
                        })
                    if current_id != last_id:
                        last_id = current_id
                        yield f"data: {data}\n\n"
                    else:
                        yield ": keepalive\n\n"

            return Response(generate(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    def update_signals(self, signals: list[dict], metadata: dict) -> None:
        """Called by the main curses loop each scan cycle."""
        with self._lock:
            self._signals = list(signals)
            self._metadata = dict(metadata)
        self._sse_event.set()

    def start(self) -> None:
        """Run Flask in a daemon background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        import logging
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)

        def run():
            self._app.run(host="0.0.0.0", port=self.port, threaded=True, use_reloader=False)

        self._thread = threading.Thread(target=run, daemon=True, name="rflord-web")
        self._thread.start()

    def stop(self) -> None:
        """Signal the server to stop (daemon thread dies with the process)."""
        self._running = False
        self._sse_event.set()  # unblock any waiting SSE
