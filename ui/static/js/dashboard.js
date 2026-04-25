/**
 * dashboard.js — APEX AI-Powered EA Optimizer
 * Handles all SocketIO events, chart rendering, and UI state.
 */

'use strict';

/* ══════════════════════════════════════════════════════════════════════
   UTILITIES
══════════════════════════════════════════════════════════════════════ */

function el(id) { return document.getElementById(id); }

function fmtMoney(v) {
  const n = parseFloat(v) || 0;
  const abs = Math.abs(n);
  let s;
  if (abs >= 1000000) s = (n / 1000000).toFixed(2) + 'M';
  else if (abs >= 1000) s = (n / 1000).toFixed(1) + 'k';
  else s = n.toFixed(2);
  return (n >= 0 ? '$' : '-$') + (n < 0 ? s.replace('-', '') : s);
}

function fmtPct(v, decimals) {
  return (parseFloat(v) || 0).toFixed(decimals != null ? decimals : 2) + '%';
}

function fmtTime(secs) {
  const s = Math.floor(secs);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return h + 'h ' + String(m).padStart(2, '0') + 'm';
  return String(m).padStart(2, '0') + ':' + String(sec).padStart(2, '0');
}

function nowStr() {
  return new Date().toLocaleTimeString('en-US', { hour12: false });
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function safe(id, fn) {
  const e = el(id);
  if (e) fn(e);
}

/* ══════════════════════════════════════════════════════════════════════
   GLOBAL STATE
══════════════════════════════════════════════════════════════════════ */

const state = {
  runs: 0,
  isRunning: false,
  startTime: null,
  elapsedTimer: null,
  logCount: 0,
  bestProfit: null,
  phase: 'idle',          // current pipeline phase, lowercase
  autonomous: false,      // whether the current run uses the AI loop
  aiKeySet: false,        // whether ANTHROPIC_API_KEY is present
  sparkData: { profit: [], dd: [], pf: [], trades: [], runs: [] },
};

/* ══════════════════════════════════════════════════════════════════════
   CHART.JS DEFAULTS
══════════════════════════════════════════════════════════════════════ */

Chart.defaults.color = '#64748b';
Chart.defaults.font.family = "'Inter', -apple-system, sans-serif";
Chart.defaults.font.size = 11;

/* ── Equity Curve ─────────────────────────────────────────────────── */

const equityChart = new Chart(el('equityChart').getContext('2d'), {
  type: 'line',
  data: {
    labels: [],
    datasets: [
      {
        label: 'Equity',
        data: [],
        borderColor: '#4f46e5',
        backgroundColor: (ctx) => {
          const { ctx: c, chartArea } = ctx.chart;
          if (!chartArea) return 'transparent';
          const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, 'rgba(79,70,229,0.22)');
          g.addColorStop(1, 'rgba(79,70,229,0)');
          return g;
        },
        borderWidth: 2, pointRadius: 0, pointHoverRadius: 4,
        pointHoverBackgroundColor: '#4f46e5', tension: 0.4, fill: true,
      },
      {
        label: 'Balance',
        data: [],
        borderColor: '#00d4aa',
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [5, 4],
        pointRadius: 0, pointHoverRadius: 4,
        pointHoverBackgroundColor: '#00d4aa', tension: 0.4, fill: false,
      },
    ],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 400 },
    interaction: { intersect: false, mode: 'index' },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#0f1629', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
        padding: 10, titleColor: '#94a3b8', bodyColor: '#e2e8f0',
        callbacks: { label: (ctx) => ' ' + ctx.dataset.label + ': $' + ctx.parsed.y.toFixed(2) },
      },
    },
    scales: {
      x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { maxTicksLimit: 8, maxRotation: 0, color: '#475569', font: { size: 10 } }, border: { display: false } },
      y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { maxTicksLimit: 6, color: '#475569', font: { size: 10 }, callback: (v) => '$' + v.toFixed(0) }, border: { display: false } },
    },
  },
});

/* ── Drawdown Chart ───────────────────────────────────────────────── */

const drawdownChart = new Chart(el('drawdownChart').getContext('2d'), {
  type: 'line',
  data: {
    labels: [],
    datasets: [{
      label: 'Drawdown',
      data: [],
      borderColor: '#ef4444',
      backgroundColor: (ctx) => {
        const { ctx: c, chartArea } = ctx.chart;
        if (!chartArea) return 'transparent';
        const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        g.addColorStop(0, 'rgba(239,68,68,0.35)');
        g.addColorStop(1, 'rgba(239,68,68,0.02)');
        return g;
      },
      borderWidth: 1.5, pointRadius: 0, pointHoverRadius: 3, tension: 0.4, fill: true,
    }],
  },
  options: {
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 400 },
    interaction: { intersect: false, mode: 'index' },
    plugins: {
      legend: { display: false },
      tooltip: {
        backgroundColor: '#0f1629', borderColor: 'rgba(255,255,255,0.1)', borderWidth: 1,
        padding: 8, titleColor: '#94a3b8', bodyColor: '#ef4444',
        callbacks: { label: (ctx) => ' Drawdown: ' + ctx.parsed.y.toFixed(2) + '%' },
      },
    },
    scales: {
      x: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { maxTicksLimit: 8, maxRotation: 0, color: '#475569', font: { size: 10 } }, border: { display: false } },
      y: { grid: { color: 'rgba(255,255,255,0.04)' }, ticks: { maxTicksLimit: 4, color: '#475569', font: { size: 10 }, callback: (v) => v.toFixed(1) + '%' }, border: { display: false } },
    },
  },
});

/* ── Sparklines ───────────────────────────────────────────────────── */

const sparklineCharts = {};

function createSparkline(canvasId, color, fillColor) {
  const canvas = el(canvasId);
  if (!canvas) return null;
  return new Chart(canvas.getContext('2d'), {
    type: 'line',
    data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: fillColor || 'transparent', borderWidth: 1.5, pointRadius: 0, tension: 0.4, fill: !!fillColor }] },
    options: { responsive: false, animation: { duration: 250 }, plugins: { legend: { display: false }, tooltip: { enabled: false } }, scales: { x: { display: false }, y: { display: false } } },
  });
}

function initSparklines() {
  sparklineCharts.profit = createSparkline('spark-profit', '#10b981', 'rgba(16,185,129,0.12)');
  sparklineCharts.dd     = createSparkline('spark-dd',     '#ef4444', 'rgba(239,68,68,0.12)');
  sparklineCharts.pf     = createSparkline('spark-pf',     '#f59e0b', 'rgba(245,158,11,0.10)');
  sparklineCharts.trades = createSparkline('spark-trades',  '#00d4aa', 'rgba(0,212,170,0.10)');
  sparklineCharts.runs   = createSparkline('spark-runs',   '#7c6dfa', 'rgba(124,109,250,0.10)');
}

function pushSparkData(key, value) {
  const arr = state.sparkData[key];
  arr.push(value);
  if (arr.length > 20) arr.shift();
  const c = sparklineCharts[key];
  if (!c) return;
  c.data.labels = arr.map((_, i) => i);
  c.data.datasets[0].data = arr;
  c.update('none');
}

/* ══════════════════════════════════════════════════════════════════════
   METRIC CARDS
══════════════════════════════════════════════════════════════════════ */

function updateMetricCards(d) {
  const profit = parseFloat(d.net_profit  || d.best_net_profit  || 0);
  const maxDD  = parseFloat(d.max_drawdown || d.best_max_drawdown || d.max_dd || 0);
  const pf     = parseFloat(d.profit_factor || d.best_pf || d.pf || 0);
  const trades = parseInt(d.total_trades  || d.best_total_trades || d.trades || 0, 10);

  // Net Profit
  safe('m-net-profit', e => { e.textContent = fmtMoney(profit); });
  if (state.bestProfit === null) state.bestProfit = profit;
  const delta = state.bestProfit !== 0 ? ((profit - state.bestProfit) / Math.abs(state.bestProfit) * 100) : 0;
  safe('m-net-profit-delta-val', e => { e.textContent = (delta >= 0 ? '+' : '') + delta.toFixed(1) + '%'; });
  const arrow = el('m-net-profit-delta') && el('m-net-profit-delta').querySelector('span:first-child');
  if (arrow) { arrow.textContent = delta >= 0 ? '↑' : '↓'; arrow.className = delta >= 0 ? 'delta-up' : 'delta-down'; }
  pushSparkData('profit', profit);

  // Max Drawdown
  safe('m-max-dd', e => { e.textContent = fmtPct(maxDD); });
  safe('max-dd-badge', e => { e.textContent = 'Max DD: ' + fmtPct(maxDD, 1); });
  pushSparkData('dd', maxDD);

  // Profit Factor
  safe('m-pf', e => { e.textContent = pf.toFixed(2); });
  safe('m-pf-quality', e => {
    if (pf >= 2)       { e.textContent = 'Excellent'; e.style.color = 'var(--green)'; }
    else if (pf >= 1.5){ e.textContent = 'Good';      e.style.color = 'var(--teal)'; }
    else if (pf >= 1)  { e.textContent = 'Marginal';  e.style.color = 'var(--yellow)'; }
    else               { e.textContent = 'Poor';       e.style.color = 'var(--red)'; }
  });
  pushSparkData('pf', pf);

  // Trades
  safe('m-trades', e => { e.textContent = trades.toLocaleString(); });
  pushSparkData('trades', trades);
}

function updateRunsCounter() {
  state.runs++;
  safe('m-runs', e => { e.textContent = state.runs.toString(); });
  pushSparkData('runs', state.runs);
}

/* ══════════════════════════════════════════════════════════════════════
   AI GAUGE
══════════════════════════════════════════════════════════════════════ */

const CIRCUMFERENCE = 163.4; // 2π × 26

function updateAIGauge(pct, label) {
  const color = { high: '#10b981', medium: '#f59e0b', low: '#ef4444' }[label] || '#00d4aa';
  safe('ai-gauge-arc', e => {
    e.style.strokeDashoffset = (CIRCUMFERENCE * (1 - pct / 100)).toString();
    e.style.stroke = color;
  });
  safe('ai-gauge-pct', e => { e.textContent = pct + '%'; });
  safe('ai-conf-label', e => {
    e.textContent = label ? label.charAt(0).toUpperCase() + label.slice(1) : '—';
    e.style.color = color;
  });
}

