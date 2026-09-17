/* RFLord Dashboard — Client JS */
(function () {
  'use strict';

  /* ---- Helpers ---- */
  function fmtFreq(f) {
    return f != null ? (f / 1e6).toFixed(3) : '\u2014';
  }
  function fmtPower(p) {
    return p != null ? p.toFixed(1) + ' dB' : '\u2014';
  }
  function fmtStd(s) {
    return s != null ? s.toFixed(2) : '\u2014';
  }
  function fmtDist(d) {
    return d != null ? d : '\u2014';
  }
  function esc(s) {
    var d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  }

  /* ---- State ---- */
  var sseSource = null;
  var retryTimer = null;
  var selectedRow = null;
  var lastDangerSignals = {};  // dedup: freq -> timestamp of last toast
  var audioFiles = [];

  /* ======================================================
     WATERFALL CHART — pure Canvas, no external libs
     ====================================================== */
  var waterfall = (function () {
    var canvas, ctx, W, H;
    var COLS = 200;      // frequency bins
    var history = [];     // array of Float32Array rows (power per bin)
    var MAX_ROWS = 80;    // max visible rows

    function init() {
      canvas = document.getElementById('waterfallCanvas');
      if (!canvas) return;
      ctx = canvas.getContext('2d');
      resize();
      window.addEventListener('resize', resize);
      clearCanvas();
    }

    function resize() {
      if (!canvas) return;
      var rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * (window.devicePixelRatio || 1);
      canvas.height = rect.height * (window.devicePixelRatio || 1);
      ctx.scale(window.devicePixelRatio || 1, window.devicePixelRatio || 1);
      W = rect.width;
      H = rect.height;
      MAX_ROWS = Math.floor(H / 2);  // 2px per row
      redraw();
    }

    function clearCanvas() {
      if (!ctx) return;
      ctx.fillStyle = '#0a0a1a';
      ctx.fillRect(0, 0, W, H);
      // Draw grid lines
      ctx.strokeStyle = 'rgba(15,52,96,0.4)';
      ctx.lineWidth = 0.5;
      for (var i = 0; i < 5; i++) {
        var y = Math.floor(H * i / 4) + 0.5;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      }
      for (var j = 0; j < 10; j++) {
        var x = Math.floor(W * j / 9) + 0.5;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, H);
        ctx.stroke();
      }
    }

    /* Map power (dBFS) to a color: blue -> cyan -> green -> yellow -> red */
    function powerToColor(dbfs) {
      // Range: -80 dBFS (quiet) to 0 dBFS (loud)
      var t = Math.max(0, Math.min(1, (dbfs + 80) / 80));
      var r, g, b;
      if (t < 0.25) {
        var s = t / 0.25;
        r = 0; g = Math.floor(s * 100); b = Math.floor(80 + s * 175);
      } else if (t < 0.5) {
        var s = (t - 0.25) / 0.25;
        r = 0; g = Math.floor(100 + s * 155); b = Math.floor(255 - s * 155);
      } else if (t < 0.75) {
        var s = (t - 0.5) / 0.25;
        r = Math.floor(s * 255); g = 255; b = 0;
      } else {
        var s = (t - 0.75) / 0.25;
        r = 255; g = Math.floor(255 - s * 255); b = 0;
      }
      return 'rgb(' + r + ',' + g + ',' + b + ')';
    }

    /* Push a new spectrum row from signal data */
    function pushRow(signals) {
      if (!ctx) return;
      var bins = new Float32Array(COLS);
      // Fill with noise floor
      for (var i = 0; i < COLS; i++) bins[i] = -80;

      // Map each signal into a bin
      var minFreq = 50e6;   // 50 MHz
      var maxFreq = 6000e6; // 6 GHz
      var range = maxFreq - minFreq;

      for (var s = 0; s < signals.length; s++) {
        var sig = signals[s];
        var freq = sig.freq || 0;
        var power = sig.power != null ? sig.power : -60;
        if (freq < minFreq || freq > maxFreq) continue;
        var bin = Math.floor((freq - minFreq) / range * COLS);
        if (bin >= 0 && bin < COLS) {
          // Spread signal across ~3 bins for visibility
          for (var d = -1; d <= 1; d++) {
            var b = bin + d;
            if (b >= 0 && b < COLS) {
              var attenuation = Math.abs(d) * 6; // -6dB per bin offset
              bins[b] = Math.max(bins[b], power - attenuation);
            }
          }
        }
      }

      history.push(bins);
      if (history.length > MAX_ROWS) {
        history.shift();
      }
      redraw();
    }

    function redraw() {
      if (!ctx || history.length === 0) {
        clearCanvas();
        return;
      }

      // Dark background
      ctx.fillStyle = '#0a0a1a';
      ctx.fillRect(0, 0, W, H);

      // Draw grid
      ctx.strokeStyle = 'rgba(15,52,96,0.3)';
      ctx.lineWidth = 0.5;
      for (var g = 1; g < 5; g++) {
        var gy = Math.floor(H * g / 4) + 0.5;
        ctx.beginPath();
        ctx.moveTo(0, gy);
        ctx.lineTo(W, gy);
        ctx.stroke();
      }

      var rowH = Math.max(1, Math.floor(H / MAX_ROWS));
      var colW = W / COLS;
      var offset = MAX_ROWS - history.length;

      for (var r = 0; r < history.length; r++) {
        var row = history[r];
        var y = (r + offset) * rowH;
        for (var c = 0; c < COLS; c++) {
          ctx.fillStyle = powerToColor(row[c]);
          ctx.fillRect(c * colW, y, Math.ceil(colW), rowH);
        }
      }

      // Frequency labels at bottom
      ctx.fillStyle = 'rgba(136,136,136,0.7)';
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      var freqLabels = [100, 500, 1000, 2000, 3000, 4000, 5000];
      for (var f = 0; f < freqLabels.length; f++) {
        var fx = (freqLabels[f] * 1e6 - 50e6) / (6000e6 - 50e6) * W;
        if (fx > 10 && fx < W - 10) {
          ctx.fillText(freqLabels[f] + 'M', fx, H - 2);
        }
      }
    }

    return { init: init, pushRow: pushRow };
  })();


  /* ======================================================
     TOAST NOTIFICATIONS — for ALARM-level signals
     ====================================================== */
  var toasts = (function () {
    var container;
    var MAX_TOASTS = 5;

    function init() {
      container = document.getElementById('toastContainer');
    }

    function show(title, detail, icon) {
      if (!container) return;
      // Limit visible toasts
      while (container.children.length >= MAX_TOASTS) {
        container.removeChild(container.firstChild);
      }

      var toast = document.createElement('div');
      toast.className = 'toast';
      toast.innerHTML =
        '<span class="toast-icon">' + (icon || '\u26a0\ufe0f') + '</span>' +
        '<div class="toast-body">' +
          '<div class="toast-title">' + esc(title) + '</div>' +
          '<div class="toast-detail">' + esc(detail) + '</div>' +
        '</div>' +
        '<span class="toast-time">' + new Date().toLocaleTimeString() + '</span>';

      // Click to dismiss
      toast.addEventListener('click', function () {
        dismiss(toast);
      });

      container.appendChild(toast);

      // Auto-dismiss after 6 seconds
      setTimeout(function () {
        dismiss(toast);
      }, 6000);
    }

    function dismiss(el) {
      if (!el || !el.parentNode) return;
      el.classList.add('toast-exit');
      setTimeout(function () {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 300);
    }

    /* Check signals for ALARM-level and fire toasts */
    function checkAlarms(signals) {
      if (!signals) return;
      var now = Date.now();
      for (var i = 0; i < signals.length; i++) {
        var s = signals[i];
        if (s.classify !== 'danger') continue;
        // Dedup: don't toast same freq more than once per 15s
        var key = String(s.freq);
        if (lastDangerSignals[key] && (now - lastDangerSignals[key]) < 15000) continue;
        lastDangerSignals[key] = now;

        var icon = s.icon || '\u26a0\ufe0f';
        var title = 'ALARM: ' + (s.identification || s.type || 'Unknown Signal');
        var detail = fmtFreq(s.freq) + ' MHz \u2022 ' + fmtPower(s.power);
        if (s.distance) detail += ' \u2022 ' + s.distance;
        show(title, detail, icon);
      }
    }

    return { init: init, show: show, checkAlarms: checkAlarms };
  })();


  /* ======================================================
     AUDIO PLAYER — decoded voice samples
     ====================================================== */
  var audioPlayer = (function () {
    var audioEl, listEl, barEl, nowPlayingEl, countEl;
    var currentFile = null;

    function init() {
      audioEl = document.getElementById('audioElement');
      listEl = document.getElementById('audioList');
      barEl = document.getElementById('audioPlayerBar');
      nowPlayingEl = document.getElementById('audioNowPlaying');
      countEl = document.getElementById('audioCount');
      loadAudioList();
      // Refresh audio list every 30s
      setInterval(loadAudioList, 30000);
    }

    function loadAudioList() {
      fetch('/api/audio')
        .then(function (r) { return r.json(); })
        .then(function (data) {
          audioFiles = data.files || [];
          if (countEl) countEl.textContent = audioFiles.length;
          renderList();
        })
        .catch(function () {});
    }

    function renderList() {
      if (!listEl) return;
      if (audioFiles.length === 0) {
        listEl.innerHTML = '<div class="empty-msg">No audio samples available</div>';
        return;
      }
      listEl.innerHTML = audioFiles.map(function (f) {
        var playing = currentFile === f.name ? ' playing' : '';
        return '<div class="audio-item' + playing + '" data-file="' + esc(f.name) + '">' +
          '<span class="audio-item-icon">' + (currentFile === f.name ? '\u25b6' : '\u25b7') + '</span>' +
          '<span class="audio-item-name">' + esc(f.name) + '</span>' +
          '<span class="audio-item-duration">' + (f.duration || '') + '</span>' +
          '</div>';
      }).join('');
    }

    function play(name) {
      if (!audioEl || !barEl) return;
      currentFile = name;
      audioEl.src = '/api/audio/' + encodeURIComponent(name);
      audioEl.play();
      barEl.style.display = 'flex';
      if (nowPlayingEl) nowPlayingEl.textContent = name;
      renderList();
    }

    function onListClick(ev) {
      var item = ev.target.closest('.audio-item');
      if (!item) return;
      play(item.dataset.file);
    }

    return { init: init, onListClick: onListClick };
  })();


  /* ---- Rendering ---- */
  function renderRows(tbody, signals, category) {
    if (!signals || signals.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">No ' + category + ' signals</td></tr>';
      return;
    }
    tbody.innerHTML = signals.map(function (s) {
      var rowClass = '';
      if (s.classify === 'danger') rowClass = 'row-danger';
      else if (s.classify === 'sus') rowClass = 'row-sus';
      else if (category === 'suspicious') rowClass = 'row-sus';
      var icon = s.icon || '';
      var idText = icon ? icon + ' ' + (s.identification || '') : (s.identification || '\u2014');
      return '<tr class="' + rowClass + '" data-signal="' + esc(JSON.stringify(s)) + '">' +
        '<td class="count">' + (s.count > 1 ? 'x' + s.count : '') + '</td>' +
        '<td class="freq">' + fmtFreq(s.freq) + '</td>' +
        '<td class="power">' + fmtPower(s.power) + '</td>' +
        '<td class="std">' + fmtStd(s.std) + '</td>' +
        '<td class="distance">' + fmtDist(s.distance) + '</td>' +
        '<td class="type">' + esc(s.type || '\u2014') + '</td>' +
        '<td class="id" title="' + esc(s.identification || '') + '">' + esc(idText) + '</td>' +
        '</tr>';
    }).join('');
  }

  function renderSpyRows(events) {
    var tbody = document.getElementById('spyBody');
    var countEl = document.getElementById('spyCount');
    if (!events || events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">No drone/camera events recorded</td></tr>';
      if (countEl) countEl.textContent = '0';
      return;
    }
    if (countEl) countEl.textContent = events.length;
    tbody.innerHTML = events.map(function (e) {
      var tl = e.threat_level != null ? e.threat_level : 3;
      var threatClass = 'threat-' + tl;
      var rowClass = 'threat-row-' + tl;
      var threatLabel = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'][tl] || '?';
      return '<tr class="' + rowClass + '" data-event="' + esc(JSON.stringify(e)) + '">' +
        '<td class="timestamp">' + esc(e.time || '\u2014') + '</td>' +
        '<td class="freq">' + (e.freq_mhz ? e.freq_mhz.toFixed(3) : '\u2014') + '</td>' +
        '<td class="' + threatClass + '">' + threatLabel + '</td>' +
        '<td class="type">' + esc(e.device_name || '\u2014') + '</td>' +
        '<td class="power">' + fmtPower(e.peak_dbfs) + '</td>' +
        '<td class="distance">' + fmtDist(e.distance) + '</td>' +
        '<td class="id">' + esc(e.details || '\u2014') + '</td>' +
        '</tr>';
    }).join('');
  }

  function update(data) {
    var signals = data.signals || [];
    var meta = data.metadata || {};
    var sus = signals.filter(function (s) { return s.category === 'suspicious'; });
    var known = signals.filter(function (s) { return s.category !== 'suspicious'; });

    document.getElementById('susCount').textContent = sus.length;
    document.getElementById('knownCount').textContent = known.length;
    renderRows(document.getElementById('susBody'), sus, 'suspicious');
    renderRows(document.getElementById('knownBody'), known, 'known');

    if (meta.version) document.getElementById('version').textContent = meta.version;
    if (meta.uptime) document.getElementById('uptime').textContent = meta.uptime;
    var alertEl = document.getElementById('alerts');
    if (meta.alert_count != null) alertEl.textContent = meta.alert_count;
    else if (meta.alerts != null) alertEl.textContent = meta.alerts;
    if (meta.device) document.getElementById('device').textContent = meta.device;
    var scanEl = document.getElementById('scanCount');
    if (scanEl && meta.scan_count != null) scanEl.textContent = meta.scan_count;
    var now = new Date().toLocaleTimeString();
    document.getElementById('lastUpdate').textContent = now;
    var footerTime = document.getElementById('lastUpdateFooter');
    if (footerTime) footerTime.textContent = now;

    // Feed waterfall with new signal data
    waterfall.pushRow(signals);

    // Check for ALARM-level signals and fire toasts
    toasts.checkAlarms(signals);
  }

  /* ---- Data loading ---- */
  function loadSpyEvents() {
    fetch('/api/spy?limit=100')
      .then(function (r) { return r.json(); })
      .then(function (data) { renderSpyRows(data.events || []); })
      .catch(function () {});
  }

  function loadInitial() {
    fetch('/api/signals')
      .then(function (r) { return r.json(); })
      .then(function (data) { update(data); })
      .catch(function () {});
  }

  /* ---- SSE ---- */
  function connectSSE() {
    if (sseSource) { sseSource.close(); sseSource = null; }
    if (retryTimer) { clearTimeout(retryTimer); retryTimer = null; }

    setConnStatus('connecting');
    sseSource = new EventSource('/api/stream');

    sseSource.onopen = function () {
      setConnStatus('live');
    };

    sseSource.onmessage = function (e) {
      try {
        update(JSON.parse(e.data));
      } catch (err) {
        console.error('SSE parse error', err);
      }
    };

    sseSource.onerror = function () {
      setConnStatus('offline');
      sseSource.close();
      sseSource = null;
      retryTimer = setTimeout(connectSSE, 3000);
    };
  }

  function setConnStatus(state) {
    var dot = document.getElementById('statusDot');
    var label = document.getElementById('connStatus');
    if (!dot || !label) return;
    dot.className = 'status-dot';
    if (state === 'live') {
      dot.classList.add('live');
      label.textContent = 'Live';
    } else if (state === 'connecting') {
      label.textContent = 'Connecting\u2026';
    } else {
      dot.classList.add('offline');
      label.textContent = 'Disconnected';
    }
  }

  /* ---- Signal Detail Modal ---- */
  function showModal(title, rows) {
    var overlay = document.getElementById('modalOverlay');
    var titleEl = document.getElementById('modalTitle');
    var bodyEl = document.getElementById('modalBody');
    if (!overlay || !titleEl || !bodyEl) return;

    titleEl.textContent = title;
    bodyEl.innerHTML = rows.map(function (r) {
      return '<div class="modal-row">' +
        '<span class="modal-label">' + esc(r.label) + '</span>' +
        '<span class="modal-value ' + (r.cls || '') + '">' + esc(r.value) + '</span>' +
        '</div>';
    }).join('');

    overlay.classList.add('active');
  }

  function hideModal() {
    var overlay = document.getElementById('modalOverlay');
    if (overlay) overlay.classList.remove('active');
    if (selectedRow) {
      selectedRow.classList.remove('selected');
      selectedRow = null;
    }
  }

  function openSignalModal(s) {
    showModal(s.identification || s.type || 'Signal', [
      { label: 'Frequency', value: fmtFreq(s.freq) + ' MHz', cls: 'freq' },
      { label: 'Power', value: fmtPower(s.power), cls: 'power' },
      { label: 'Std Dev', value: fmtStd(s.std) },
      { label: 'Distance', value: fmtDist(s.distance), cls: 'distance' },
      { label: 'Type', value: s.type || '\u2014' },
      { label: 'Count', value: String(s.count || 1) },
      { label: 'Category', value: s.category || '\u2014' },
      { label: 'Band', value: s.band || '\u2014' },
      { label: 'Identification', value: s.identification || '\u2014' },
    ]);
  }

  function openSpyModal(e) {
    var tl = e.threat_level != null ? e.threat_level : 3;
    var threatLabel = ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'][tl] || '?';
    showModal(e.device_name || 'Spy Event', [
      { label: 'Time', value: e.time || '\u2014' },
      { label: 'Frequency', value: e.freq_mhz ? e.freq_mhz.toFixed(3) + ' MHz' : '\u2014', cls: 'freq' },
      { label: 'Threat Level', value: threatLabel + ' (' + tl + ')' },
      { label: 'Device', value: e.device_name || '\u2014' },
      { label: 'Peak Power', value: fmtPower(e.peak_dbfs), cls: 'power' },
      { label: 'Distance', value: fmtDist(e.distance), cls: 'distance' },
      { label: 'Details', value: e.details || '\u2014' },
    ]);
  }

  /* ---- Event delegation for table rows ---- */
  function onTableClick(ev) {
    var tr = ev.target.closest('tr[data-signal], tr[data-event]');
    if (!tr) return;

    /* Highlight */
    if (selectedRow) selectedRow.classList.remove('selected');
    tr.classList.add('selected');
    selectedRow = tr;

    if (tr.dataset.signal) {
      try { openSignalModal(JSON.parse(tr.dataset.signal)); } catch (e) {}
    } else if (tr.dataset.event) {
      try { openSpyModal(JSON.parse(tr.dataset.event)); } catch (e) {}
    }
  }

  /* ---- Touch: swipe-down to dismiss modal ---- */
  var touchStartY = 0;
  function onTouchStart(e) {
    touchStartY = e.touches[0].clientY;
  }
  function onTouchEnd(e) {
    var dy = e.changedTouches[0].clientY - touchStartY;
    if (dy > 60) hideModal();
  }

  /* ---- Init ---- */
  function init() {
    /* Click/tap handlers */
    document.addEventListener('click', function (ev) {
      /* Close modal on overlay click */
      if (ev.target.id === 'modalOverlay') { hideModal(); return; }
      /* Close button */
      if (ev.target.closest('.modal-close')) { hideModal(); return; }
      /* Table row click */
      onTableClick(ev);
      /* Audio list click */
      if (ev.target.closest('.audio-item')) { audioPlayer.onListClick(ev); return; }
    });

    /* Touch swipe to dismiss modal */
    var overlay = document.getElementById('modalOverlay');
    if (overlay) {
      overlay.addEventListener('touchstart', onTouchStart, { passive: true });
      overlay.addEventListener('touchend', onTouchEnd, { passive: true });
    }

    /* Keyboard: Escape closes modal */
    document.addEventListener('keydown', function (ev) {
      if (ev.key === 'Escape') hideModal();
    });

    /* Init subsystems */
    waterfall.init();
    toasts.init();
    audioPlayer.init();

    /* Load initial data then connect SSE */
    loadInitial();
    loadSpyEvents();
    connectSSE();
    setInterval(loadSpyEvents, 60000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();