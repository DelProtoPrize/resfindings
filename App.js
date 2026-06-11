// app.js — fetch from our own API and render. The browser never touches Sleeper
// or any external source; it only reads pre-computed analytics from our backend.
//
// CHANGES (KPI + tape wiring, presentation-only otherwise):
//   1. KPI strip: Portfolio Value + HHI wired with REAL data, scoped — league
//      aggregates by default, selected roster after a drill-in. Sharpe/Alpha
//      stay untouched (their engines don't exist yet; cards remain PENDING).
//   2. Ticker tape: wired from the /value endpoint already fetched for the
//      triangulation scatter. Items = largest |VBD − FP| wedges (the
//      off-diagonal players from the scatter). No FC arbitrage here — that
//      endpoint doesn't serve fc values, so nothing is invented.
//   3. All new DOM writes are null-safe: this file still works on the old
//      index.html (no KPI/tape elements) without errors.
//   4. Plotly grid/font constants aligned to the new design tokens.

const api = (path) => fetch(`/api${path}`).then((r) => r.json());
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
const leagueSel = document.getElementById('league');

const POS_COLOR = { QB: '#3d80f5', RB: '#3ecf74', WR: '#e8a838', TE: '#b47cf5' };
const INK = '#dde4ee', GRID = '#1e2530';

let currentLeague = null;
let currentRows = [];
let prodByRoster = null;   // roster_id -> production_vbd (null = endpoint absent)
let prodTotal = 0;
let projByRoster = null;   // projected basis (m1); null until project_production.py + route v2
let projTotal = 0;
let cornerCache = { realized: null, projected: null };  // per-league fetch cache
let cornerBasis = 'realized';
let valueTotal = 0;

/* ── KPI strip (null-safe: absent on old index.html) ───────────────────── */
function setKpi(id, value, sub, tone) {
  const card = document.getElementById(id);
  if (!card) return;
  card.classList.remove('unwired', 'kpi-accent', 'kpi-good', 'kpi-warn', 'kpi-bad');
  if (tone) card.classList.add(tone);
  card.querySelector('.kpi-value').textContent = value;
  const subEl = card.querySelector('.kpi-sub');
  if (subEl) subEl.innerHTML = sub;
}

const hhiTone = (h) => (h > 0.2 ? 'kpi-bad' : h > 0.15 ? 'kpi-warn' : 'kpi-good');
const pctShare = (x, total) => (total > 0 && x != null ? ((x / total) * 100).toFixed(1) + '%' : '–');
const median = (xs) => {
  const s = [...xs].sort((a, b) => a - b);
  return s.length ? (s[(s.length - 1) >> 1] + s[s.length >> 1]) / 2 : null;
};

function leagueKpis(rows) {
  if (!rows.length) return;
  valueTotal = rows.reduce((s, d) => s + (d.team_value || 0), 0);
  const top = rows.find((d) => d.value_rank === 1) || rows[0];
  setKpi('kpiPortfolio', fmt(Math.round(valueTotal)),
    `<span class="delta-tag flat">LEAGUE</span><span>market cap · top: ${top.owner_name || 'Roster ' + top.roster_id}</span>`,
    'kpi-accent');
  const med = median(rows.map((d) => Number(d.hhi)));
  const hi = rows.reduce((a, b) => (Number(a.hhi) > Number(b.hhi) ? a : b));
  setKpi('kpiHhi', med != null ? med.toFixed(3) : '–',
    `<span class="delta-tag flat">LEAGUE</span><span>median · most concentrated: ${hi.owner_name || 'Roster ' + hi.roster_id} (${Number(hi.hhi).toFixed(3)})</span>`,
    med != null ? hhiTone(med) : null);

  // Value Share — league leader by default ("league leader per metric").
  setKpi('kpiValueShare', pctShare(top.team_value, valueTotal),
    `<span class="delta-tag up">LEADER</span><span>${top.owner_name || 'Roster ' + top.roster_id} · click a team for its share</span>`,
    'kpi-accent');

  // Production Share — league leader, from the /production endpoint.
  if (prodByRoster) {
    let leadId = null, leadV = -1;
    for (const [rid, v] of Object.entries(prodByRoster)) {
      if (v > leadV) { leadV = v; leadId = Number(rid); }
    }
    const leadMeta = rows.find((d) => d.roster_id === leadId) || {};
    setKpi('kpiProdShare', pctShare(leadV, prodTotal),
      `<span class="delta-tag up">LEADER</span><span>${leadMeta.owner_name || 'Roster ' + leadId} · share of league VBD</span>`,
      'kpi-good');
  } else {
    setKpi('kpiProdShare', '–',
      `<span class="delta-tag todo">UNWIRED</span><span>add /leagues/:id/production (see server patch)</span>`,
      null);
  }
}

