/**
 * dashboard.js — Smart Optimizer Live Dashboard
 * Handles all SocketIO events from the 3-phase pipeline.
 */

// ── Chart Setup ───────────────────────────────────────────────────────────────
const ctx   = document.getElementById('scoreChart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'Session Score',
        data: [],
        borderColor: '#00d4aa',
        backgroundColor: 'rgba(0,212,170,0.08)',
        borderWidth: 2,
        pointRadius: 4,
        pointHoverRadius: 6,
        pointBackgroundColor: ctx => {
          const v = ctx.raw;
          return (v !== null && v > 0) ? '#00d4aa' : '#ef4444';
        },
        tension: 0.35,
        fill: true,
      },
      {
        label: 'Calmar Ratio',
        data: [],
        borderColor: '#7c6dfa',
        borderWidth: 1.5,
        pointRadius: 2,
        borderDash: [4, 3],
        tension: 0.35,
        fill: false,
      }
    ]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 400 },
    plugins: {
      legend: {
        labels: { color: '#94a3b8', font: { family: 'Inter', size: 11 }, boxWidth: 12 }
      },
      tooltip: {
        backgroundColor: '#141b27',
        borderColor: 'rgba(255,255,255,0.1)',
        borderWidth: 1,
        titleColor: '#e2e8f0',
        bodyColor: '#94a3b8',
        callbacks: {
          label: ctx => {
            if (ctx.dataset.label === 'Session Score') return ` Score: ${(ctx.raw || 0).toFixed(4)}`;
            return ` Calmar: ${(ctx.raw || 0).toFixed(3)}`;
          }
        }
      }
    },
    scales: {
      x: { ticks: { color: '#475569', font: { size: 10 }, maxTicksLimit: 12 }, grid: { color: 'rgba(255,255,255,0.04)' } },
      y: { min: 0, max: 1, ticks: { color: '#475569', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.04)' } }
    }
  }
});

// ── State ─────────────────────────────────────────────────────────────────────
let startTs    = null;
let elapsedTimer = null;
let findingsCount = 0;
let runCount      = 0;
let bestScore     = 0;
let candCount     = 0;
let totalRuns     = 0;

// ── SocketIO ──────────────────────────────────────────────────────────────────
const socket = io();

socket.on('connect', () => addLog('info', '🔗 Connected to optimizer server'));
socket.on('disconnect', () => addLog('warning', '⚡ Disconnected from server'));

// Status sync on connect
socket.on('status_sync', (s) => {
  setStatus(s.state, s.phase);
  if (s.ea_name && s.symbol && s.timeframe) {
    document.getElementById('session-label').textContent =
      `${s.ea_name} · ${s.symbol} · ${s.timeframe}`;
  }
  totalRuns = s.total_runs || 0;
  updateRunCount(s.run_count || 0);
  if (s.state === 'running') {
    document.getElementById('progress-wrap').style.display = '';
    document.getElementById('btn-stop').classList.remove('hidden');
  }
});

socket.on('status_change', (d) => {
  setStatus(d.state, d.phase);
  if (d.state === 'running') {
    startTs = Date.now();
    startElapsedTimer();
    document.getElementById('progress-wrap').style.display = '';
    document.getElementById('btn-stop').classList.remove('hidden');
  }
  if (d.state === 'idle' || d.state === 'stopping') {
    document.getElementById('btn-stop').classList.add('hidden');
  }
});

// Each run completes
socket.on('run_complete', (d) => {
  updateRunCount((d.run_number != null) ? null : runCount + 1, d);

  // Update metrics
  const profit = d.net_profit || 0;
  el('m-profit').textContent  = `$${profit.toFixed(0)}`;
  el('m-profit').className    = 'metric-val ' + (profit >= 0 ? 'profit-pos' : 'profit-neg');
  el('m-calmar').textContent  = (d.calmar || 0).toFixed(3);
  el('m-pf').textContent      = d.profit_factor != null ? (d.profit_factor).toFixed(2) : '—';
  el('m-dd').textContent      = d.max_drawdown != null ? `${d.max_drawdown.toFixed(1)}%` : '—';
  el('m-wr').textContent      = d.win_rate != null ? `${d.win_rate.toFixed(1)}%` : '—';
  el('m-trades').textContent  = d.total_trades != null ? d.total_trades : '—';
  el('current-run-id').textContent = d.run_id || '—';

  // Session score (0–1)
  const score    = d.score || 0;
  const scoreStr = score.toFixed(4);
  el('m-score').textContent = d.passing ? scoreStr : 'failing';
  el('score-bar-fill').style.width = `${Math.round(score * 100)}%`;

  // Update best score header
  if (d.passing && score > bestScore) {
    bestScore = score;
    el('hdr-score').textContent = scoreStr;
  }

  // Add to chart
  const label = d.run_id ? d.run_id.slice(-6) : `${runCount}`;
  chart.data.labels.push(label);
  chart.data.datasets[0].data.push(d.passing ? score : 0);

  // Calmar normalized to 0-1 range (cap at 3)
  const calmarNorm = Math.min(Math.max((d.calmar || 0) / 3.0, 0), 1);
  chart.data.datasets[1].data.push(d.passing ? calmarNorm : 0);

  chart.update('none');
  el('chart-badge').textContent = `${chart.data.labels.length} runs`;

  // Progress bar
  if (d.progress_pct != null) {
    el('progress-bar-fill').style.width = `${d.progress_pct}%`;
  }
  if (d.phase) updatePhaseLabel(d.phase, d.run_number, d.total);
  if (d.budget_summary) el('progress-budget').textContent = d.budget_summary;

  // Add to best configs panel if profitable
  if (d.passing) {
    addCandidate(d);
  }
});