/* ══════════════════════════════════════════════════════════════════════
   HEADER SCORE
══════════════════════════════════════════════════════════════════════ */

function updateHeaderScore(score, delta) {
  const s = parseFloat(score);
  if (isNaN(s)) return;
  safe('hdr-score', e => { e.textContent = s.toFixed(3); });
  if (delta != null) {
    const d2 = parseFloat(delta);
    if (!isNaN(d2)) {
      const sign = d2 >= 0 ? '+' : '';
      safe('hdr-score-delta', e => {
        e.textContent = sign + d2.toFixed(3) + ' improvement';
        e.style.color = d2 >= 0 ? 'var(--green)' : 'var(--red)';
      });
    }
  } else {
    safe('hdr-score-delta', e => { e.textContent = 'Latest result'; e.style.color = ''; });
  }
}

/* ══════════════════════════════════════════════════════════════════════
   AI PANEL — shared update function
══════════════════════════════════════════════════════════════════════ */

function updateAIPanel(d) {
  if (!d) return;

  safe('ai-waiting', e => { e.style.display = 'none'; });
  safe('ai-content', e => { e.style.display = ''; });

  // Preview (always visible) — headline + diagnosis
  if (d.headline) {
    safe('ai-headline', e => { e.textContent = d.headline; e.style.display = ''; });
  }
  if (d.diagnosis) {
    safe('ai-diagnosis-text', e => { e.textContent = d.diagnosis; });
  }

  // Details section — populate but keep collapsed until user clicks "View Details"
  const patterns = d.patterns || d.strengths || [];
  if (patterns.length) {
    safe('ai-strengths-section', e => { e.style.display = ''; });
    safe('ai-strengths-list', e => {
      e.innerHTML = patterns.slice(0, 4).map(p => '<li>' + escapeHtml(p) + '</li>').join('');
    });
  }

  const suggestions = d.suggestions || [];
  if (suggestions.length) {
    const s = suggestions[0];
    let txt = '';
    if (typeof s === 'string') {
      txt = s;
    } else {
      txt = s.reason || '';
      if (s.param || s.parameter) {
        const param = s.param || s.parameter;
        txt += ' (' + param + ': ' + (s.from !== undefined ? s.from : '') + ' → ' + (s.to !== undefined ? s.to : '') + ')';
      }
    }
    safe('ai-suggestion-text', e => { e.textContent = txt; });
    safe('ai-suggestion-box', e => { e.style.display = ''; });
  }

  const risks = d.risk_flags || d.risks || [];
  if (risks.length) {
    safe('ai-risks-section', e => { e.style.display = ''; });
    safe('ai-risks-list', e => {
      e.innerHTML = risks.slice(0, 3).map(r => '<li>' + escapeHtml(r) + '</li>').join('');
    });
  }

  // Confidence (always shown at bottom)
  const conf = (typeof d.confidence === 'string' ? d.confidence : 'medium').toLowerCase();
  safe('ai-panel-conf-badge', e => {
    e.textContent = conf;
    e.className = 'ai-conf-badge ' + conf;
  });

  const pctMap = { high: 87, medium: 62, low: 38 };
  const pct = typeof d.confidence_pct === 'number' ? d.confidence_pct : (pctMap[conf] || 62);
  updateAIGauge(pct, conf);

  if (d.run_id) safe('ai-run-ref', e => { e.textContent = 'Run: ' + d.run_id; });

  safe('ai-badge', e => { e.style.display = ''; });
}

/* ══════════════════════════════════════════════════════════════════════
   RECENT RUNS TABLE
══════════════════════════════════════════════════════════════════════ */

const MAX_RECENT_RUNS = 8;
const recentRuns = [];

function addRecentRun(d) {
  const profit = parseFloat(d.net_profit || 0);
  const pf     = parseFloat(d.profit_factor || d.pf || 0);
  const maxDD  = parseFloat(d.max_drawdown || d.max_dd || 0);
  const run_id = d.run_id || null;

  // Derive verdict: use explicit field if present, otherwise infer from passing
  let verdict = (d.verdict || '').toUpperCase().replace(/ /g, '_');
  if (!verdict) {
    verdict = d.passing === false ? 'NOT_RELIABLE' : (d.passing === true ? 'PASSING' : 'PENDING');
  }

  const verdictClass = {
    RECOMMENDED:  'verdict-recommended',
    PASSING:      'verdict-recommended',
    RISKY:        'verdict-risky',
    PENDING:      'verdict-risky',
    NOT_RELIABLE: 'verdict-not-reliable',
  }[verdict] || 'verdict-risky';

  const verdictLabel = {
    RECOMMENDED:  'Recommended',
    PASSING:      'Passing',
    RISKY:        'Risky',
    PENDING:      'Pending',
    NOT_RELIABLE: 'Not Reliable',
  }[verdict] || verdict.replace(/_/g, ' ');

  recentRuns.unshift({ profit, pf, maxDD, verdictClass, verdictLabel, run_id });
  if (recentRuns.length > MAX_RECENT_RUNS) recentRuns.pop();

  const tbody = el('recent-runs-tbody');
  if (!tbody) return;
  tbody.innerHTML = recentRuns.map((r, i) => `
    <tr>
      <td class="run-num">#${state.runs - i}</td>
      <td class="${r.profit >= 0 ? 'profit-pos' : 'profit-neg'}">${fmtMoney(r.profit)}</td>
      <td style="color:var(--yellow);font-weight:600;">${r.pf.toFixed(2)}</td>
      <td style="color:var(--red);">${r.maxDD.toFixed(1)}%</td>
      <td><span class="${r.verdictClass}">${r.verdictLabel}</span></td>
      <td>${r.run_id ? `<button class="detail-btn" onclick="openRunDetail('${escapeHtml(r.run_id)}')">Details</button>` : ''}</td>
    </tr>
  `).join('');
}

/* ══════════════════════════════════════════════════════════════════════
   PARAMETERS TABLE
══════════════════════════════════════════════════════════════════════ */

function updateParamsTable(params) {
  if (!params || typeof params !== 'object') return;
  const tbody = el('params-tbody');
  if (!tbody) return;
  const entries = Object.entries(params);
  if (!entries.length) return;

  tbody.innerHTML = entries.slice(0, 8).map(([key, val], i) => {
    const impactClass = i < 2 ? 'impact-high' : i < 5 ? 'impact-medium' : 'impact-low';
    const impactLabel = i < 2 ? 'High' : i < 5 ? 'Medium' : 'Low';
    const display = typeof val === 'number' ? val.toFixed(val % 1 !== 0 ? 4 : 0) : val;
    return `
      <tr>
        <td class="param-name">${escapeHtml(key)}</td>
        <td class="param-val">${escapeHtml(String(display))}</td>
        <td><span class="${impactClass}">${impactLabel}</span></td>
      </tr>
    `;
  }).join('');
}

/* ══════════════════════════════════════════════════════════════════════
   CHART UPDATES
══════════════════════════════════════════════════════════════════════ */

function pushEquityPoint(equity, balance, label) {
  const MAX = 80;
  const lbl = label || nowStr();
  equityChart.data.labels.push(lbl);
  equityChart.data.datasets[0].data.push(parseFloat(equity) || 0);
  equityChart.data.datasets[1].data.push(parseFloat(balance) || 0);
  if (equityChart.data.labels.length > MAX) {
    equityChart.data.labels.shift();
    equityChart.data.datasets.forEach(ds => ds.data.shift());
  }
  equityChart.update('none');
}

function pushDDPoint(dd, label) {
  const MAX = 80;
  const lbl = label || nowStr();
  drawdownChart.data.labels.push(lbl);
  drawdownChart.data.datasets[0].data.push(parseFloat(dd) || 0);
  if (drawdownChart.data.labels.length > MAX) {
    drawdownChart.data.labels.shift();
    drawdownChart.data.datasets[0].data.shift();
  }
  drawdownChart.update('none');
}

function populateChartsFromEquityCurve(curve) {
  if (!Array.isArray(curve) || !curve.length) return;
  equityChart.data.labels = [];
  equityChart.data.datasets[0].data = [];
  equityChart.data.datasets[1].data = [];
  drawdownChart.data.labels = [];
  drawdownChart.data.datasets[0].data = [];

  const step = Math.max(1, Math.floor(curve.length / 80));
  curve.forEach((pt, i) => {
    if (i % step !== 0 && i !== curve.length - 1) return;
    const lbl = pt.date || pt.time || String(i + 1);
    equityChart.data.labels.push(lbl);
    equityChart.data.datasets[0].data.push(parseFloat(pt.equity || pt.Equity || 0));
    equityChart.data.datasets[1].data.push(parseFloat(pt.balance || pt.Balance || 0));
    drawdownChart.data.labels.push(lbl);
    drawdownChart.data.datasets[0].data.push(parseFloat(pt.drawdown || pt.Drawdown || 0));
  });

  equityChart.update();
  drawdownChart.update();
}

/* ══════════════════════════════════════════════════════════════════════
   PROGRESS
══════════════════════════════════════════════════════════════════════ */

function updateProgress(d) {
  // Use progress_pct if available, fall back to run_number/total
  let pct = null;
  if (d.progress_pct !== undefined) pct = d.progress_pct;
  else if (d.run_number !== undefined && d.total) pct = Math.round(d.run_number / d.total * 100);

  if (pct !== null) {
    const p = Math.min(100, pct);
    safe('opt-progress-fill', e => { e.style.width = p + '%'; });
    safe('hdr-prog-fill',     e => { e.style.width = p + '%'; });
    safe('hdr-pct',           e => { e.textContent = p + '% Complete'; });
  }

  if (d.run_number !== undefined && d.total !== undefined) {
    safe('opt-gen',       e => { e.textContent = 'Run ' + d.run_number + ' / ' + d.total; });
    safe('hdr-gen-cur',   e => { e.textContent = d.run_number; });
    safe('hdr-gen-total', e => { e.textContent = d.total; });
    safe('opt-remaining', e => {
      if (!state.startTime || !d.run_number) return;
      const elapsed = (Date.now() - state.startTime) / 1000;
      const rem = (elapsed / d.run_number) * (d.total - d.run_number);
      e.textContent = fmtTime(rem);
    });
  }
}

/* ══════════════════════════════════════════════════════════════════════
   ELAPSED TIMER
══════════════════════════════════════════════════════════════════════ */