function shares(meta) {
  const vShare = valueTotal > 0 ? (meta.team_value || 0) / valueTotal : null;
  const pShare = prodByRoster && prodTotal > 0
    ? (prodByRoster[meta.roster_id] || 0) / prodTotal : null;
  return { vShare, pShare };
}

function rosterKpis(meta) {
  setKpi('kpiPortfolio', fmt(meta.team_value),
    `<span class="delta-tag ${meta.value_rank <= 3 ? 'up' : 'flat'}">#${meta.value_rank}</span><span>${meta.owner_name || 'Roster ' + meta.roster_id} · click league to reset</span>`,
    'kpi-accent');
  const h = Number(meta.hhi);
  setKpi('kpiHhi', h.toFixed(3),
    `<span class="delta-tag ${h > 0.2 ? 'down' : h > 0.15 ? 'flat' : 'up'}">${h > 0.2 ? 'HIGH' : h > 0.15 ? 'MOD' : 'LOW'}</span><span>${meta.owner_name || 'roster'} concentration</span>`,
    hhiTone(h));

  const { vShare, pShare } = shares(meta);
  const projShare = projByRoster && projTotal > 0
    ? (projByRoster[meta.roster_id] || 0) / projTotal : null;
  setKpi('kpiValueShare', vShare != null ? (vShare * 100).toFixed(1) + '%' : '–',
    `<span class="delta-tag flat">TEAM</span><span>${meta.owner_name || 'roster'} share of league value</span>`,
    'kpi-accent');
  if (pShare != null && vShare != null) {
    // Production vs value gap = win-now vs rebuild tilt, in one number.
    const gap = pShare - vShare;
    const tag = gap > 0.01 ? 'up' : gap < -0.01 ? 'down' : 'flat';
    // Cause-neutral labels: a negative gap means market value runs ahead of
    // current production — could be youth, injury, or SF-QB scarcity. The
    // sign is known; the cause needs the roster table to confirm.
    const word = gap > 0.01 ? 'win-now tilt' : gap < -0.01 ? 'value ahead of production' : 'balanced';
    const projNote = projShare != null ? ` · proj ${(projShare * 100).toFixed(1)}%` : '';
    setKpi('kpiProdShare', (pShare * 100).toFixed(1) + '%',
      `<span class="delta-tag ${tag}">${gap >= 0 ? '+' : ''}${(gap * 100).toFixed(1)}pp vs value</span><span>${word}${projNote}</span>`,
      gap > 0.01 ? 'kpi-good' : gap < -0.01 ? 'kpi-warn' : 'kpi-accent');
  } else {
    setKpi('kpiProdShare', '–',
      `<span class="delta-tag todo">UNWIRED</span><span>add /leagues/:id/production (see server patch)</span>`,
      null);
  }
}

/* ── Tape: win-now wedge = |VBD − FP|, the scatter's off-diagonal players.
      Fed by the SAME /value rows the triangulation fetches — nothing extra,
      nothing invented. Stays in placeholder state if the VBD layer is empty. */
function wireTape(rows) {
  const wrap = document.getElementById('tapeInner');
  if (!wrap) return;
  const usable = rows.filter((d) => d.fp_market_value != null && d.vbd_value != null);
  if (!usable.length) return; // keep honest placeholder
  const items = [...usable]
    .map((d) => ({ ...d, wedge: d.vbd_value - d.fp_market_value }))
    .sort((a, b) => Math.abs(b.wedge) - Math.abs(a.wedge))
    .slice(0, 14)
    .map((d) => {
      const col = POS_COLOR[d.position] || '#8a95a8';
      const wCol = d.wedge > 0 ? '#3ecf74' : '#f5605a';
      return `<div class="tape-item">
        <span class="pos-badge" style="background:${col}22;color:${col}">${d.position}</span>
        <span class="name">${d.player_name}</span>
        <span class="val">FP ${fmt(Math.round(d.fp_market_value))}</span>
        <span class="delta" style="color:${wCol}" title="VBD minus FP market value">${d.wedge > 0 ? '+' : ''}${fmt(Math.round(d.wedge))} wedge</span>
      </div>`;
    }).join('');
  wrap.innerHTML = items + items; // duplicated for seamless loop
  wrap.classList.remove('unwired');
}