// Phase start
socket.on('phase_start', (d) => {
  const labels = { phase1: 'Phase 1: Broad Discovery', phase2: 'Phase 2: Refinement', phase3: 'Phase 3: Validation' };
  el('progress-phase').textContent = labels[d.phase] || d.phase;
  el('progress-count').textContent = `0 / ${d.total || '?'}`;
  setPhaseActive(d.phase);
  if (d.phase === 'phase2') {
    markPhaseDone('phase1');
    el('phase1-results-card').style.display = '';
  }
  if (d.phase === 'phase3') {
    markPhaseDone('phase2');
  }
});

// Phase 1 complete — show results table
socket.on('phase1_complete', (d) => {
  el('phase1-results-card').style.display = '';
  el('phase1-badge').textContent = `${d.n_passing}/${d.total_tested} profitable`;
  renderPhase1Table(d.top_results || []);
  addLog('info', `━━ Phase 1 done: ${d.n_passing}/${d.total_tested} profitable configs found. Starting refinement...`);
});

// Phase 2 complete
socket.on('phase2_complete', (d) => {
  addLog('success', `✅ Phase 2 done. Best: ${d.best_run_id} · Profit: $${d.best_profit} · Calmar: ${d.best_calmar}`);
});

// No profitable configuration found
socket.on('no_profitable_config', (d) => {
  el('no-profit-banner').style.display = '';
  el('no-profit-msg').textContent = d.msg || 'No profitable configuration found.';
  el('progress-wrap').style.display = 'none';
  addLog('error', '❌ ' + (d.msg || 'No profitable config found'));
});

// Optimization complete — show verdict
socket.on('optimization_complete', (d) => {
  markPhaseDone('phase3');
  markPhaseDone('done');
  setStatus('idle', 'done');
  stopElapsedTimer();

  el('progress-wrap').style.display = 'none';
  el('btn-stop').classList.add('hidden');

  renderVerdict(d);
  addLog(d.verdict === 'RECOMMENDED' ? 'success' : 'warning',
    `🏁 Optimization complete! ${d.verdict} · `+
    `Profit: $${d.net_profit} · Calmar: ${d.calmar} · Runs: ${d.total_runs} · ${d.elapsed_min}min`
  );
});

// Log
socket.on('log', (d) => addLog(d.level, d.msg));

// Error
socket.on('error', (d) => addLog('error', '❌ ' + (d.msg || 'Unknown error')));


// ── Actions ───────────────────────────────────────────────────────────────────

function stopOptimizer() {
  fetch('/api/stop', { method: 'POST' }).catch(() => {});
  addLog('warning', '⏹ Stop requested...');
}

function clearLog() {
  el('log-feed').innerHTML = '';
}


// ── Rendering helpers ─────────────────────────────────────────────────────────