function startElapsedTimer() {
  if (!state.startTime) state.startTime = Date.now();
  stopElapsedTimer();
  state.elapsedTimer = setInterval(() => {
    const secs = Math.floor((Date.now() - state.startTime) / 1000);
    safe('opt-elapsed', e => { e.textContent = fmtTime(secs); });
  }, 1000);
}

function stopElapsedTimer() {
  if (state.elapsedTimer) { clearInterval(state.elapsedTimer); state.elapsedTimer = null; }
}

/* ══════════════════════════════════════════════════════════════════════
   RUNNING STATE TOGGLE
══════════════════════════════════════════════════════════════════════ */

function setRunningState(isRunning, label) {
  state.isRunning = isRunning;

  safe('run-indicator', e => { e.classList.toggle('active', isRunning); });
  safe('btn-stop',  e => { e.classList.toggle('active', isRunning); });
  // (btn-pause removed — pipeline doesn't support pause yet)

  safe('hdr-dot',        e => { e.classList.toggle('running', isRunning); });
  safe('hdr-status',     e => { e.textContent = isRunning ? (label || 'Optimizing') : 'System Idle'; });
  safe('hdr-status-sub', e => { e.textContent = isRunning ? 'Optimization in progress...' : 'Ready to optimize'; });

  if (isRunning) {
    if (label) safe('run-indicator-label', e => { e.textContent = label; });
    startElapsedTimer();
    refreshAIWaitingState();
  } else {
    stopElapsedTimer();
  }
}

/**
 * Update the AI Summary panel's waiting message + action link based on
 * whether (a) a run is active and (b) the API key is configured.
 * Called whenever phase/state/aiKeySet changes.
 */
function refreshAIWaitingState() {
  const waiting = el('ai-waiting');
  const txt     = el('ai-waiting-text');
  const action  = el('ai-waiting-action');
  if (!waiting || !txt) return;
  // Don't override if AI content has already loaded
  const content = el('ai-content');
  if (content && content.style.display !== 'none') return;
  if (state.isRunning && !state.aiKeySet) {
    txt.textContent = 'AI insights are off — no Anthropic API key set.';
    if (action) action.style.display = '';
  } else if (state.isRunning && state.aiKeySet) {
    txt.textContent = 'Optimization running — first AI analysis fires after the next backtest.';
    if (action) action.style.display = 'none';
  } else {
    txt.textContent = 'Waiting for optimization to start...';
    if (action) action.style.display = 'none';
  }
}

/* ══════════════════════════════════════════════════════════════════════
   PHASE STEPS
══════════════════════════════════════════════════════════════════════ */

const phaseMap = {
  phase1: 1, phase_1: 1, broad: 1, scan: 1,
  phase2: 2, phase_2: 2, deep: 2, refine: 2,
  phase3: 3, phase_3: 3, validate: 3, validation: 3, phase3_oos: 3, phase3_sens: 3,
};

function setPhaseActive(phaseNum, meta) {
  for (let i = 1; i <= 3; i++) {
    const step = el('phase-step-' + i);
    if (!step) continue;
    step.classList.remove('active', 'done');
    if (i < phaseNum) step.classList.add('done');
    if (i === phaseNum) step.classList.add('active');
  }
  const labels = { 1: 'Exploration', 2: 'Iteration', 3: 'Validation' };
  safe('opt-phase', e => { e.textContent = labels[phaseNum] || 'Phase ' + phaseNum; });
  if (meta && typeof meta.total === 'number') {
    const mode = meta.mode === 'autonomous' ? ' · AI' : '';
    safe('phase-sub-' + phaseNum, e => {
      e.textContent = `0/${meta.total}${mode}`;
    });
  }
}

function updatePhaseSubProgress(phaseNum, current, total) {
  safe('phase-sub-' + phaseNum, e => {
    e.textContent = `${current}/${total}`;
  });
}

/* ══════════════════════════════════════════════════════════════════════
   LIVE LOG
══════════════════════════════════════════════════════════════════════ */

const MAX_LOG_LINES = 200;

function addLog(msg, level) {
  const lvl = level || 'info';
  state.logCount++;
  safe('log-count', e => { e.textContent = state.logCount; });

  const feed = el('log-feed');
  if (!feed) return;

  const line = document.createElement('div');
  line.className = 'log-line ' + lvl;
  line.innerHTML = '<span class="log-time">' + nowStr() + '</span><span class="log-msg">' + escapeHtml(String(msg)) + '</span>';
  feed.appendChild(line);
  while (feed.children.length > MAX_LOG_LINES) feed.removeChild(feed.firstChild);
  feed.scrollTop = feed.scrollHeight;
}

function toggleLog() {
  const body = el('log-body');
  const icon = el('log-toggle-icon');
  if (body) body.classList.toggle('open');
  if (icon) icon.classList.toggle('open');
}

/* ══════════════════════════════════════════════════════════════════════
   MT5 STATUS
══════════════════════════════════════════════════════════════════════ */

function updateMT5Status(statusStr, label) {
  const dot = el('mt5-dot');
  if (dot) dot.classList.toggle('disconnected', statusStr === 'disconnected');
  safe('mt5-state', e => { e.textContent = label || statusStr; });
}

/* ══════════════════════════════════════════════════════════════════════
   VERDICT OVERLAY
══════════════════════════════════════════════════════════════════════ */

function showVerdict(d) {
  const icons   = { RECOMMENDED: '✅', RISKY: '⚠️', NOT_RELIABLE: '❌' };
  const colors  = { RECOMMENDED: '#10b981', RISKY: '#f59e0b', NOT_RELIABLE: '#ef4444' };
  const subs    = {
    RECOMMENDED:  'Consistent returns with controlled drawdown. Ready to deploy.',
    RISKY:        'Profitable in-sample but shows fragility. Deploy with caution.',
    NOT_RELIABLE: 'Results are inconsistent. Try different settings or date ranges.',
  };

  const verdict = (d.verdict || 'RISKY').toUpperCase().replace(/ /g, '_');

  safe('verdict-icon',     e => { e.textContent = icons[verdict] || '?'; });
  safe('verdict-title',    e => { e.textContent = verdict.replace(/_/g, ' '); e.style.color = colors[verdict] || '#e2e8f0'; });
  safe('verdict-subtitle', e => { e.textContent = subs[verdict] || ''; });

  const profit  = parseFloat(d.net_profit  || 0);
  const calmar  = parseFloat(d.calmar      || 0);
  const winRate = parseFloat(d.win_rate    || 0);
  const maxDD   = parseFloat(d.max_drawdown || d.max_dd || 0);

  safe('verdict-metrics', e => {
    e.innerHTML = `
      <div class="vm"><div class="vm-val" style="color:#10b981">${fmtMoney(profit)}</div><div class="vm-lbl">Net Profit</div></div>
      <div class="vm"><div class="vm-val">${calmar.toFixed(2)}</div><div class="vm-lbl">Calmar</div></div>
      <div class="vm"><div class="vm-val">${winRate.toFixed(1)}%</div><div class="vm-lbl">Win Rate</div></div>
      <div class="vm"><div class="vm-val" style="color:#ef4444">${maxDD.toFixed(1)}%</div><div class="vm-lbl">Max DD</div></div>
    `;
  });

  safe('verdict-actions', e => {
    e.innerHTML = `
      ${d.set_file_url ? '<a href="' + d.set_file_url + '" class="verdict-btn primary" download>⬇ Download .set File</a>' : ''}
      <a href="/reports" class="verdict-btn secondary">📋 View Report</a>
      <a href="/setup"   class="verdict-btn secondary">⚡ New Run</a>
      <button class="verdict-btn ghost" onclick="document.getElementById('verdict-overlay').style.display='none'">✕ Close</button>
    `;
  });

  safe('verdict-overlay', e => { e.style.display = 'flex'; });
  stopElapsedTimer();
  setRunningState(false);
}

/* ══════════════════════════════════════════════════════════════════════
   STATUS POLLING FALLBACK
   Polls /api/status every 5 seconds when SocketIO is connected but
   pipeline state is unknown (e.g., user opens dashboard mid-run).
══════════════════════════════════════════════════════════════════════ */

let pollInterval = null;

