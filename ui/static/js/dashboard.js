/* dashboard.js — Real-time MT5 Optimizer Dashboard */

// ── Socket.IO connection ───────────────────────────────────────────────────
const socket = io();
let startTime = null;
let timerInterval = null;
let findingsCount = 0;
let candidatesCount = 0;
let currentParams = {};

// ── Chart setup ────────────────────────────────────────────────────────────
const ctx = document.getElementById('scoreChart').getContext('2d');
const scoreChart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'Composite Score',
        data: [],
        borderColor: '#22d3a5',
        backgroundColor: 'rgba(34,211,165,0.08)',
        fill: true,
        tension: 0.4,
        pointRadius: 4,
        pointBackgroundColor: '#22d3a5',
        borderWidth: 2,
      },
      {
        label: 'Calmar Ratio',
        data: [],
        borderColor: '#4f8ef7',
        backgroundColor: 'rgba(79,142,247,0.05)',
        fill: false,
        tension: 0.4,
        pointRadius: 3,
        borderWidth: 1.5,
        borderDash: [4, 2],
      },
    ]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 500 },
    plugins: {
      legend: {
        labels: { color: '#6b7fa3', font: { size: 11, family: 'Inter' } }
      },
      tooltip: {
        backgroundColor: '#111621',
        borderColor: '#2a3a6e',
        borderWidth: 1,
        titleColor: '#e2e8f8',
        bodyColor: '#6b7fa3',
      }
    },
    scales: {
      x: {
        ticks:  { color: '#6b7fa3', font: { size: 10 } },
        grid:   { color: 'rgba(30,39,64,0.6)' },
      },
      y: {
        ticks:  { color: '#6b7fa3', font: { size: 10 } },
        grid:   { color: 'rgba(30,39,64,0.6)' },
        min:    0, max: 1,
      }
    }
  }
});

// ── Controls ───────────────────────────────────────────────────────────────

function startOptimizer() {
  fetch('/api/start', { method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ auto: true })
  });
  startTime = Date.now();
  timerInterval = setInterval(updateTimer, 1000);
  setRunning(true);
  addLog('info', 'Starting optimizer...');
}

function pauseOptimizer() {
  fetch('/api/pause', { method: 'POST' }).then(r => r.json()).then(d => {
    const btn = document.getElementById('btn-pause');
    btn.textContent = d.paused ? '▶ Resume' : '⏸ Pause';
    setDot(d.paused ? 'paused' : 'running', d.paused ? 'Paused' : 'Running...');
  });
}

function stopOptimizer() {
  fetch('/api/stop', { method: 'POST' });
  clearInterval(timerInterval);
  setRunning(false);
  setDot('idle', 'Stopped');
  addLog('warn', 'Optimizer stopped by user.');
}

function clearLog() {
  document.getElementById('log-feed').innerHTML = '';
}

function setRunning(on) {
  document.getElementById('btn-start').classList.toggle('hidden', on);
  document.getElementById('btn-pause').classList.toggle('hidden', !on);
  document.getElementById('btn-stop').classList.toggle('hidden', !on);
  if (on) setDot('running', 'Running...');
}

// ── Timer ──────────────────────────────────────────────────────────────────

function updateTimer() {
  if (!startTime) return;
  const s = Math.floor((Date.now() - startTime) / 1000);
  const m = Math.floor(s / 60);
  const ss = String(s % 60).padStart(2, '0');
  document.getElementById('hdr-elapsed').textContent = `${m}:${ss}`;
}

// ── State dot ──────────────────────────────────────────────────────────────

function setDot(state, label) {
  const dot = document.getElementById('state-dot');
  dot.className = `dot dot-${state}`;
  document.getElementById('state-label').textContent = label;
}

// ── Phase tracker ──────────────────────────────────────────────────────────

const PHASES = ['baseline', 'analyze', 'explore', 'validate', 'oos'];

function setPhase(phase) {
  const idx = PHASES.indexOf(phase);
  PHASES.forEach((p, i) => {
    const el = document.getElementById(`phase-${p}`);
    if (!el) return;
    el.classList.remove('active', 'done');
    if (i < idx) el.classList.add('done');
    else if (i === idx) el.classList.add('active');
  });
}

// ── Metrics update ─────────────────────────────────────────────────────────

