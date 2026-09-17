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

  /* ---- Rendering ---- */
  function renderRows(tbody, signals, category) {
    if (!signals || signals.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-msg">No ' + category + ' signals</td></tr>';
      return;
    }
    tbody.innerHTML = signals.map(function (s) {
      var rowClass = '';
      if (category === 'suspicious') {
        rowClass = s.count > 3 ? 'row-danger' : 'row-sus';
      }
      return '<tr class="' + rowClass + '" data-signal="' + esc(JSON.stringify(s)) + '">' +
        '<td class="count">' + (s.count > 1 ? 'x' + s.count : '') + '</td>' +
        '<td class="freq">' + fmtFreq(s.freq) + '</td>' +
        '<td class="power">' + fmtPower(s.power) + '</td>' +
        '<td class="std">' + fmtStd(s.std) + '</td>' +
        '<td class="distance">' + fmtDist(s.distance) + '</td>' +
        '<td class="type">' + esc(s.type || '\u2014') + '</td>' +
        '<td class="id" title="' + esc(s.identification || '') + '">' + esc(s.identification || '\u2014') + '</td>' +
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