async function init() {
  const leagues = await api('/leagues');
  // Show the season so repeated league names (one row per season) are distinguishable.
  leagueSel.innerHTML = leagues
    .map((l) => `<option value="${l.league_id}">${l.league_name} (${l.season})${l.is_superflex ? ' · SF' : ''}</option>`)
    .join('');
  leagueSel.addEventListener('change', () => render(leagueSel.value));
  if (leagues.length) render(leagues[0].league_id);
}

async function render(leagueId) {
  currentLeague = leagueId;
  document.getElementById('rosterPanel').style.display = 'none';
  const rows = await api(`/leagues/${leagueId}/diagnostics`);
  currentRows = rows;
  // Production sums (graceful: cards stay dashed if the route isn't deployed).
  prodByRoster = null; prodTotal = 0; projByRoster = null; projTotal = 0;
  try {
    const prod = await api(`/leagues/${leagueId}/production`);
    if (Array.isArray(prod)) {
      prodByRoster = Object.fromEntries(prod.map((p) => [p.roster_id, p.production_vbd || 0]));
      prodTotal = prod.reduce((s, p) => s + (p.production_vbd || 0), 0);
    }
  } catch { /* endpoint absent — leave null */ }
  try {
    const proj = await api(`/leagues/${leagueId}/production?basis=projected`);
    if (Array.isArray(proj) && proj.length) {
      projByRoster = Object.fromEntries(proj.map((p) => [p.roster_id, p.production_vbd || 0]));
      projTotal = proj.reduce((s, p) => s + (p.production_vbd || 0), 0);
    }
  } catch { /* projection layer not built — pills show a dash */ }
  leagueKpis(rows);

  // Horizontal bar of team value, colored by HHI concentration (red = top-heavy).
  Plotly.react('valueChart', [{
    type: 'bar', orientation: 'h',
    x: rows.map((d) => d.team_value).reverse(),
    y: rows.map((d) => d.owner_name || `Roster ${d.roster_id}`).reverse(),
    marker: { color: rows.map((d) => d.hhi).reverse(), colorscale: 'YlOrRd',
              showscale: true, colorbar: { title: 'HHI' } },
    hovertemplate: '%{y}<br>value %{x:,.0f}<extra></extra>',
  }], {
    margin: { l: 170, r: 40, t: 10, b: 40 },
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
    font: { color: INK }, xaxis: { gridcolor: GRID },
  }, { displayModeBar: false, responsive: true });

  // Diagnostics table — rows are clickable to drill into a roster.
  document.getElementById('diagTable').innerHTML = `
    <table><thead><tr>
      <th>Rank</th><th>Team</th><th class="num">Value</th>
      <th class="num">Assets</th><th class="num">Pctile</th><th class="num">HHI</th>
    </tr></thead><tbody>
      ${rows.map((d) => `<tr class="clickable" data-roster="${d.roster_id}">
        <td>${d.value_rank}</td>
        <td>${d.owner_name || 'Roster ' + d.roster_id}</td>
        <td class="num">${fmt(d.team_value)}</td>
        <td class="num">${d.n_assets}</td>
        <td class="num">${(d.value_percentile * 100).toFixed(0)}%</td>
        <td class="num">${Number(d.hhi).toFixed(3)}</td>
      </tr>`).join('')}
    </tbody></table>`;

  document.querySelector('#diagTable tbody').addEventListener('click', (e) => {
    const tr = e.target.closest('tr[data-roster]');
    if (tr) drillRoster(Number(tr.dataset.roster));
  });

  renderTriangulation(leagueId);
  renderCornering(leagueId);
}

/* ── Positional Cornering (Step 4 UI) ──────────────────────────────────────
   Who controls the scarce production, per position, against the FIXED
   realized replacement bar. Both bases come from the same route; HHIs are
   never compared across bases (projected runs mechanically hot — shrinkage
   thins the elite pool). The durability line is a WITHIN-TEAM realized→
   projected share delta, rendered as text on the diagnostic strip — the one
   cross-basis read the caveat permits. */
const ownerName = (rid) =>
  (currentRows.find((r) => r.roster_id === rid) || {}).owner_name || `Roster ${rid}`;
const MUTED_SEG = ['#2a3340', '#242c38', '#1f2630', '#343e4d'];

