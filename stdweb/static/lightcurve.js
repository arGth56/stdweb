(function () {
  const FILTER_COLORS = {
    G: '#4c9aff', BP: '#6ea8fe', RP: '#e07a5f',
    B: '#5b8def', V: '#7dce82', R: '#e07a5f', I: '#c77dff',
    g: '#4c9aff', r: '#e07a5f', i: '#c77dff', u: '#9b5de5',
    C: '#8ab4f8'
  };

  function colorFor(filt) {
    return FILTER_COLORS[filt] || '#d7dee8';
  }

  function fmt(v, n) {
    return v == null || v === '' ? '—' : Number(v).toFixed(n);
  }

  function tracesFrom(points) {
    const byFilter = {};
    for (const p of points) {
      const f = p.filter || '?';
      (byFilter[f] ||= []).push(p);
    }
    const traces = [];
    for (const [filt, pts] of Object.entries(byFilter)) {
      const det = pts.filter((p) => p.mag != null);
      const lim = pts.filter((p) => p.mag == null && p.mag_limit != null);
      const col = colorFor(filt);
      if (det.length) {
        traces.push({
          name: filt,
          x: det.map((p) => p.time),
          y: det.map((p) => p.mag),
          error_y: {
            type: 'data',
            array: det.map((p) => p.magerr || 0),
            visible: true,
            thickness: 1,
            width: 0,
            color: col
          },
          mode: 'markers',
          type: 'scatter',
          marker: { color: col, size: 8 },
          text: det.map((p) => p.observer || ''),
          hovertemplate: '%{x}<br>' + filt + ' = %{y:.3f} ± %{error_y.array:.3f}<br>%{text}<extra></extra>'
        });
      }
      if (lim.length) {
        traces.push({
          name: filt + ' limit',
          x: lim.map((p) => p.time),
          y: lim.map((p) => p.mag_limit),
          mode: 'markers',
          type: 'scatter',
          marker: { color: col, size: 9, symbol: 'triangle-down', opacity: 0.85 },
          text: lim.map((p) => p.observer || ''),
          hovertemplate: '%{x}<br>' + filt + ' limit = %{y:.3f}<br>%{text}<extra></extra>'
        });
      }
    }
    return traces;
  }

  function renderTable(el, points) {
    if (!points.length) {
      el.innerHTML = '<p class="lc-empty">No public points at this position yet.</p>';
      return;
    }
    const rows = points.map((p) => {
      const mag = p.is_detection ? fmt(p.mag, 3) + ' ± ' + fmt(p.magerr, 3) : '>';
      const lim = fmt(p.mag_limit, 2);
      const task = p.task_id != null
        ? '<a href="/tasks/' + p.task_id + '">' + p.task_id + '</a>'
        : '';
      return '<tr>' +
        '<td>' + (p.time || '') + '</td>' +
        '<td>' + (p.filter || '') + '</td>' +
        '<td>' + mag + '</td>' +
        '<td>' + lim + '</td>' +
        '<td>' + (p.observer || '') + '</td>' +
        '<td>' + task + '</td>' +
        '</tr>';
    }).join('');
    el.innerHTML = '<table class="lc-table"><thead><tr>' +
      '<th>Time</th><th>Filter</th><th>Mag</th><th>Limit</th><th>Observer</th><th></th>' +
      '</tr></thead><tbody>' + rows + '</tbody></table>';
  }

  const layout = {
    paper_bgcolor: 'rgba(0,0,0,0)',
    plot_bgcolor: 'rgba(0,0,0,0)',
    font: { color: '#e8edf4', family: 'system-ui, sans-serif' },
    margin: { t: 24, r: 24, b: 48, l: 56 },
    xaxis: { title: 'UTC', gridcolor: '#243044', zeroline: false },
    yaxis: { title: 'mag', autorange: 'reversed', gridcolor: '#243044', zeroline: false },
    legend: { orientation: 'h', y: 1.08 },
    hovermode: 'closest'
  };

  async function load(plotEl, tableEl, url, signature) {
    const res = await fetch(url, { credentials: 'same-origin' });
    const data = await res.json();
    const next = String(data.n) + ':' + (data.points.length ? data.points[data.points.length - 1].mjd : '');
    if (next === signature.current && plotEl.dataset.ready) {
      return signature;
    }
    signature.current = next;
    Plotly.react(plotEl, tracesFrom(data.points), layout, { responsive: true, displaylogo: false });
    plotEl.dataset.ready = '1';
    renderTable(tableEl, data.points);
    return signature;
  }

  document.addEventListener('DOMContentLoaded', function () {
    const plotEl = document.getElementById('lc-plot');
    if (!plotEl) return;
    const tableEl = document.getElementById('lc-table-wrap');
    const url = plotEl.dataset.url;
    const signature = { current: '' };
    function tick() {
      load(plotEl, tableEl, url, signature).catch(function () {});
    }
    tick();
    setInterval(tick, 15000);
  });
})();