function updateMetrics(d) {
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  };
  set('m-profit', d.net_profit !== undefined ? `$${d.net_profit.toLocaleString()}` : '—');
  set('m-calmar', d.calmar !== undefined ? d.calmar.toFixed(3) : '—');
  set('m-pf',     d.profit_factor !== undefined ? d.profit_factor.toFixed(3) : '—');
  set('m-dd',     d.drawdown_pct !== undefined ? `${d.drawdown_pct}%` : '—');
  set('m-wr',     d.win_rate !== undefined ? `${d.win_rate.toFixed(1)}%` : '—');
  set('m-trades', d.total_trades ?? '—');
  set('m-mfe',    d.mfe_capture !== undefined ? `${d.mfe_capture.toFixed(1)}%` : '—');
  set('m-rev',    d.reversal_rate !== undefined ? `${d.reversal_rate.toFixed(1)}%` : '—');
  set('m-score',  d.score !== undefined ? d.score.toFixed(4) : '—');
  set('current-run-id', d.run_id || '—');

  if (d.score !== undefined) {
    const fill = document.getElementById('score-bar-fill');
    if (fill) fill.style.width = `${Math.min(100, d.score * 100)}%`;
    set('hdr-score', d.score.toFixed(4));
  }

  // Color profit
  const pEl = document.getElementById('m-profit');
  if (pEl && d.net_profit !== undefined) {
    pEl.style.color = d.net_profit >= 0 ? 'var(--green)' : 'var(--red)';
  }
}

// ── Findings ───────────────────────────────────────────────────────────────

function addFinding(f) {
  findingsCount++;
  const feed = document.getElementById('findings-feed');
  const empty = feed.querySelector('.finding-empty');
  if (empty) empty.remove();

  const sevClass = { high: 'sev-high', medium: 'sev-medium', low: 'sev-low' }[f.severity] || 'sev-low';

  const row = document.createElement('div');
  row.className = 'finding-row';
  row.innerHTML = `
    <span class="finding-sev ${sevClass}">${f.severity}</span>
    <span class="finding-text">${escHtml(f.description)}</span>
    <span class="finding-conf">${(f.confidence * 100).toFixed(0)}%</span>
  `;
  feed.insertBefore(row, feed.firstChild);

  // Keep max 20 findings visible
  while (feed.children.length > 20) feed.removeChild(feed.lastChild);

  document.getElementById('findings-count').textContent = `${findingsCount} findings`;
}

// ── Hypotheses / Params ────────────────────────────────────────────────────