function renderPhase1Table(results) {
  const tbody = el('phase1-tbody');
  tbody.innerHTML = '';
  results.forEach((r, i) => {
    const tr = document.createElement('tr');
    if (i === 0) tr.className = 'rank-1';
    const profitClass = r.net_profit >= 0 ? 'profit-pos' : 'profit-neg';
    const badge = r.passing
      ? `<span class="badge-pass">✓ pass</span>`
      : `<span class="badge-fail">fail</span>`;
    tr.innerHTML = `
      <td>#${r.rank || i+1} ${i === 0 ? '🥇' : ''}</td>
      <td class="${profitClass}">$${(r.net_profit||0).toFixed(0)}</td>
      <td>${(r.calmar||0).toFixed(3)}</td>
      <td>${(r.profit_factor||0).toFixed(2)}</td>
      <td>${(r.win_rate||0).toFixed(1)}%</td>
      <td>${(r.max_drawdown||0).toFixed(1)}%</td>
      <td>${r.total_trades||0}</td>
      <td>${(r.score||0).toFixed(4)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderVerdict(d) {
  const banner = el('verdict-banner');
  const icons  = { RECOMMENDED: '✅', RISKY: '⚠️', NOT_RELIABLE: '❌' };
  const labels = {
    RECOMMENDED:  'Recommended',
    RISKY:        'Risky',
    NOT_RELIABLE: 'Not Reliable',
  };
  const subs = {
    RECOMMENDED:  'Consistent returns with controlled drawdown. Ready to deploy.',
    RISKY:        'Profitable in-sample but shows signs of fragility or OOS degradation. Use with caution.',
    NOT_RELIABLE: 'Results are inconsistent or not profitable enough. Consider different settings.',
  };

  const profitClass = d.net_profit >= 0 ? 'profit-pos' : 'profit-neg';
  const oos = d.oos_profit != null
    ? `<div class="verdict-metric"><div class="verdict-metric-val ${d.oos_profit>=0?'profit-pos':'profit-neg'}" >$${d.oos_profit.toFixed(0)}</div><div class="verdict-metric-lbl">OOS Profit</div></div>`
    : '';

  banner.className = `verdict-banner ${d.verdict}`;
  banner.innerHTML = `
    <div class="verdict-header verdict-${d.verdict}">
      <div class="verdict-icon">${icons[d.verdict] || '?'}</div>
      <div>
        <div class="verdict-title">${labels[d.verdict] || d.verdict}</div>
        <div class="verdict-sub">${subs[d.verdict] || ''}</div>
      </div>
    </div>
    <div class="verdict-metrics">
      <div class="verdict-metric">
        <div class="verdict-metric-val ${profitClass}">$${(d.net_profit||0).toFixed(0)}</div>
        <div class="verdict-metric-lbl">Net Profit</div>
      </div>
      <div class="verdict-metric">
        <div class="verdict-metric-val">${(d.calmar||0).toFixed(2)}</div>
        <div class="verdict-metric-lbl">Calmar</div>
      </div>
      <div class="verdict-metric">
        <div class="verdict-metric-val">${(d.win_rate||0).toFixed(1)}%</div>
        <div class="verdict-metric-lbl">Win Rate</div>
      </div>
      <div class="verdict-metric">
        <div class="verdict-metric-val">${(d.max_drawdown||0).toFixed(1)}%</div>
        <div class="verdict-metric-lbl">Max DD</div>
      </div>
      ${oos}
    </div>
    <div class="verdict-actions">
      ${d.set_file_url ? `<a href="${d.set_file_url}" class="btn-download" download>⬇ Download .set File</a>` : ''}
      <a href="/reports" class="btn-new-run">📁 View Full Report</a>
      <a href="/setup" class="btn-new-run">⚡ New Optimization</a>
    </div>
  `;
  banner.style.display = '';
}

function addCandidate(d) {
  const list = el('candidates-list');
  const empty = list.querySelector('.cand-empty');
  if (empty) empty.remove();

  candCount++;
  el('cand-count').textContent = `${candCount} config${candCount !== 1 ? 's' : ''}`;

  // Keep only top 10
  if (list.children.length >= 10) {
    list.removeChild(list.lastChild);
  }

  const card = document.createElement('div');
  card.className = 'cand-card';
  const profitClass = (d.net_profit||0) >= 0 ? 'profit-pos' : 'profit-neg';
  card.innerHTML = `
    <div class="cand-header">
      <span class="cand-rank">${d.phase?.toUpperCase() || 'RUN'} · Score ${(d.score||0).toFixed(4)}</span>
      <span class="cand-run-id">${d.run_id}</span>
    </div>
    <div class="cand-score-row">
      <div class="cand-metric">
        <div class="cand-metric-lbl">Profit</div>
        <div class="cand-metric-val ${profitClass}">$${(d.net_profit||0).toFixed(0)}</div>
      </div>
      <div class="cand-metric">
        <div class="cand-metric-lbl">Calmar</div>
        <div class="cand-metric-val">${(d.calmar||0).toFixed(3)}</div>
      </div>
      <div class="cand-metric">
        <div class="cand-metric-lbl">Win%</div>
        <div class="cand-metric-val">${(d.win_rate||0).toFixed(1)}%</div>
      </div>
      <div class="cand-metric">
        <div class="cand-metric-lbl">DD%</div>
        <div class="cand-metric-val">${(d.max_drawdown||0).toFixed(1)}%</div>
      </div>
      <div class="cand-metric">
        <div class="cand-metric-lbl">Trades</div>
        <div class="cand-metric-val">${d.total_trades||0}</div>
      </div>
    </div>
  `;
  list.insertBefore(card, list.firstChild);
}

function addLog(level, msg) {
  const feed = el('log-feed');
  const div  = document.createElement('div');
  div.className = `log-line log-${level}`;
  const ts = new Date().toLocaleTimeString('en-GB', { hour12: false });
  div.textContent = `[${ts}] ${msg}`;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
  // Max 200 lines
  while (feed.children.length > 200) feed.removeChild(feed.firstChild);
}

// ── Phase indicator helpers ───────────────────────────────────────────────────

function setPhaseActive(phase) {
  const map = { setup: 'phase-setup', phase1: 'phase-phase1', phase2: 'phase-phase2', phase3: 'phase-phase3' };
  const id  = map[phase];
  if (!id) return;
  document.querySelectorAll('.phase-step').forEach(s => s.classList.remove('active'));
  const step = el(id);
  if (step) step.classList.add('active');
}

function markPhaseDone(phase) {
  const map = { phase1: 'phase-phase1', phase2: 'phase-phase2', phase3: 'phase-phase3', done: 'phase-done' };
  const step = el(map[phase]);
  if (step) { step.classList.remove('active'); step.classList.add('done'); }
}

// ── Status helpers ────────────────────────────────────────────────────────────

function setStatus(state, phase) {
  const dot   = el('state-dot');
  const label = el('state-label');

  const states = {
    running:  { cls: 'dot-running', label: phase ? `${phase.replace('phase','Phase ')}` : 'Running' },
    stopping: { cls: 'dot-warn',    label: 'Stopping...' },
    idle:     { cls: 'dot-idle',    label: 'Ready' },
    done:     { cls: 'dot-done',    label: 'Complete' },
  };
  const s = states[state] || states.idle;
  dot.className   = `dot ${s.cls}`;
  label.textContent = s.label;
}

function updateRunCount(count, d) {
  if (count !== null) runCount = count;
  else if (d) runCount++;
  el('hdr-iter').textContent = totalRuns > 0 ? `${runCount}/${totalRuns}` : runCount;
}

function updatePhaseLabel(phase, runNum, total) {
  const labels = { phase1: 'Phase 1: Broad Search', phase2: 'Phase 2: Refinement', phase3: 'Phase 3: Validation', phase3_oos: 'Phase 3: OOS Test', phase3_sens: 'Phase 3: Sensitivity' };
  el('progress-phase').textContent = labels[phase] || phase;
  if (runNum != null && total != null) {
    el('progress-count').textContent = `${runNum} / ${total}`;
    el('progress-bar-fill').style.width = `${Math.round(runNum / total * 100)}%`;
  }
}

// ── Elapsed timer ─────────────────────────────────────────────────────────────

function startElapsedTimer() {
  if (elapsedTimer) clearInterval(elapsedTimer);
  if (!startTs) startTs = Date.now();
  elapsedTimer = setInterval(() => {
    const secs = Math.floor((Date.now() - startTs) / 1000);
    const mm   = Math.floor(secs / 60).toString().padStart(2, '0');
    const ss   = (secs % 60).toString().padStart(2, '0');
    el('hdr-elapsed').textContent = `${mm}:${ss}`;
  }, 1000);
}

function stopElapsedTimer() {
  if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
}

// ── Utility ───────────────────────────────────────────────────────────────────

function el(id) { return document.getElementById(id); }

// Init: mark Setup phase as active
setPhaseActive('setup');

// Check if run already in progress
fetch('/api/status').then(r => r.json()).then(s => {
  if (s.state === 'running') {
    setStatus(s.state, s.phase);
    startTs = Date.now() - (s.elapsed_s || 0) * 1000;
    startElapsedTimer();
    totalRuns = s.total_runs || 0;
    updateRunCount(s.run_count || 0, null);
    document.getElementById('progress-wrap').style.display = '';
    document.getElementById('btn-stop').classList.remove('hidden');
    setPhaseActive(s.phase);
    if (s.ea_name) {
      document.getElementById('session-label').textContent =
        `${s.ea_name} · ${s.symbol} · ${s.timeframe}`;
    }
  }
}).catch(() => {});