function startPolling() {
  if (pollInterval) return;
  pollInterval = setInterval(async () => {
    try {
      const resp = await fetch('/api/status');
      const d = await resp.json();
      syncFromStatus(d);
    } catch (_) {}
  }, 5000);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

function syncFromStatus(d) {
  const isRunning = d.state === 'running';

  if (isRunning && !state.isRunning) {
    setRunningState(true, d.phase || 'Optimizing...');
    const ph = phaseMap[d.phase] || 1;
    setPhaseActive(ph);
  } else if (!isRunning && state.isRunning) {
    setRunningState(false);
  }

  if (d.run_count) {
    state.runs = d.run_count;
    safe('m-runs', e => { e.textContent = state.runs.toString(); });
  }

  // Populate metric cards from flat status fields
  if (d.best_net_profit != null || d.best_max_drawdown != null) {
    updateMetricCards({
      net_profit:    d.best_net_profit,
      max_drawdown:  d.best_max_drawdown,
      profit_factor: d.best_pf,
      total_trades:  d.best_total_trades,
    });
  }

  // Restore header score
  if (d.best_score != null) updateHeaderScore(d.best_score);

  // Restore AI panel
  if (d.latest_insight) {
    updateAIPanel(d.latest_insight);
    addLog('AI insight restored from server (run: ' + (d.latest_insight.run_id || '?') + ')', 'info');
  }

  if (d.elapsed_s) {
    // Approximate startTime from elapsed
    if (!state.startTime) state.startTime = Date.now() - d.elapsed_s * 1000;
  }
}

/* ══════════════════════════════════════════════════════════════════════
   SOCKET.IO
══════════════════════════════════════════════════════════════════════ */

const socket = io({ transports: ['websocket', 'polling'] });
if (typeof window !== 'undefined') window.socket = socket;

socket.on('connect', () => {
  addLog('Connected to APEX server.', 'success');
  updateMT5Status('connected', 'Connected');
  // Also poll once to sync state immediately
  fetch('/api/status').then(r => r.json()).then(syncFromStatus).catch(() => {});
});

socket.on('disconnect', (reason) => {
  addLog('Disconnected: ' + reason, 'warn');
  updateMT5Status('disconnected', 'Disconnected');
});

socket.on('connect_error', (err) => {
  addLog('Connection error: ' + err.message, 'error');
  updateMT5Status('disconnected', 'Error');
});

/* ── status_sync ── full state on reconnect ───────────────────────── */

socket.on('status_sync', (d) => {
  addLog('Status sync received.', 'info');
  syncFromStatus(d);
});

/* ── status_change ────────────────────────────────────────────────── */

socket.on('status_change', (d) => {
  // Backend emits d.state NOT d.status
  const running = d.state === 'running' || d.status === 'running';
  const label = d.label || d.phase || (running ? 'Optimizing...' : 'Idle');
  setRunningState(running, label);
  addLog('Status: ' + (d.state || d.status || (running ? 'running' : 'idle')), 'info');

  // Reset live-intelligence panels when a fresh run starts
  if (running && (d.state === 'running' || d.status === 'running') && d.phase === 'setup') {
    thinkingFeedState.entries = [];
    paramChangesState.iterations = [];
    validationState.runs = [];
    validationState.planned = 0;
    validationState.complete = 0;
    validationState.active = false;
    renderThinkingFeed();
    renderParamChanges();
    renderValidation();
    safe('tf-badge', e => { e.textContent = 'Live'; e.className = 'badge live'; });
    safe('pc-badge', e => { e.textContent = '—'; e.className = 'badge'; });
    safe('val-badge', e => { e.textContent = 'Pending'; e.className = 'badge'; });
    dismissEarlyTerm();
  }
});

/* ── run_complete ─────────────────────────────────────────────────── */

socket.on('run_complete', (d) => {
  updateRunsCounter();

  // Only update metric cards if we have meaningful data
  if (d.net_profit != null) updateMetricCards(d);

  // Charts: use equity curve if available, otherwise push single point
  if (d.equity_curve) {
    populateChartsFromEquityCurve(d.equity_curve);
  } else if (d.net_profit != null) {
    pushEquityPoint(d.net_profit, d.balance || d.net_profit, 'Run ' + state.runs);
    pushDDPoint(d.max_drawdown || 0, 'Run ' + state.runs);
  }

  if (d.params) updateParamsTable(d.params);
  if (d.score != null) updateHeaderScore(d.score);

  addRecentRun(d);
  updateProgress(d);

  // Update per-phase sub-progress label under phase dot
  const phase = (d.phase || '').toLowerCase();
  if (phase === 'phase1' && d.run_number && d.total) {
    updatePhaseSubProgress(1, d.run_number, d.total);
  } else if (phase.startsWith('phase2') && d.progress_pct != null) {
    // Rough count: we don't always have iteration here; UI updates via ai_iteration_* handler
  } else if (phase.startsWith('phase3')) {
    // Updated via validation_run_complete handler
  }

  const msg = 'Run ' + state.runs + ' (' + (d.phase || '?') + ') '
    + (d.net_profit != null ? '— Net Profit: ' + fmtMoney(d.net_profit) : '')
    + (d.profit_factor != null ? ', PF: ' + parseFloat(d.profit_factor).toFixed(2) : '')
    + (d.max_drawdown != null ? ', Max DD: ' + fmtPct(d.max_drawdown) : '');
  addLog(msg, d.passing === false ? 'warn' : 'success');
});

/* ── phase_start ──────────────────────────────────────────────────── */

socket.on('phase_start', (d) => {
  const phaseNum = phaseMap[d.phase] || parseInt(d.phase_num || d.phase || '1', 10);
  state.phase = (d.phase || '').toLowerCase();
  if (d.mode === 'autonomous') state.autonomous = true;
  setPhaseActive(phaseNum, { total: d.total, mode: d.mode });
  // Re-render empty-state cards so they pick up the phase-aware message
  renderParamChanges();
  const label = d.label
    || ({ 1: 'Exploration', 2: (d.mode === 'autonomous' ? 'AI Iteration' : 'Iteration'), 3: 'Validation' }[phaseNum])
    || ('Phase ' + phaseNum);
  addLog('Phase started: ' + label, 'info');
  safe('run-indicator-label', e => { e.textContent = label + '...'; });
  setRunningState(true, label);
});

/* ── phase1_complete ──────────────────────────────────────────────── */

socket.on('phase1_complete', (d) => {
  addLog('Phase 1 complete — ' + (d.n_passing || 0) + '/' + (d.total_tested || '?') + ' configs passed.', 'success');
  setPhaseActive(2);

  // Backend emits top_results (NOT top_configs)
  const topResults = d.top_results || d.top_configs || [];
  if (topResults.length) {
    if (topResults[0] && topResults[0].params) updateParamsTable(topResults[0].params);
    topResults.forEach(cfg => addRecentRun(cfg));
  }
});

/* ── phase2_complete ──────────────────────────────────────────────── */

socket.on('phase2_complete', (d) => {
  addLog('Phase 2 complete — best: ' + (d.best_run_id || '?') + ', profit=$' + (d.best_profit || 0), 'success');
  setPhaseActive(3);

  // Phase2 complete has flat fields: best_profit, best_calmar
  if (d.best_profit != null) {
    updateMetricCards({ net_profit: d.best_profit, calmar: d.best_calmar });
  }
});

/* ── optimization_complete ────────────────────────────────────────── */

socket.on('optimization_complete', (d) => {
  addLog('✅ Optimization complete! Verdict: ' + (d.verdict || '?'), 'success');
  for (let i = 1; i <= 3; i++) {
    const step = el('phase-step-' + i);
    if (step) { step.classList.remove('active'); step.classList.add('done'); }
  }
  safe('opt-progress-fill', e => { e.style.width = '100%'; });
  safe('hdr-prog-fill',     e => { e.style.width = '100%'; });
  safe('hdr-pct',           e => { e.textContent = '100% Complete'; });
  safe('opt-phase', e => { e.textContent = 'Complete'; });
  if (d.score != null) updateHeaderScore(d.score);
  showVerdict(d);
});

/* ── ai_insight ───────────────────────────────────────────────────── */

socket.on('ai_insight', (d) => {
  updateAIPanel(d);
  addLog('AI Insight received (confidence: ' + (d.confidence || '?') + ')', 'info');
});

/* ── log ──────────────────────────────────────────────────────────── */

socket.on('log', (d) => {
  const msg   = typeof d === 'string' ? d : (d.msg || d.message || JSON.stringify(d));
  const level = typeof d === 'object' ? (d.level || 'info') : 'info';
  addLog(msg, level);
});

/* ── no_profitable_config ─────────────────────────────────────────── */

socket.on('no_profitable_config', (d) => {
  safe('no-profit-banner', e => {
    e.classList.add('active');
    // Backend emits d.msg (not d.message)
    const text = (d && (d.msg || d.message)) || 'No profitable configuration found.';
    const msgEl = el('no-profit-msg');
    if (msgEl) msgEl.textContent = ' ' + text;
  });
  setRunningState(false);
  addLog('No profitable configuration found.', 'warn');
});

/* ── error ────────────────────────────────────────────────────────── */

socket.on('error', (d) => {
  const msg = (d && d.msg) || 'Unknown server error';
  addLog('Server error: ' + msg, 'error');
  setRunningState(false);
});

/* ── Autonomous AI Loop events ─────────────────────────────────────── */

socket.on('ai_iteration_start', (d) => {
  const iter = d.iteration || '?';
  const max  = d.max_iterations || '?';
  safe('hdr-status',     e => { e.textContent = 'AI Loop'; });
  safe('hdr-status-sub', e => { e.textContent = `Iteration ${iter} / ${max}`; });

  let msg = `🤖 AI Iteration ${iter}/${max}`;
  if (d.is_stuck_escape) {
    msg += ' [escape mode]';
  } else if (d.changes && d.changes.length) {
    const changes = d.changes.map(c => `${c.param}=${c.value}`).join(', ');
    msg += ` — trying: ${changes}`;
  }
  addLog(msg, 'info');
  if (d.analysis) addLog(`  AI reasoning: ${d.analysis}`, 'info');
});

socket.on('ai_iteration_complete', (d) => {
  const iter   = d.iteration || '?';
  const max    = d.max_iterations || 0;
  const pf     = parseFloat(d.profit_factor || 0).toFixed(2);
  const calmar = parseFloat(d.calmar || 0).toFixed(2);
  const dd     = parseFloat(d.max_drawdown || 0).toFixed(1);
  const status = d.passing ? '✅' : '❌';

  addLog(
    `  ${status} iter=${iter} | PF=${pf} | Calmar=${calmar} | DD=${dd}% | `
    + `confidence=${(d.confidence || 0).toFixed(2)}`,
    d.passing ? 'success' : 'warn'
  );

  if (typeof iter === 'number' && max) updatePhaseSubProgress(2, iter, max);
  if (d.best_score != null) updateHeaderScore(d.best_score);

  if (d.goal_status) {
    const gs = d.goal_status;
    const flags = [
      gs.profit_factor_met ? '✓ PF'     : '✗ PF',
      gs.drawdown_ok       ? '✓ DD'     : '✗ DD',
      gs.calmar_met        ? '✓ Calmar' : '✗ Calmar',
    ].join('  ');
    addLog(`  Targets: ${flags}`, 'info');
  }

  if (d.targets_met) addLog('🎯 All targets met! Stopping AI loop early.', 'success');
});

socket.on('ai_targets_met', (d) => {
  addLog(
    `🎯 Targets achieved after ${d.iteration} iterations — `
    + `PF=${parseFloat(d.profit_factor || 0).toFixed(2)}, `
    + `Calmar=${parseFloat(d.calmar || 0).toFixed(2)}, `
    + `DD=${parseFloat(d.max_drawdown || 0).toFixed(1)}%`,
    'success'
  );
});

socket.on('ai_stuck', (d) => {
  addLog(`⚠ AI loop stuck at iteration ${d.iteration} — applying random escape`, 'warn');
});

/* ══════════════════════════════════════════════════════════════════════
   LIVE INTELLIGENCE — AI Thinking Feed / Param Changes / Validation
══════════════════════════════════════════════════════════════════════ */

const MAX_THINKING_ENTRIES = 150;
const MAX_PARAM_CHANGE_ITERATIONS = 20;
const thinkingFeedState = { entries: [], active: false };
const paramChangesState = { iterations: [] };
const validationState   = { runs: [], planned: 0, complete: 0, active: false };

function _formatVal(v) {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return String(v);
    return Number(v).toFixed(Math.abs(v) < 10 ? 4 : 2);
  }
  return String(v);
}

