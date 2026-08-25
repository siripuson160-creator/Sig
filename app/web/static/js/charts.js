/* Minimal SVG charts — no external libraries, no network dependency.
   Colour is meaning, not decoration: green profit, red loss, blue reference. */

import { clear, debounce, el } from './util.js';

const NS = 'http://www.w3.org/2000/svg';

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    node.setAttribute(key, String(value));
  }
  return node;
}

/** Ticks on round numbers (1, 2, 2.5, 5 x 10^n) rather than arbitrary fractions. */
function niceTicks(min, max, count = 4) {
  if (!(max > min)) return [min];
  const raw = (max - min) / count;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const normalized = raw / magnitude;
  const step = (normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10) * magnitude;
  const ticks = [];
  for (let value = Math.ceil(min / step) * step; value <= max + step * 1e-6; value += step) {
    ticks.push(Math.round(value * 1e6) / 1e6);
  }
  return ticks.length ? ticks : [min, max];
}

/**
 * Wraps a chart so it is drawn at the container's real pixel width — an SVG
 * stretched with preserveAspectRatio would squash the axis labels on a phone.
 */
export function chartHost(factory, { height = 220 } = {}) {
  const host = el('div', { style: `min-height:${height}px` });
  let lastWidth = 0;
  const draw = () => {
    const width = Math.round(host.clientWidth || 720);
    if (!width || width === lastWidth) return;
    lastWidth = width;
    clear(host).append(factory(width, height));
  };
  requestAnimationFrame(draw);
  if (typeof ResizeObserver !== 'undefined') {
    new ResizeObserver(debounce(draw, 120)).observe(host);
  } else {
    window.addEventListener('resize', debounce(draw, 160));
  }
  return host;
}

function format(value) {
  const abs = Math.abs(value);
  if (abs >= 1000) return `${(value / 1000).toFixed(1)}k`;
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

/**
 * Cumulative equity curve. `series` is [{time, equity}] in chronological order.
 */
export function equityChart(series, { width = 720, height = 220 } = {}) {
  const pad = { top: 16, right: 18, bottom: 28, left: 48 };
  const svg = svgEl('svg', {
    class: 'chart',
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': 'Cumulative profit and loss in points',
  });

  if (!series.length) {
    svg.append(svgEl('rect', { width, height, fill: 'transparent' }));
    return svg;
  }

  const values = series.map((d) => d.equity);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;

  const x = (i) => pad.left + (series.length === 1 ? plotW / 2 : (i / (series.length - 1)) * plotW);
  const y = (v) => pad.top + plotH - ((v - min) / span) * plotH;

  for (const tick of niceTicks(min, max)) {
    const ty = y(tick);
    svg.append(svgEl('line', { class: 'grid-line', x1: pad.left, x2: width - pad.right, y1: ty, y2: ty }));
    const label = svgEl('text', { class: 'axis-label', x: pad.left - 8, y: ty + 3, 'text-anchor': 'end' });
    label.textContent = format(tick);
    svg.append(label);
  }

  // Zero line is the reference the eye should find first.
  if (min < 0 && max > 0) {
    svg.append(
      svgEl('line', {
        x1: pad.left,
        x2: width - pad.right,
        y1: y(0),
        y2: y(0),
        stroke: 'var(--text-faint)',
        'stroke-width': 1,
        'stroke-dasharray': '3 3',
      })
    );
  }

  const line = series.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(d.equity).toFixed(2)}`).join(' ');
  const last = values[values.length - 1];
  const stroke = last >= 0 ? 'var(--green)' : 'var(--red)';

  const gradientId = `eq-${Math.random().toString(36).slice(2, 8)}`;
  const defs = svgEl('defs');
  const gradient = svgEl('linearGradient', { id: gradientId, x1: 0, y1: 0, x2: 0, y2: 1 });
  gradient.append(svgEl('stop', { offset: '0%', 'stop-color': stroke, 'stop-opacity': 0.28 }));
  gradient.append(svgEl('stop', { offset: '100%', 'stop-color': stroke, 'stop-opacity': 0 }));
  defs.append(gradient);
  svg.append(defs);

  svg.append(
    svgEl('path', {
      d: `${line} L${x(series.length - 1).toFixed(2)},${y(Math.max(min, 0)).toFixed(2)} L${x(0).toFixed(2)},${y(
        Math.max(min, 0)
      ).toFixed(2)} Z`,
      fill: `url(#${gradientId})`,
      stroke: 'none',
    })
  );
  svg.append(svgEl('path', { d: line, fill: 'none', stroke, 'stroke-width': 2, 'stroke-linejoin': 'round' }));
  svg.append(svgEl('circle', { cx: x(series.length - 1), cy: y(last), r: 3.5, fill: stroke }));

  // Only the ends of the time axis are labelled; the shape is what matters.
  const stamp = (index, anchor) => {
    const moment = series[index].time ? new Date(series[index].time) : null;
    if (!moment || Number.isNaN(moment.getTime())) return;
    const label = svgEl('text', { class: 'axis-label', x: x(index), y: height - 8, 'text-anchor': anchor });
    label.textContent = moment.toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });
    svg.append(label);
  };
  stamp(0, 'start');
  if (series.length > 1) stamp(series.length - 1, 'end');

  return svg;
}