function showHypotheses(items) {
  if (!items || !items.length) return;
  const h = items[0]; // show first (highest priority)
  document.getElementById('hyp-desc').textContent = h.desc || '';
  document.getElementById('hyp-badge').textContent = `${items.length} hypothesis(es)`;

  const tbody = document.getElementById('params-tbody');
  tbody.innerHTML = '';
  Object.entries(h.delta || {}).forEach(([param, newVal]) => {
    const oldVal = currentParams[param];
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="param-changed">${escHtml(param)}</td>
      <td class="param-old">${oldVal !== undefined ? oldVal : '—'}</td>
      <td class="param-new">${newVal}</td>
    `;
    tbody.appendChild(tr);
  });
}

// ── Log ────────────────────────────────────────────────────────────────────

function addLog(level, msg) {
  const feed = document.getElementById('log-feed');
  const line = document.createElement('div');
  line.className = `log-line log-${level}`;
  const ts = new Date().toLocaleTimeString('en-GB', { hour12: false });
  line.textContent = `[${ts}] ${msg}`;
  feed.insertBefore(line, feed.firstChild);
  while (feed.children.length > 200) feed.removeChild(feed.lastChild);
}

// ── Candidates ─────────────────────────────────────────────────────────────

function addCandidate(d) {
  candidatesCount++;
  const list = document.getElementById('candidates-list');
  const empty = list.querySelector('.cand-empty');
  if (empty) empty.remove();

  const deltaClass = d.delta >= 0 ? 'delta-up' : 'delta-dn';
  const deltaSign  = d.delta >= 0 ? '+' : '';

  const chip = document.createElement('div');
  chip.className = 'candidate-chip';
  chip.innerHTML = `
    <div class="cand-score">${d.score.toFixed(4)}</div>
    <div class="cand-delta ${deltaClass}">${deltaSign}${d.delta.toFixed(4)} vs prev</div>
    <div class="cand-id">ID: ${d.candidate_id}</div>
  `;
  list.insertBefore(chip, list.firstChild);
  document.getElementById('cand-count').textContent = `${candidatesCount} candidates`;
}

// ── Chart update ───────────────────────────────────────────────────────────

function pushChartPoint(d) {
  const labels = scoreChart.data.labels;
  // Label: 'Baseline' for first point, 'Iter N · run_id' for hypothesis runs
  const lbl = d.run_id
    ? (d.run_id.startsWith('baseline') ? 'Baseline' : `It${d.iteration}·${d.run_id.split('_').pop()}`)
    : `Iter ${d.iteration}`;
  labels.push(lbl);
  scoreChart.data.datasets[0].data.push(d.score);
  // Normalize calmar: clamp -0.5..2.0 → 0..1 for display
  const calmarNorm = Math.max(0, Math.min(1, (d.calmar + 0.5) / 2.5));
  scoreChart.data.datasets[1].data.push(calmarNorm);
  if (labels.length > 50) {
    labels.shift();
    scoreChart.data.datasets.forEach(ds => ds.data.shift());
  }
  scoreChart.update('none');
  document.getElementById('chart-badge').textContent = `Score: ${d.score.toFixed(4)}`;
}

// ── Utility ────────────────────────────────────────────────────────────────

function escHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ── Socket events ──────────────────────────────────────────────────────────

socket.on('connect', () => addLog('info', 'Connected to optimizer.'));

socket.on('status_sync', d => {
  document.getElementById('hdr-iter').textContent = d.iteration;
  if (d.state === 'running') { setRunning(true); setDot('running', 'Running...'); }
  if (d.best_score) document.getElementById('hdr-score').textContent = d.best_score.toFixed(4);
});

socket.on('status_change', d => {
  if (d.state === 'running') setDot('running', 'Running...');
  if (d.state === 'paused')  setDot('paused',  'Paused');
  if (d.state === 'idle')    setDot('idle',    'Ready');
  if (d.phase) setPhase(d.phase);
});

socket.on('iteration_start', d => {
  document.getElementById('hdr-iter').textContent = d.iteration;
  addLog('info', `━━ Starting Iteration ${d.iteration} ━━`);
  setPhase('analyze');
});

socket.on('run_started', d => {
  setPhase(d.phase === 'baseline' ? 'baseline' : 'explore');
  document.getElementById('current-run-id').textContent = d.run_id;
  addLog('info', `▶ MT5 started: ${d.run_id} [${d.period}]`);
  currentParams = d.params || {};
});

socket.on('run_complete', d => {
  updateMetrics(d);
  const sign = d.score > 0 ? '\u2713' : '\u2022';
  addLog(d.score > 0.3 ? 'success' : 'info',
    `${sign} ${d.run_id}: Score=${d.score} | Calmar=${d.calmar} | PF=${d.profit_factor} | DD=${d.drawdown_pct}%`
  );
  // Push baseline run to chart immediately (hypothesis runs pushed via score_update)
  if (d.run_id && d.run_id.startsWith('baseline')) {
    pushChartPoint({ iteration: 0, run_id: d.run_id, score: d.score, calmar: d.calmar });
  }
});

socket.on('run_failed', d => {
  addLog('error', `✗ Run failed: ${d.run_id} — ${d.error}`);
});

socket.on('finding', f => addFinding(f));

socket.on('hypotheses', d => {
  setPhase('explore');
  showHypotheses(d.items);
  addLog('info', `💡 ${d.items.length} hypothesis(es) proposed`);
});

socket.on('hypothesis_testing', d => {
  addLog('info', `🧪 Testing H${d.idx}: ${d.desc.substring(0, 60)}...`);
});

socket.on('score_update', d => {
  pushChartPoint(d);
  // Update best score header only when a candidate is actually promoted
  if (d.promoted) {
    document.getElementById('hdr-score').textContent = d.score.toFixed(4);
  }
});

socket.on('candidate_promoted', d => {
  addCandidate(d);
  addLog('success', `🏆 Candidate promoted! Score: ${d.score}`);
});

socket.on('optimization_complete', d => {
  clearInterval(timerInterval);
  setRunning(false);
  setDot('idle', 'Complete');
  addLog('success', `🏁 Done! ${d.iterations} iterations, ${d.candidates} candidate(s), best score: ${d.best_score}`);
  addLog('info', '📁 Reports saved to MT5_Optimizer\\Reports\\');
});

socket.on('error', d => {
  addLog('error', `Error: ${d.msg}`);
  setDot('error', 'Error');
});

socket.on('log', d => addLog(d.level, d.msg));

// ── Init: restore chart from history ──────────────────────────────────────

fetch('/api/history').then(r => r.json()).then(history => {
  history.forEach(d => pushChartPoint(d));
});

fetch('/api/status').then(r => r.json()).then(d => {
  document.getElementById('hdr-iter').textContent = d.iteration;
  if (d.best_score) document.getElementById('hdr-score').textContent = d.best_score.toFixed(4);
  if (d.state === 'running') {
    setRunning(true);
    setDot('running', 'Running...');
    startTime = Date.now() - d.elapsed_s * 1000;
    timerInterval = setInterval(updateTimer, 1000);
  }
});