async function renderCornering(leagueId) {
  const chartEl = document.getElementById('cornerChart');
  if (!chartEl) return;                       // old page — feature absent
  cornerCache = { realized: null, projected: null };
  cornerBasis = 'realized';
  try {
    const r = await api(`/leagues/${leagueId}/cornering?basis=realized`);
    if (r && Array.isArray(r.league) && r.league.length) cornerCache.realized = r;
  } catch { /* route absent — empty state stays */ }
  try {
    const r = await api(`/leagues/${leagueId}/cornering?basis=projected`);
    if (r && Array.isArray(r.league) && r.league.length) cornerCache.projected = r;
  } catch { /* projection layer absent — toggle disables below */ }

  const toggle = document.getElementById('cornerToggle');
  if (toggle && !toggle.dataset.wired) {
    toggle.dataset.wired = '1';
    toggle.addEventListener('click', (e) => {
      const btn = e.target.closest('button[data-basis]');
      if (!btn || btn.disabled) return;
      cornerBasis = btn.dataset.basis;
      toggle.querySelectorAll('button').forEach((b) =>
        b.setAttribute('aria-pressed', String(b.dataset.basis === cornerBasis)));
      drawCornering();
    });
  }
  if (toggle) {
    const projBtn = toggle.querySelector('button[data-basis="projected"]');
    if (projBtn) {
      projBtn.disabled = !cornerCache.projected;
      projBtn.title = cornerCache.projected ? '' :
        'Projected basis unavailable — run project_production.py, then cornering_metrics.py';
    }
  }
  drawCornering();
}

function drawCornering() {
  const chartEl = document.getElementById('cornerChart');
  const cardsEl = document.getElementById('cornerCards');
  const diagEl = document.getElementById('cornerDiag');
  if (!chartEl) return;
  const data = cornerCache[cornerBasis];
  if (!data) {
    chartEl.innerHTML = `<div class="state-msg">Cornering tables not built yet — run
      <code>python project_production.py</code> → <code>python cornering_metrics.py</code></div>`;
    if (cardsEl) cardsEl.innerHTML = '';
    if (diagEl) diagEl.style.display = 'none';
    return;
  }

  const league = [...data.league].sort((a, b) => (b.hhi ?? 0) - (a.hhi ?? 0));
  const positions = league.map((l) => l.position);
  const nTeams = currentRows.length || 14;
  const evenHhi = 1 / nTeams;

  // one trace per "share rank within position" so segment colors are per-cell:
  // top holder gets the position color, the field gets muted shades.
  const byPos = {};
  for (const row of data.rosters) (byPos[row.position] ??= []).push(row);
  for (const posRows of Object.values(byPos)) posRows.sort((a, b) => (b.vona_share ?? 0) - (a.vona_share ?? 0));
  const maxLen = Math.max(...Object.values(byPos).map((r) => r.length), 0);
  const traces = [];
  for (let k = 0; k < maxLen; k++) {
    const xs = [], cols = [], cd = [];
    for (const pos of positions) {
      const row = (byPos[pos] || [])[k];
      xs.push(row ? row.vona_share : 0);
      cols.push(k === 0 ? (POS_COLOR[pos] || '#8a95a8') : MUTED_SEG[k % MUTED_SEG.length]);
      cd.push(row ? [ownerName(row.roster_id), (row.vona_share * 100).toFixed(1), row.elite_count] : ['', '', '']);
    }
    traces.push({
      type: 'bar', orientation: 'h', x: xs, y: positions, marker: { color: cols },
      customdata: cd, showlegend: false,
      hovertemplate: '%{customdata[0]}: %{customdata[1]}% of %{y} VONA · %{customdata[2]} startable<extra></extra>',
    });
  }
  Plotly.react('cornerChart', traces, {
    barmode: 'stack', margin: { l: 44, r: 10, t: 6, b: 34 },
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
    font: { color: INK, size: 12 },
    xaxis: { tickformat: '.0%', gridcolor: GRID, range: [0, 1], zeroline: false },
    yaxis: { autorange: 'reversed' },
  }, { displayModeBar: false, responsive: true });

  // right: HHI cards
  if (cardsEl) {
    const label = (h) => h / evenHhi > 1.8 ? 'concentrated' : h / evenHhi > 1.3 ? 'tilted' : 'balanced';
    cardsEl.innerHTML = league.map((l, i) => `
      <div class="hhi-card${i === 0 ? ' cornered' : ''}">
        <div class="hc-row">
          <span class="hc-pos" style="color:${POS_COLOR[l.position] || '#8a95a8'}">${l.position}</span>
          <span class="hc-hhi">${l.hhi != null ? l.hhi.toFixed(3) : '–'}</span>
        </div>
        <div class="hc-sub">${l.hhi != null ? label(l.hhi) : 'no data'} · top: ${ownerName(l.top_roster_id)}
          ${l.n_unprojected ? ` · ${l.n_unprojected} unprojected` : ''}</div>
      </div>`).join('');
  }

  // bottom: plain-language diagnostic for the most-cornered position
  if (diagEl) {
    const top = league[0];
    const rows = byPos[top.position] || [];
    const lead = rows[0], second = rows[1];
    if (!lead) { diagEl.style.display = 'none'; return; }
    let line = `<b>${ownerName(lead.roster_id)}</b> holds ${lead.elite_count} of ${top.elite_total} startable ${top.position}s — carrying <b>${(lead.vona_share * 100).toFixed(1)}%</b> of league ${top.position} VONA`;
    if (second) {
      line += `, vs ${ownerName(second.roster_id)}'s ${second.elite_count} at ${(second.vona_share * 100).toFixed(1)}%`;
      if (lead.elite_count <= second.elite_count) line += '. Quality cornering, not body-count';
    }
    line += '.';
    // durability: within-team realized→projected delta, text only (per caveat)
    if (cornerBasis === 'realized' && cornerCache.projected) {
      const projRows = cornerCache.projected.rosters
        .filter((r) => r.position === top.position);
      const mine = projRows.find((r) => r.roster_id === lead.roster_id);
      if (mine && mine.vona_share != null) {
        const holds = mine.vona_share >= lead.vona_share - 0.01;
        line += `<span class="durability">${top.position} corner: ${(lead.vona_share * 100).toFixed(1)}% → ${(mine.vona_share * 100).toFixed(1)}% projected · ${holds ? 'corner holds' : 'moat depreciating'}</span>`;
      }
    }
    diagEl.innerHTML = line;
    diagEl.style.display = 'block';
  }
}