function _markerFor(kind) {
  return {
    info:       '•',
    reasoning:  '✦',
    decision:   '→',
    success:    '✓',
    warning:    '⚠',
    hypothesis: '?',
  }[kind] || '•';
}

function renderThinkingFeed() {
  const box = el('thinking-feed');
  if (!box) return;
  if (!thinkingFeedState.entries.length) {
    box.innerHTML = '<div class="live-intel-empty">Waiting for the optimizer to start…<br><span style="font-size:0.68rem">The AI\'s reasoning will stream here in real time.</span></div>';
    return;
  }
  box.innerHTML = thinkingFeedState.entries.map(e => {
    const kind = e.kind || 'info';
    const iter = e.iteration ? `<span class="tf-chip">iter ${e.iteration}</span>` : '';
    const phase = e.phase ? `<span class="tf-chip">${escapeHtml(e.phase)}</span>` : '';
    const time = e.time ? `<span>${escapeHtml(e.time)}</span>` : '';
    const cursor = e.streaming ? '<span class="tf-cursor">▌</span>' : '';
    return `<div class="tf-entry ${kind}${e.streaming ? ' streaming' : ''}">
      <div class="tf-marker">${_markerFor(kind)}</div>
      <div class="tf-body">
        <div class="tf-msg">${escapeHtml(e.msg)}${cursor}</div>
        <div class="tf-meta">${time}${iter}${phase}</div>
      </div>
    </div>`;
  }).join('');
  // Auto-scroll to bottom so latest entry is visible
  box.scrollTop = box.scrollHeight;
}

function addThinking(d) {
  // For replayed events, use the original ts from the server. For live ones, use now.
  let timeLabel = nowStr();
  if (d.ts) {
    try {
      const dt = new Date(d.ts);
      if (!isNaN(dt)) {
        timeLabel = dt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      }
    } catch (_) {}
  }
  const entry = {
    msg:       d.msg || '',
    kind:      d.kind || 'info',
    iteration: d.iteration,
    phase:     d.phase,
    time:      timeLabel,
  };
  thinkingFeedState.entries.push(entry);
  if (thinkingFeedState.entries.length > MAX_THINKING_ENTRIES) {
    thinkingFeedState.entries.shift();
  }
  thinkingFeedState.active = true;
  safe('tf-badge', e => { e.textContent = 'Live'; e.className = 'badge live'; });
  renderThinkingFeed();
}

function renderParamChanges() {
  const box = el('param-changes-list');
  if (!box) return;
  if (!paramChangesState.iterations.length) {
    // Tailor the empty-state message to the current phase + mode
    const phase = (state.phase || '').toLowerCase();
    let msg, sub;
    if (phase === 'phase1') {
      msg = 'Exploration phase';
      sub = 'Parameter changes appear once Phase 2 (AI iteration) starts.';
    } else if (phase.startsWith('phase2')) {
      msg = state.autonomous ? 'Waiting for first AI iteration…' : 'Random-neighbor refinement';
      sub = state.autonomous
        ? 'Claude will choose the next parameter set after the first run finishes.'
        : 'Enable Autonomous AI mode in setup to see AI-driven parameter changes.';
    } else if (phase.startsWith('phase3')) {
      msg = 'Validation phase';
      sub = 'Phase 2 complete. See validation panel for OOS + sensitivity results.';
    } else {
      msg = 'No iterations yet';
      sub = 'AI-driven parameter edits appear per iteration during Phase 2.';
    }
    box.innerHTML = `<div class="live-intel-empty">${escapeHtml(msg)}<br><span style="font-size:0.68rem">${escapeHtml(sub)}</span></div>`;
    return;
  }
  // Newest first
  const items = [...paramChangesState.iterations].reverse();
  box.innerHTML = items.map(it => {
    const changes = (it.changes || []).map(c => `
      <div class="pc-change">
        <span class="pc-param">${escapeHtml(c.param || '')}</span>:
        <span class="pc-oldval">${escapeHtml(_formatVal(c.from))}</span>
        <span class="pc-arrow">→</span>
        <span class="pc-newval">${escapeHtml(_formatVal(c.to))}</span>
        ${c.reason ? `<div class="pc-reason">${escapeHtml(c.reason)}</div>` : ''}
      </div>
    `).join('');
    const conf = (typeof it.confidence === 'number')
      ? `conf ${(it.confidence * 100).toFixed(0)}%`
      : '';
    return `<div class="pc-iteration">
      <div class="pc-iter-header">
        <span class="pc-iter-label">iter ${it.iteration}</span>
        <span class="pc-iter-conf">${escapeHtml(conf)}</span>
      </div>
      ${changes || '<div class="pc-change" style="color:var(--muted);font-style:italic">(no param changes — random escape)</div>'}
    </div>`;
  }).join('');
}

function addParamChanges(d) {
  paramChangesState.iterations.push({
    iteration:  d.iteration,
    changes:    d.changes || d.change_records || [],
    analysis:   d.analysis || '',
    confidence: d.confidence,
  });
  if (paramChangesState.iterations.length > MAX_PARAM_CHANGE_ITERATIONS) {
    paramChangesState.iterations.shift();
  }
  safe('pc-badge', e => { e.textContent = `iter ${d.iteration}`; e.className = 'badge live'; });
  renderParamChanges();
}

function renderValidation() {
  const box = el('validation-panel');
  if (!box) return;
  if (!validationState.active && !validationState.runs.length) {
    box.innerHTML = '<div class="live-intel-empty">Validation runs after optimization.<br><span style="font-size:0.68rem">Out-of-sample + sensitivity tests.</span></div>';
    return;
  }
  const planned = validationState.planned || validationState.runs.length;
  const done    = validationState.complete;
  const pct     = planned > 0 ? Math.round(done / planned * 100) : 0;
  const progress = `
    <div class="val-progress-row">
      <span>${done}/${planned}</span>
      <div class="val-progress-bar"><div class="val-progress-fill" style="width:${pct}%"></div></div>
      <span>${pct}%</span>
    </div>`;
  const runsHtml = validationState.runs.map(r => {
    let clsExtra = r.status === 'running' ? 'active' : (r.passing === true ? 'pass' : (r.passing === false ? 'fail' : ''));
    const statusBadge = r.status === 'running'
      ? '<span class="val-spinner"></span>'
      : (r.passing ? '<span style="color:var(--green)">✓</span>'
                   : r.passing === false ? '<span style="color:var(--red)">✗</span>' : '');
    const metrics = (r.status === 'complete') ? `
      <div class="val-run-metrics">
        <div class="val-metric"><div class="val-metric-lbl">Profit</div><div class="val-metric-val" style="color:${r.net_profit >= 0 ? 'var(--green)' : 'var(--red)'}">${fmtMoney(r.net_profit)}</div></div>
        <div class="val-metric"><div class="val-metric-lbl">PF</div><div class="val-metric-val" style="color:var(--yellow)">${Number(r.profit_factor || 0).toFixed(2)}</div></div>
        <div class="val-metric"><div class="val-metric-lbl">Calmar</div><div class="val-metric-val" style="color:var(--teal)">${Number(r.calmar || 0).toFixed(2)}</div></div>
        <div class="val-metric"><div class="val-metric-lbl">DD</div><div class="val-metric-val" style="color:var(--red)">${Number(r.max_drawdown || 0).toFixed(1)}%</div></div>
      </div>` : '';
    return `<div class="val-run ${clsExtra}">
      <div class="val-run-title">
        ${statusBadge}
        <span>${escapeHtml(r.label || r.kind || 'Run')}</span>
        <span class="val-run-kind ${escapeHtml(r.kind || '')}">${escapeHtml(r.kind || '')}</span>
      </div>
      ${r.description ? `<div class="val-run-desc">${escapeHtml(r.description)}</div>` : ''}
      ${metrics}
    </div>`;
  }).join('');
  box.innerHTML = progress + runsHtml;
}

function validationRunStart(d) {
  validationState.active = true;
  validationState.planned = d.total || validationState.planned;
  // Replace any existing run with same run_id
  validationState.runs = validationState.runs.filter(r => r.run_id !== d.run_id);
  validationState.runs.push({
    run_id:      d.run_id,
    kind:        d.kind,
    label:       d.label,
    description: d.description,
    index:       d.index,
    status:      'running',
  });
  safe('val-badge', e => { e.textContent = `Running ${d.index}/${d.total || validationState.planned}`; e.className = 'badge live'; });
  renderValidation();
}

function validationRunComplete(d) {
  const idx = validationState.runs.findIndex(r => r.run_id === d.run_id);
  const base = idx >= 0 ? validationState.runs[idx] : { run_id: d.run_id, kind: d.kind, label: d.label };
  const merged = {
    ...base,
    status:        'complete',
    net_profit:    d.net_profit,
    profit_factor: d.profit_factor,
    calmar:        d.calmar,
    max_drawdown:  d.max_drawdown,
    total_trades:  d.total_trades,
    passing:       d.passing,
  };
  if (idx >= 0) validationState.runs[idx] = merged;
  else validationState.runs.push(merged);
  validationState.complete++;
  renderValidation();
}

function validationDone(d) {
  validationState.active = false;
  const label = ({
    RECOMMENDED:  'Passed',
    RISKY:        'Mixed',
    NOT_RELIABLE: 'Failed',
  })[d.verdict] || 'Done';
  const cls = d.verdict === 'RECOMMENDED' ? 'live' : '';
  safe('val-badge', e => { e.textContent = label; e.className = `badge ${cls}`; });
  renderValidation();
}

