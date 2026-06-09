// app.js — fetch from our own API and render. The browser never touches Sleeper
// or any external source; it only reads pre-computed analytics from our backend.
const api = (path) => fetch(`/api${path}`).then((r) => r.json());
const fmt = (n) => (n == null ? '–' : Number(n).toLocaleString());
const leagueSel = document.getElementById('league');

let currentLeague = null;
let currentRows = [];

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
    font: { color: '#e8eef5' }, xaxis: { gridcolor: '#232a33' },
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

  const colors = { QB: '#4f8cff', RB: '#5fd08a', WR: '#ffb454', TE: '#c98bff' };
  const maxv = Math.max(1, ...rows.map((d) => Math.max(d.fp_market_value || 0, d.vbd_value || 0)));
  const traces = ['QB', 'RB', 'WR', 'TE'].map((pos) => {
    const pr = rows.filter((d) => d.position === pos);
    return {
      type: 'scatter', mode: 'markers', name: pos,
      x: pr.map((d) => d.fp_market_value),
      y: pr.map((d) => d.vbd_value),
      text: pr.map((d) => `${d.player_name}<br>${Number(d.ppg).toFixed(1)} ppg · VORP ${Number(d.vorp).toFixed(1)}`),
      marker: { color: colors[pos], size: 9, opacity: 0.82, line: { color: '#171c23', width: 1 } },
      hovertemplate: '%{text}<br>FP %{x:,.0f} · VBD %{y:,.0f}<extra>' + pos + '</extra>',
    };
  });
  traces.push({
    type: 'scatter', mode: 'lines', x: [0, maxv], y: [0, maxv],
    line: { color: '#3a4350', width: 1, dash: 'dot' }, hoverinfo: 'skip', showlegend: false,
  });

  Plotly.react('triChart', traces, {
    margin: { l: 66, r: 20, t: 10, b: 52 },
    paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: { color: '#e8eef5' },
    xaxis: { title: 'Dynasty value — FantasyPros', gridcolor: '#232a33', zeroline: false },
    yaxis: { title: 'Win-now value — VBD (pts over replacement)', gridcolor: '#232a33', zeroline: false },
    legend: { orientation: 'h', y: 1.1 },
    annotations: [
      { x: maxv * 0.04, y: maxv * 0.96, xanchor: 'left', showarrow: false,
        text: 'produces now · market discounts (sell-high)', font: { size: 10, color: '#8b97a7' } },
      { x: maxv * 0.96, y: maxv * 0.05, xanchor: 'right', showarrow: false,
        text: 'market pays ahead of production (youth / injury / SF-QB)', font: { size: 10, color: '#8b97a7' } },
    ],
  }, { displayModeBar: false, responsive: true });
}

async function drillRoster(rosterId) {
  const meta = currentRows.find((r) => r.roster_id === rosterId) || {};
  const assets = await api(`/leagues/${currentLeague}/rosters/${rosterId}`);
  const valued = assets.filter((a) => a.fp_market_value != null);

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
    <div class="stat">Value-weighted age<b>${wAge ? wAge.toFixed(1) : '–'}</b></div>`;

  // Positional value allocation (donut).
  const byPos = {};
  valued.forEach((a) => { byPos[a.position] = (byPos[a.position] || 0) + a.fp_market_value; });
  Plotly.react('posChart', [{
    type: 'pie', hole: 0.55,
    labels: Object.keys(byPos), values: Object.values(byPos),
    textinfo: 'label+percent', marker: { line: { color: '#171c23', width: 2 } },
  }], {
    margin: { l: 10, r: 10, t: 10, b: 10 }, showlegend: false,
    paper_bgcolor: 'transparent', font: { color: '#e8eef5' },
    annotations: [{ text: 'value<br>by pos', showarrow: false, font: { size: 12, color: '#8b97a7' } }],
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
        <td><span class="pos">${a.position || '?'}</span></td>
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