// Win-now (VBD, production over replacement) vs dynasty (FP market price). Each point
// is a player; the dotted line is parity. Top-left = produces now but the dynasty
// market discounts him (aging vets — sell-high). Bottom-right = the market pays for
// value not yet in the box score (youth, injury return, or SF-QB scarcity).
async function renderTriangulation(leagueId) {
  const rows = await api(`/leagues/${leagueId}/value`);
  const panel = document.getElementById('triPanel');
  if (!rows.length) { panel.style.display = 'none'; return; }  // VBD layer not built yet
  panel.style.display = 'block';
  wireTape(rows);

  const colors = { QB: '#3d80f5', RB: '#3ecf74', WR: '#e8a838', TE: '#b47cf5' };
  const maxv = Math.max(1, ...rows.map((d) => Math.max(d.fp_market_value || 0, d.vbd_value || 0)));
  const traces = ['QB', 'RB', 'WR', 'TE'].map((pos) => {
    const pr = rows.filter((d) => d.position === pos);
    return {
      type: 'scatter', mode: 'markers', name: pos,
      x: pr.map((d) => d.fp_market_value),
      y: pr.map((d) => d.vbd_value),
      text: pr.map((d) => `${d.player_name}<br>${Number(d.ppg).toFixed(1)} ppg · VORP ${Number(d.vorp).toFixed(1)}`),
      marker: { color: colors[pos], size: 9, opacity: 0.82, line: { color: '#161b22', width: 1 } },
      hovertemplate: '%{text}<br>FP %{x:,.0f} · VBD %{y:,.0f}<extra>' + pos + '</extra>',
    };
  });
  traces.push({
    type: 'scatter', mode: 'lines', x: [0, maxv], y: [0, maxv],
    line: { color: '#2a3340', width: 1, dash: 'dot' }, hoverinfo: 'skip', showlegend: false,
  });

  Plotly.react('triChart', traces, {
    margin: { l: 66, r: 20, t: 10, b: 52 },
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: { color: INK },
    xaxis: { title: 'Dynasty value — FantasyPros', gridcolor: GRID, zeroline: false },
    yaxis: { title: 'Win-now value — VBD (pts over replacement)', gridcolor: GRID, zeroline: false },
    legend: { orientation: 'h', y: 1.1 },
    annotations: [
      { x: maxv * 0.04, y: maxv * 0.96, xanchor: 'left', showarrow: false,
        text: 'produces now · market discounts (sell-high)', font: { size: 10, color: '#8a95a8' } },
      { x: maxv * 0.96, y: maxv * 0.05, xanchor: 'right', showarrow: false,
        text: 'market pays ahead of production (youth / injury / SF-QB)', font: { size: 10, color: '#8a95a8' } },
    ],
  }, { displayModeBar: false, responsive: true });
}