/**
 * P/L per period. `series` is [{label, value}].
 */
export function barChart(series, { width = 720, height = 200 } = {}) {
  const pad = { top: 14, right: 14, bottom: 30, left: 48 };
  const svg = svgEl('svg', {
    class: 'chart',
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': 'Profit and loss per period, in points',
  });
  if (!series.length) return svg;

  const values = series.map((d) => d.value);
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const y = (v) => pad.top + plotH - ((v - min) / span) * plotH;
  const slot = plotW / series.length;
  const barW = Math.max(2, Math.min(26, slot * 0.62));

  for (const tick of niceTicks(min, max)) {
    const ty = y(tick);
    svg.append(svgEl('line', { class: 'grid-line', x1: pad.left, x2: width - pad.right, y1: ty, y2: ty }));
    const label = svgEl('text', { class: 'axis-label', x: pad.left - 8, y: ty + 3, 'text-anchor': 'end' });
    label.textContent = format(tick);
    svg.append(label);
  }

  const zero = y(0);
  series.forEach((datum, index) => {
    const cx = pad.left + slot * index + slot / 2;
    const value = datum.value;
    const top = value >= 0 ? y(value) : zero;
    const barH = Math.max(1, Math.abs(zero - y(value)));
    const bar = svgEl('rect', {
      x: cx - barW / 2,
      y: top,
      width: barW,
      height: barH,
      rx: 2,
      fill: value >= 0 ? 'var(--green)' : 'var(--red)',
      opacity: 0.85,
    });
    const title = svgEl('title');
    title.textContent = `${datum.label}: ${value > 0 ? '+' : ''}${value} points`;
    bar.append(title);
    svg.append(bar);

    // Label every nth bar so the axis stays readable on a phone.
    const stride = Math.ceil(series.length / Math.max(3, Math.floor(width / 90)));
    if (index % stride === 0) {
      const label = svgEl('text', {
        class: 'axis-label',
        x: cx,
        y: height - 10,
        'text-anchor': 'middle',
      });
      label.textContent = datum.label;
      svg.append(label);
    }
  });

  svg.append(
    svgEl('line', { x1: pad.left, x2: width - pad.right, y1: zero, y2: zero, stroke: 'var(--border)', 'stroke-width': 1 })
  );
  return svg;
}

/**
 * Horizontal comparison rows — used for hour/weekday/direction breakdowns.
 * `rows` is [{label, value, caption, tone}].
 */
export function barRows(rows, { formatValue = (v) => v } = {}) {
  const wrap = el('div');
  const peak = Math.max(1, ...rows.map((r) => Math.abs(r.value)));
  for (const row of rows) {
    const tone = row.tone || (row.value >= 0 ? 'var(--green)' : 'var(--red)');
    wrap.append(
      el('div', { class: 'bar-row' }, [
        el('div', { class: 'muted', text: row.label }),
        el('div', { class: 'bar-track' }, [
          el('div', {
            class: 'bar-fill',
            style: `width:${(Math.abs(row.value) / peak) * 100}%;background:${tone}`,
          }),
        ]),
        el('div', { class: 'num right small', text: row.caption ?? formatValue(row.value) }),
      ])
    );
  }
  return wrap;
}

export function legend(items) {
  return el(
    'div',
    { class: 'chart-legend' },
    items.map((item) =>
      el('span', {}, [el('span', { class: 'swatch', style: `background:${item.color}` }), item.label])
    )
  );
}