function showEarlyTermination(d) {
  const banner = el('early-term-banner');
  if (!banner) return;
  const reason = d.reason || 'unknown';
  const cls = {
    targets_met:       'success',
    no_profit:         'critical',
    budget_exhausted:  'warning',
    stuck_escape:      'warning',
    user_stop:         'warning',
  }[reason] || 'warning';
  banner.className = `early-term-banner ${cls} active`;

  const icon = {
    targets_met: '🎯',
    no_profit:   '⚠',
    budget_exhausted: '⏱',
    stuck_escape: '↻',
    user_stop: '⏹',
  }[reason] || '⚠';
  safe('early-term-icon', e => { e.textContent = icon; });

  const heading = {
    targets_met:      'Targets Met — Optimization Complete',
    no_profit:        'Optimization Stopped Early',
    budget_exhausted: 'Time Budget Exhausted',
    stuck_escape:     'Optimizer Stuck',
    user_stop:        'Stopped by User',
  }[reason] || 'Optimization Event';
  safe('early-term-heading', e => { e.textContent = heading; });

  safe('early-term-msg', e => { e.textContent = d.message || ''; });

  // Render details as chips
  const det = d.details || {};
  const chips = Object.entries(det)
    .filter(([k, v]) => typeof v !== 'object' && v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<span>${escapeHtml(k)}: <strong style="color:var(--text)">${escapeHtml(String(v))}</strong></span>`)
    .join('');
  safe('early-term-details', e => { e.innerHTML = chips; });
}

function dismissEarlyTerm() {
  safe('early-term-banner', e => { e.classList.remove('active'); });
}

/* ── Socket wiring for new events ─────────────────────────────────── */

socket.on('ai_thinking', (d) => {
  addThinking(d);
});

/* ── Live AI token streaming ─────────────────────────────────────
   Claude streams the reasoning text token-by-token via SSE; the
   pipeline forwards each chunk as `ai_thinking_chunk`. We render
   it as a single growing bubble that finalises on `end`. */
let _streamingEntry = null;     // index into thinkingFeedState.entries while open
socket.on('ai_thinking_chunk', (d) => {
  const evt = d.event;
  if (evt === 'start') {
    // Open a new entry. Render-loop will paint as deltas arrive.
    thinkingFeedState.entries.push({
      msg:       '',
      kind:      'reasoning',
      streaming: true,
      time:      nowStr(),
      phase:     d.phase,
    });
    if (thinkingFeedState.entries.length > MAX_THINKING_ENTRIES) {
      thinkingFeedState.entries.shift();
    }
    _streamingEntry = thinkingFeedState.entries.length - 1;
    safe('tf-badge', e => { e.textContent = 'Streaming'; e.className = 'badge live'; });
    renderThinkingFeed();
  } else if (evt === 'delta' && _streamingEntry != null) {
    const e = thinkingFeedState.entries[_streamingEntry];
    if (e) {
      e.msg += d.text || '';
      renderThinkingFeed();
    }
  } else if (evt === 'end' && _streamingEntry != null) {
    const e = thinkingFeedState.entries[_streamingEntry];
    if (e) {
      e.streaming = false;
      renderThinkingFeed();
    }
    _streamingEntry = null;
    safe('tf-badge', e => { e.textContent = 'Live'; e.className = 'badge live'; });
  } else if (evt === 'error') {
    if (_streamingEntry != null) {
      const e = thinkingFeedState.entries[_streamingEntry];
      if (e) { e.kind = 'warning'; e.streaming = false; e.msg += ' [stream error: ' + (d.error || '?') + ']'; }
    }
    _streamingEntry = null;
    renderThinkingFeed();
  }
});

socket.on('param_changes', (d) => {
  addParamChanges(d);
});

socket.on('validation_start', (d) => {
  // Reset the panel for a fresh validation phase
  validationState.runs = [];
  validationState.planned = d.planned_runs || 0;
  validationState.complete = 0;
  validationState.active = true;
  safe('val-badge', e => { e.textContent = `0/${d.planned_runs}`; e.className = 'badge live'; });
  renderValidation();
  addLog(`Validation: planning ${d.planned_runs} test runs`, 'info');
});

socket.on('validation_run_start', (d) => {
  validationRunStart(d);
});

socket.on('validation_run_complete', (d) => {
  validationRunComplete(d);
});

socket.on('validation_done', (d) => {
  validationDone(d);
});

socket.on('early_termination', (d) => {
  showEarlyTermination(d);
  addLog(`⚠ ${d.message}`, d.reason === 'targets_met' ? 'success' : 'warn');
});

/* ── Also capture change_records from ai_iteration_start as a fallback ── */
socket.on('ai_iteration_start', (d) => {
  // (existing handler runs first — this just ensures param-changes panel is populated
  //  even if the dedicated param_changes event is missed for any reason)
  if (d.change_records && d.change_records.length) {
    const already = paramChangesState.iterations.find(i => i.iteration === d.iteration);
    if (!already) {
      addParamChanges({
        iteration:  d.iteration,
        changes:    d.change_records,
        analysis:   d.analysis,
        confidence: d.confidence,
      });
    }
  }
});

/* ══════════════════════════════════════════════════════════════════════
   STOP BUTTON — uses HTTP POST, not socket emit
══════════════════════════════════════════════════════════════════════ */

safe('btn-stop', stopBtn => {
  stopBtn.addEventListener('click', async () => {
    addLog('Stopping optimization...', 'warn');
    stopBtn.classList.remove('active');
    try {
      await fetch('/api/stop', { method: 'POST' });
      setRunningState(false);
      addLog('Stop signal sent.', 'warn');
    } catch (e) {
      addLog('Stop request failed: ' + e.message, 'error');
    }
  });
});

/* Pause button removed — see dashboard.html. Pipeline only supports Stop. */

/* ══════════════════════════════════════════════════════════════════════
   HISTORY RESTORATION
   Loads past runs from /api/history and /api/ai_insight/latest so
   reopening the dashboard shows progressive data, not a blank slate.
══════════════════════════════════════════════════════════════════════ */

async function restoreHistory() {
  try {
    const [histResp, aiResp, liveResp, settingsResp] = await Promise.all([
      fetch('/api/history'),
      fetch('/api/ai_insight/latest'),
      fetch('/api/live_activity'),
      fetch('/api/settings'),
    ]);

    // Note whether the API key is set so the AI panel can show a useful empty state
    try {
      const s = await settingsResp.json();
      state.aiKeySet = !!(s && s.ai && s.ai.anthropic_api_key_set);
    } catch (_) {}
    refreshAIWaitingState();

    const runs = await histResp.json();
    if (Array.isArray(runs) && runs.length) {
      // Populate recent runs table (last 8, newest first)
      const sorted = runs.slice().reverse();
      state.runs = runs.length;
      safe('m-runs', e => { e.textContent = state.runs; });

      sorted.slice(0, 8).forEach(r => addRecentRun(r));

      // Push equity sparkline from net profits
      runs.forEach(r => {
        if (r.net_profit != null) {
          pushEquityPoint(r.net_profit, r.net_profit, r.run_id || '');
          pushDDPoint(r.max_drawdown || 0, r.run_id || '');
          pushSparkData('profit', r.net_profit);
          pushSparkData('dd', r.max_drawdown || 0);
          pushSparkData('pf', r.profit_factor || 0);
          pushSparkData('trades', r.total_trades || 0);
          pushSparkData('runs', state.runs);
        }
      });

      // Show best metrics
      const passing = runs.filter(r => r.passing);
      if (passing.length) {
        const best = passing.reduce((a, b) => (a.net_profit > b.net_profit ? a : b));
        updateMetricCards(best);
        addLog('Restored ' + runs.length + ' past runs from history.', 'info');
      }
    }

    // Restore AI panel if insight exists
    const insight = await aiResp.json();
    if (insight) {
      updateAIPanel(insight);
      addLog('AI insight restored (run: ' + (insight.run_id || '?') + ').', 'info');
    }

    // ── Restore Live Intelligence (thinking feed, param changes, validation, banner) ──
    // This is what makes a refresh-mid-run feel like nothing happened.
    const live = await liveResp.json();
    if (live) {
      // Thinking feed
      const thinking = Array.isArray(live.thinking) ? live.thinking : [];
      thinking.forEach(t => addThinking(t));

      // Param changes
      const pcs = Array.isArray(live.param_changes) ? live.param_changes : [];
      pcs.forEach(pc => addParamChanges(pc));

      // Validation panel
      const val = Array.isArray(live.validation) ? live.validation : [];
      val.forEach(v => {
        if (v.event === 'validation_start') {
          validationState.runs = [];
          validationState.planned = v.planned_runs || 0;
          validationState.complete = 0;
          validationState.active = true;
        } else if (v.event === 'validation_run_start') {
          validationRunStart(v);
        } else if (v.event === 'validation_run_complete') {
          validationRunComplete(v);
        } else if (v.event === 'validation_done') {
          validationDone(v);
        }
      });
      if (val.length) renderValidation();

      // Early-termination banner
      if (live.early_termination) {
        showEarlyTermination(live.early_termination);
      }

      // Phase tracker — restore active phase even if no events have arrived yet
      const phaseMapLocal = { phase1: 1, phase2: 2, phase2_ai: 2, phase3: 3, phase3_oos: 3, phase3_sens: 3 };
      const ph = phaseMapLocal[live.phase] || (live.running ? 1 : 0);
      if (ph) {
        setPhaseActive(ph, { mode: live.phase_mode });
      }
      if (live.running) {
        setRunningState(true, live.phase || 'Optimizing...');
      }

      if (thinking.length || pcs.length || val.length) {
        addLog(
          `Restored live activity: ${thinking.length} thinking, `
          + `${pcs.length} param-change iterations, ${val.length} validation events.`,
          'info'
        );
      }
    }
  } catch (e) {
    addLog('History restore: ' + e.message, 'warn');
  }
}

/* ══════════════════════════════════════════════════════════════════════
   SETTINGS MODAL
══════════════════════════════════════════════════════════════════════ */

function openSettings() {
  safe('settings-overlay', e => { e.style.display = 'flex'; });
  loadSettings();
}

function closeSettings() {
  safe('settings-overlay', e => { e.style.display = 'none'; });
}

function switchSettingsTab(tab) {
  document.querySelectorAll('.settings-tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.querySelectorAll('.settings-panel').forEach(p => {
    p.classList.toggle('active', p.id === 'stab-' + tab);
  });
}

function toggleSettingsBool(btn) {
  btn.classList.toggle('on');
}

function setBoolToggle(id, value) {
  const btn = el(id);
  if (!btn) return;
  btn.classList.toggle('on', !!value);
}

function getBoolToggle(id) {
  const btn = el(id);
  return btn ? btn.classList.contains('on') : false;
}

function setVal(id, value) {
  const e = el(id);
  if (e) e.value = value != null ? value : '';
}

function getVal(id) {
  const e = el(id);
  return e ? e.value : '';
}

async function loadSettings() {
  try {
    const resp = await fetch('/api/settings');
    const cfg = await resp.json();

    // AI tab
    setBoolToggle('s-ai-enabled', cfg.ai && cfg.ai.enabled !== false);
    setVal('s-ai-key', (cfg.ai && cfg.ai.anthropic_api_key) || '');
    setVal('s-ai-model', (cfg.ai && cfg.ai.model) || 'claude-opus-4-7');
    setVal('s-ai-timeout', (cfg.ai && cfg.ai.timeout_seconds) || 30);

    // MT5 tab
    setVal('s-mt5-exe',     (cfg.mt5 && cfg.mt5.terminal_exe) || '');
    setVal('s-mt5-appdata', (cfg.mt5 && cfg.mt5.appdata_path) || '');
    setVal('s-mt5-mql5',   (cfg.mt5 && cfg.mt5.mql5_files_path) || '');
    setVal('s-mt5-timeout', (cfg.mt5 && cfg.mt5.tester_timeout_seconds) || 120);
    setVal('s-mt5-model',   (cfg.mt5 && cfg.mt5.tester_model) || 1);

    // Broker tab
    setVal('s-broker-tz',      (cfg.broker && cfg.broker.timezone_offset_hours) != null ? cfg.broker.timezone_offset_hours : 3);
    setVal('s-broker-deposit',  (cfg.broker && cfg.broker.deposit) || 10000);
    setVal('s-broker-leverage', (cfg.broker && cfg.broker.leverage) || 500);

    // Thresholds tab
    setVal('s-thr-trades',  (cfg.thresholds && cfg.thresholds.min_trades) || 30);
    setVal('s-thr-pf',      (cfg.thresholds && cfg.thresholds.min_profit_factor) || 1.2);
    setVal('s-thr-calmar',  (cfg.thresholds && cfg.thresholds.min_calmar) || 0.5);
    setVal('s-thr-oos',     (cfg.thresholds && cfg.thresholds.max_oos_degradation) || 0.3);
    setVal('s-thr-sens',    (cfg.thresholds && cfg.thresholds.sensitivity_tolerance) || 0.15);
  } catch (e) {
    showSettingsMsg('Failed to load settings: ' + e.message, false);
  }
}

async function saveSettings() {
  const btn = el('settings-save-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }

  const payload = {
    ai: {
      enabled:           getBoolToggle('s-ai-enabled'),
      anthropic_api_key: getVal('s-ai-key').trim(),
      model:             getVal('s-ai-model'),
      timeout_seconds:   parseFloat(getVal('s-ai-timeout')) || 30,
    },
    mt5: {
      terminal_exe:           getVal('s-mt5-exe').trim(),
      appdata_path:           getVal('s-mt5-appdata').trim(),
      mql5_files_path:        getVal('s-mt5-mql5').trim(),
      tester_timeout_seconds: parseInt(getVal('s-mt5-timeout'), 10) || 120,
      tester_model:           parseInt(getVal('s-mt5-model'), 10) || 1,
    },
    broker: {
      timezone_offset_hours: parseFloat(getVal('s-broker-tz')) || 3,
      deposit:               parseFloat(getVal('s-broker-deposit')) || 10000,
      leverage:              parseInt(getVal('s-broker-leverage'), 10) || 500,
    },
    thresholds: {
      min_trades:            parseInt(getVal('s-thr-trades'), 10) || 30,
      min_profit_factor:     parseFloat(getVal('s-thr-pf')) || 1.2,
      min_calmar:            parseFloat(getVal('s-thr-calmar')) || 0.5,
      max_oos_degradation:   parseFloat(getVal('s-thr-oos')) || 0.3,
      sensitivity_tolerance: parseFloat(getVal('s-thr-sens')) || 0.15,
    },
  };

  try {
    const resp = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.ok) {
      showSettingsMsg('Settings saved.', true);
      addLog('Settings saved successfully.', 'success');
      setTimeout(closeSettings, 1200);
    } else {
      showSettingsMsg('Error: ' + (data.error || 'Unknown error'), false);
    }
  } catch (e) {
    showSettingsMsg('Save failed: ' + e.message, false);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Save Settings'; }
  }
}

function showSettingsMsg(msg, ok) {
  safe('settings-save-msg', e => {
    e.textContent = msg;
    e.className = 'settings-save-msg ' + (ok ? 'ok' : 'err');
    e.style.opacity = '1';
    setTimeout(() => { e.style.opacity = '0'; }, 3000);
  });
}

/* ══════════════════════════════════════════════════════════════════════
   AI PANEL TOGGLE
══════════════════════════════════════════════════════════════════════ */

function toggleAIDetails() {
  const details = el('ai-details');
  if (!details) return;
  const isOpen = details.classList.contains('open');
  details.classList.toggle('open', !isOpen);
  const label = isOpen ? 'View Details →' : '▲ Collapse';
  safe('ai-expand-btn',  e => { e.textContent = label; });
  safe('ai-view-details', e => { e.textContent = label; });
}

/* ══════════════════════════════════════════════════════════════════════
   RUN DETAIL MODAL
══════════════════════════════════════════════════════════════════════ */

async function openRunDetail(run_id) {
  if (!run_id) return;

  // Show modal immediately with loading state
  safe('run-detail-overlay', e => { e.style.display = 'flex'; });
  safe('rd-run-id',       e => { e.textContent = run_id; });
  safe('rd-meta',         e => { e.textContent = 'Loading…'; });
  safe('rd-metrics',      e => { e.innerHTML = ''; });
  safe('rd-params-tbody', e => { e.innerHTML = '<tr><td colspan="2" class="empty-state">Loading…</td></tr>'; });
  safe('rd-ai-section',   e => { e.style.display = 'none'; });
  safe('rd-footer',       e => { e.innerHTML = '<button class="verdict-btn ghost" onclick="closeRunDetail()">Close</button>'; });
  // Clear any lingering evolution section from a previous openBestResult call
  const existingEvo = document.getElementById('rd-evolution-section');
  if (existingEvo) existingEvo.remove();

  try {
    const resp = await fetch('/api/run/' + encodeURIComponent(run_id));
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const d = await resp.json();

    // Header
    safe('rd-run-id', e => { e.textContent = d.run_id || run_id; });
    const phase = (d.phase || '—').replace(/_/g, ' ');
    const ts    = d.ts ? new Date(d.ts).toLocaleString() : '—';
    safe('rd-meta', e => { e.textContent = 'Phase: ' + phase + '  ·  ' + ts; });

    // Metrics grid
    const profit = parseFloat(d.net_profit     || 0);
    const pf     = parseFloat(d.profit_factor  || 0);
    const calmar = parseFloat(d.calmar         || 0);
    const dd     = parseFloat(d.drawdown_pct   || d.max_drawdown || 0);
    const wr     = parseFloat(d.win_rate       || 0);
    const trades = parseInt(d.total_trades     || 0, 10);
    const score  = parseFloat(d.score          || 0);

    const metrics = [
      { label: 'Net Profit',    val: fmtMoney(profit),          color: profit >= 0 ? 'var(--green)' : 'var(--red)' },
      { label: 'Profit Factor', val: pf.toFixed(2),             color: pf >= 1.5 ? 'var(--green)' : pf >= 1 ? 'var(--yellow)' : 'var(--red)' },
      { label: 'Calmar Ratio',  val: calmar.toFixed(2),         color: 'var(--teal)' },
      { label: 'Max Drawdown',  val: dd.toFixed(1) + '%',       color: 'var(--red)' },
      { label: 'Win Rate',      val: wr.toFixed(1) + '%',       color: 'var(--text)' },
      { label: 'Total Trades',  val: trades.toLocaleString(),   color: 'var(--text)' },
      { label: 'Score',         val: score.toFixed(4),          color: 'var(--accent2)' },
      { label: 'Phase',         val: phase,                     color: 'var(--muted)' },
    ];
    safe('rd-metrics', e => {
      e.innerHTML = metrics.map(m =>
        `<div class="rd-metric">
          <div class="rd-metric-label">${escapeHtml(m.label)}</div>
          <div class="rd-metric-val" style="color:${m.color}">${escapeHtml(String(m.val))}</div>
        </div>`
      ).join('');
    });

    // Parameters
    const params = d.params || {};
    const paramEntries = Object.entries(params);
    safe('rd-params-tbody', e => {
      if (!paramEntries.length) {
        e.innerHTML = '<tr><td colspan="2" class="empty-state">No parameters available</td></tr>';
        return;
      }
      e.innerHTML = paramEntries.map(([key, val]) => {
        const display = typeof val === 'number' ? val.toFixed(val % 1 !== 0 ? 4 : 0) : val;
        return `<tr><td class="param-name">${escapeHtml(key)}</td><td class="param-val">${escapeHtml(String(display))}</td></tr>`;
      }).join('');
    });

    // AI Reasoning
    const ai = d.ai_insight;
    if (ai) {
      safe('rd-ai-section', e => { e.style.display = ''; });
      safe('rd-ai-content', e => {
        let html = '<div class="rd-ai-box">';
        const analysisText = ai.analysis || ai.diagnosis || ai.headline || '';
        if (analysisText) {
          html += '<div class="rd-ai-analysis">' + escapeHtml(analysisText) + '</div>';
        }
        const changes = ai.changes || ai.suggestions || [];
        if (changes.length) {
          html += '<div class="ai-sub-title" style="margin-bottom:0.4rem;">Suggested Changes</div>';
          html += '<div class="rd-ai-changes">';
          changes.forEach(c => {
            if (typeof c === 'object' && (c.param || c.parameter)) {
              const p = c.param || c.parameter;
              const v = c.value !== undefined ? c.value : (c.to !== undefined ? c.to : '?');
              html += `<div class="rd-ai-change">
                <span class="rd-ai-change-param">${escapeHtml(p)}</span>
                <span class="rd-ai-change-arrow">→</span>
                <span class="rd-ai-change-val">${escapeHtml(String(v))}</span>
              </div>`;
            } else if (typeof c === 'string') {
              html += `<div class="rd-ai-change"><span style="color:var(--text)">${escapeHtml(c)}</span></div>`;
            }
          });
          html += '</div>';
        }
        const conf = ai.confidence;
        if (conf !== undefined) {
          const confStr = typeof conf === 'number' ? (conf * 100).toFixed(0) + '%' : String(conf);
          html += `<div style="margin-top:0.65rem;font-size:0.7rem;color:var(--muted)">Confidence: <strong style="color:var(--text)">${escapeHtml(confStr)}</strong></div>`;
        }
        const gs = ai.goal_status;
        if (gs) {
          const flags = [
            gs.profit_factor_met ? '✓ PF' : '✗ PF',
            gs.drawdown_ok       ? '✓ DD' : '✗ DD',
            gs.calmar_met        ? '✓ Calmar' : '✗ Calmar',
          ].join('  ·  ');
          html += `<div style="margin-top:0.4rem;font-size:0.7rem;color:var(--muted)">Targets: ${escapeHtml(flags)}</div>`;
        }
        html += '</div>';
        e.innerHTML = html;
      });
    }

    // Footer
    safe('rd-footer', e => {
      let btns = '';
      if (d.set_url) {
        btns += `<a href="${escapeHtml(d.set_url)}" class="verdict-btn primary" download>⬇ Download .set</a>`;
      }
      const reportHref = '/reports/' + encodeURIComponent(d.run_id || run_id) + '/summary.html';
      btns += `<a href="${reportHref}" class="verdict-btn secondary" target="_blank">📋 View Report</a>`;
      btns += '<button class="verdict-btn ghost" onclick="closeRunDetail()">Close</button>';
      e.innerHTML = btns;
    });

  } catch (err) {
    safe('rd-meta', e => { e.textContent = 'Error: ' + err.message; });
  }
}

function closeRunDetail() {
  safe('run-detail-overlay', e => { e.style.display = 'none'; });
}

/* ══════════════════════════════════════════════════════════════════════
   BEST RESULT — shows full params, AI reasoning, AND evolution path
══════════════════════════════════════════════════════════════════════ */

async function openBestResult() {
  try {
    const resp = await fetch('/api/best_result');
    if (!resp.ok) {
      // No best yet — open a toast-like state in the run detail modal
      safe('run-detail-overlay', e => { e.style.display = 'flex'; });
      safe('rd-run-id',       e => { e.textContent = 'Best Result'; });
      safe('rd-meta',         e => { e.textContent = 'No best result yet — optimization must complete at least one passing run.'; });
      safe('rd-metrics',      e => { e.innerHTML = ''; });
      safe('rd-params-tbody', e => { e.innerHTML = '<tr><td colspan="2" class="empty-state">—</td></tr>'; });
      safe('rd-ai-section',   e => { e.style.display = 'none'; });
      safe('rd-footer',       e => { e.innerHTML = '<button class="verdict-btn ghost" onclick="closeRunDetail()">Close</button>'; });
      return;
    }
    const d = await resp.json();
    // Delegate to the existing openRunDetail, then append evolution path
    await openRunDetail(d.run_id);
    // Append evolution path as an extra section inside the modal body
    const body = document.querySelector('.run-detail-body');
    if (!body) return;
    let evoSection = document.getElementById('rd-evolution-section');
    if (!evoSection) {
      evoSection = document.createElement('div');
      evoSection.id = 'rd-evolution-section';
      body.appendChild(evoSection);
    }
    const evo = d.evolution || [];
    if (!evo.length) {
      evoSection.innerHTML = '<div class="rd-section-title">Evolution Path</div><div style="font-size:0.76rem;color:var(--muted);padding:0.5rem 0;">No evolution data available for this run.</div>';
      return;
    }
    // ── Replay scrubber: slider that walks through evolution one step at a time ──
    const bestIdx = Math.max(0, evo.findIndex(r => r.is_best));
    evoSection.innerHTML = `
      <div class="rd-section-title">
        Evolution Path
        <span style="font-weight:500;color:var(--muted);letter-spacing:0.02em;text-transform:none;font-size:0.65rem">— ${evo.length} steps to this best result · drag the slider to replay</span>
      </div>

      <div id="evo-scrubber-wrap" style="background:rgba(255,255,255,0.02);border:1px solid var(--border);border-radius:10px;padding:0.85rem 1rem;margin-bottom:0.5rem;">

        <!-- Step indicator + slider -->
        <div style="display:flex;align-items:center;gap:0.75rem;margin-bottom:0.6rem;">
          <button id="evo-prev-btn" style="background:rgba(255,255,255,0.05);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:0.85rem;width:28px;height:28px;border-radius:6px;cursor:pointer;flex-shrink:0;">‹</button>
          <input type="range" id="evo-slider" min="0" max="${evo.length - 1}" value="${bestIdx}"
                 style="flex:1;accent-color:var(--accent2);cursor:pointer;">
          <button id="evo-next-btn" style="background:rgba(255,255,255,0.05);border:1px solid var(--border);color:var(--text);font-family:inherit;font-size:0.85rem;width:28px;height:28px;border-radius:6px;cursor:pointer;flex-shrink:0;">›</button>
          <button id="evo-play-btn" style="background:rgba(79,70,229,0.15);border:1px solid rgba(79,70,229,0.4);color:var(--accent2);font-family:inherit;font-size:0.7rem;padding:4px 10px;border-radius:6px;cursor:pointer;flex-shrink:0;font-weight:600;">▶ Play</button>
        </div>

        <!-- Step header -->
        <div style="display:flex;align-items:center;gap:0.5rem;font-size:0.72rem;flex-wrap:wrap;margin-bottom:0.5rem;">
          <span id="evo-step-num" style="font-family:'JetBrains Mono',monospace;color:var(--muted);font-weight:600;"></span>
          <span id="evo-step-id" style="font-family:'JetBrains Mono',monospace;color:var(--accent2);"></span>
          <span id="evo-step-phase" style="color:var(--muted);font-size:0.6rem;text-transform:uppercase;letter-spacing:0.06em;"></span>
          <span id="evo-step-best"></span>
          <span style="margin-left:auto;font-size:0.65rem;font-family:'JetBrains Mono',monospace;color:var(--muted);">score <span id="evo-step-score" style="color:var(--text);font-weight:600;"></span></span>
        </div>

        <!-- Metrics for this step -->
        <div id="evo-step-metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:0.4rem;margin-bottom:0.55rem;"></div>

        <!-- Changes -->
        <div id="evo-step-changes" style="font-size:0.7rem;font-family:'JetBrains Mono',monospace;color:var(--teal);line-height:1.5;"></div>

        <!-- AI analysis -->
        <div id="evo-step-analysis" style="font-size:0.72rem;color:#94a3b8;margin-top:0.45rem;line-height:1.55;"></div>
      </div>
    `;

    // Wire scrubber
    const slider  = document.getElementById('evo-slider');
    const stepNum = document.getElementById('evo-step-num');
    const stepId  = document.getElementById('evo-step-id');
    const stepPh  = document.getElementById('evo-step-phase');
    const stepBst = document.getElementById('evo-step-best');
    const stepScr = document.getElementById('evo-step-score');
    const stepMtr = document.getElementById('evo-step-metrics');
    const stepChg = document.getElementById('evo-step-changes');
    const stepAna = document.getElementById('evo-step-analysis');
    const prevBtn = document.getElementById('evo-prev-btn');
    const nextBtn = document.getElementById('evo-next-btn');
    const playBtn = document.getElementById('evo-play-btn');

    function paintStep(i) {
      const r = evo[i];
      if (!r) return;
      stepNum.textContent = `Step ${i + 1} / ${evo.length}`;
      stepId.textContent  = r.run_id || '';
      stepPh.textContent  = (r.phase || '').replace(/_/g, ' ');
      stepBst.innerHTML   = r.is_best ? ' <span style="background:rgba(16,185,129,0.18);color:var(--green);padding:0.06rem 0.4rem;border-radius:4px;font-size:0.55rem;font-weight:700;letter-spacing:0.04em;">BEST</span>' : '';
      stepScr.textContent = Number(r.score || 0).toFixed(3);

      const cells = [
        { l: 'PF',     v: Number(r.profit_factor || 0).toFixed(2),     c: 'var(--yellow)' },
        { l: 'Calmar', v: Number(r.calmar || 0).toFixed(2),            c: 'var(--teal)' },
        { l: 'DD',     v: Number(r.max_drawdown || 0).toFixed(1) + '%', c: 'var(--red)' },
        { l: 'Profit', v: fmtMoney(r.net_profit || 0),                  c: r.net_profit >= 0 ? 'var(--green)' : 'var(--red)' },
      ];
      stepMtr.innerHTML = cells.map(c => `<div style="background:rgba(255,255,255,0.03);padding:5px 8px;border-radius:5px;"><div style="font-size:0.55rem;color:var(--muted);text-transform:uppercase;letter-spacing:0.05em;">${c.l}</div><div style="font-size:0.85rem;font-weight:700;font-family:'JetBrains Mono',monospace;color:${c.c};">${escapeHtml(c.v)}</div></div>`).join('');

      const changes = r.changes || [];
      if (changes.length) {
        stepChg.innerHTML = '<strong style="color:var(--muted);font-weight:500;text-transform:uppercase;letter-spacing:0.06em;font-size:0.6rem;">Δ</strong> ' + changes.slice(0, 4).map(c => {
          const p = c.param || c.parameter || '?';
          const v = c.value !== undefined ? c.value : (c.to !== undefined ? c.to : '?');
          return `${escapeHtml(p)}=${escapeHtml(String(v))}`;
        }).join(', ');
      } else {
        stepChg.innerHTML = '<em style="color:var(--muted);">— no parameter changes (LHS seed)</em>';
      }
      stepAna.textContent = r.analysis || '';
      slider.value = i;
    }

    slider.addEventListener('input', () => paintStep(parseInt(slider.value, 10)));
    prevBtn.addEventListener('click', () => paintStep(Math.max(0, parseInt(slider.value, 10) - 1)));
    nextBtn.addEventListener('click', () => paintStep(Math.min(evo.length - 1, parseInt(slider.value, 10) + 1)));

    // Play: auto-step ~700ms intervals
    let playTimer = null;
    playBtn.addEventListener('click', () => {
      if (playTimer) {
        clearInterval(playTimer); playTimer = null;
        playBtn.textContent = '▶ Play';
        return;
      }
      playBtn.textContent = '⏸ Pause';
      let pos = parseInt(slider.value, 10);
      if (pos >= evo.length - 1) pos = 0;
      playTimer = setInterval(() => {
        pos++;
        if (pos >= evo.length) {
          clearInterval(playTimer); playTimer = null;
          playBtn.textContent = '▶ Play';
          return;
        }
        paintStep(pos);
      }, 700);
    });

    paintStep(bestIdx);  // start showing the best step
  } catch (err) {
    addLog('Failed to open best result: ' + err.message, 'error');
  }
}

/* ══════════════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════════════ */

(function init() {
  initSparklines();
  restoreHistory();
  startPolling();
  addLog('APEX Dashboard initialized.', 'success');
})();