async function drillRoster(rosterId) {
  const meta = currentRows.find((r) => r.roster_id === rosterId) || {};
  const assets = await api(`/leagues/${currentLeague}/rosters/${rosterId}`);
  const valued = assets.filter((a) => a.fp_market_value != null);
  rosterKpis(meta);

  // Value-weighted age — a dynasty-specific read on how "old" a team's VALUE is.
  const wsum = valued.reduce((s, a) => s + (a.age ? a.fp_market_value : 0), 0);
  const wAge = wsum ? valued.reduce((s, a) => s + (a.age || 0) * a.fp_market_value, 0) / wsum : null;

  document.getElementById('rosterPanel').style.display = 'block';
  document.getElementById('rosterTitle').textContent =
    `${meta.owner_name || 'Roster ' + rosterId} — Roster Detail`;

  document.getElementById('rosterSummary').innerHTML = `
    <div class="stat">Total value<b>${fmt(meta.team_value)}</b></div>
    <div class="stat">League rank<b>#${meta.value_rank}</b></div>
    <div class="stat">HHI concentration<b>${Number(meta.hhi).toFixed(3)}</b></div>
    <div class="stat">Assets valued<b>${valued.length}</b></div>
    <div class="stat">Value-weighted age<b>${wAge ? wAge.toFixed(1) : '–'}</b></div>
    <div class="stat">Value share<b>${pctShare(meta.team_value, valueTotal)}</b></div>
    <div class="stat">Prod. share (realized)<b>${prodByRoster ? pctShare(prodByRoster[rosterId], prodTotal) : '–'}</b></div>
    <div class="stat" title="m1 projection — at preseason as-ofs statistically ≈ the ECR baseline (see Model Lab); its validated edge is in-season">Prod. share (projected)<b>${projByRoster ? pctShare(projByRoster[rosterId], projTotal) : '–'}</b></div>`;

  // Positional value allocation (donut).
  const byPos = {};
  valued.forEach((a) => { byPos[a.position] = (byPos[a.position] || 0) + a.fp_market_value; });
  Plotly.react('posChart', [{
    type: 'pie', hole: 0.55,
    labels: Object.keys(byPos), values: Object.values(byPos),
    marker: { colors: Object.keys(byPos).map((p) => POS_COLOR[p] || '#8a95a8'),
              line: { color: '#161b22', width: 2 } },
    textinfo: 'label+percent',
  }], {
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: false,
    paper_bgcolor: 'transparent', font: { color: INK },
    annotations: [{ text: 'value<br>by pos', showarrow: false, font: { size: 12, color: '#8a95a8' } }],
  }, { displayModeBar: false, responsive: true });

  // Asset table.
  const arb = (d) => d == null ? '–'
    : `<span class="${d > 0 ? 'pos-good' : 'pos-bad'}">${d > 0 ? '+' : ''}${fmt(Math.round(d))}</span>`;
  document.getElementById('rosterTable').innerHTML = `
    <table><thead><tr>
      <th>Player</th><th>Pos</th><th class="num">Age</th><th>Team</th>
      <th class="num">FP value</th><th class="num">FC value</th>
      <th class="num">VBD</th><th class="num">PPG</th>
      <th class="num">Arb Δ</th><th class="num">30d</th>
    </tr></thead><tbody>
      ${assets.map((a) => `<tr>
        <td>${a.player_name || '–'}</td>
        <td><span class="pos pos-${a.position || ''}">${a.position || '?'}</span></td>
        <td class="num">${a.age ?? '–'}</td>
        <td>${a.nfl_team || '–'}</td>
        <td class="num">${fmt(a.fp_market_value)}</td>
        <td class="num">${fmt(a.fc_market_value)}</td>
        <td class="num">${fmt(a.vbd_value)}</td>
        <td class="num">${a.ppg != null ? Number(a.ppg).toFixed(1) : '–'}</td>
        <td class="num">${arb(a.arb_delta_fp_minus_fc)}</td>
        <td class="num">${a.fc_trend_30day != null ? fmt(Math.round(a.fc_trend_30day)) : '–'}</td>
      </tr>`).join('')}
    </tbody></table>`;

  document.getElementById('rosterPanel').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

init();
