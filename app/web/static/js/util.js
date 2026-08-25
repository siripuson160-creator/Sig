/* Shared helpers: fetching, formatting, tiny DOM builder. */

export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { Accept: 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail || detail;
    } catch (_) {
      /* response had no JSON body */
    }
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return response.json();
}

export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

/* ------------------------------------------------------------ formatting */

/** Points are always signed and never dressed up as money (brief section 21). */
export function points(value, { signed = true, decimals = null } = {}) {
  if (value === null || value === undefined) return '—';
  const rounded = Number(value);
  const places = decimals !== null ? decimals : Number.isInteger(rounded) ? 0 : 2;
  const body = Math.abs(rounded).toFixed(places);
  if (!signed) return body;
  const sign = rounded > 0 ? '+' : rounded < 0 ? '-' : '';
  return `${sign}${body}`;
}

export function pointsLabel(value) {
  if (value === null || value === undefined) return '—';
  return `${points(value)} Points`;
}

export function percent(value) {
  if (value === null || value === undefined) return '—';
  return `${Number(value).toFixed(2)}%`;
}

export function price(value) {
  if (value === null || value === undefined) return '—';
  return Number(value).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

export function signClass(value) {
  if (value === null || value === undefined || Number(value) === 0) return 'flat';
  return Number(value) > 0 ? 'pos' : 'neg';
}

export function dateTime(iso, { withDate = true, withTime = true } = {}) {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const date = d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (withDate && withTime) return `${date} ${time}`;
  return withDate ? date : time;
}

export function dayLabel(period) {
  // "2026-08-17" -> "17 Aug 2026"; "2026-08" -> "Aug 2026".
  if (/^\d{4}-\d{2}-\d{2}$/.test(period)) {
    return new Date(`${period}T00:00:00`).toLocaleDateString('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
    });
  }
  if (/^\d{4}-\d{2}$/.test(period)) {
    return new Date(`${period}-01T00:00:00`).toLocaleDateString('en-GB', { month: 'short', year: 'numeric' });
  }
  return period;
}

/* --------------------------------------------------------------- badges */
const RESULT_CLASS = {
  WIN: 'win',
  LOSS: 'loss',
  BREAKEVEN: 'neutral',
  AMBIGUOUS: 'info',
  CANCELLED: 'neutral',
  PENDING_RESULT: 'open',
};

const RESULT_TEXT = {
  WIN: 'WIN',
  LOSS: 'LOSS',
  BREAKEVEN: 'BREAKEVEN',
  AMBIGUOUS: 'AMBIGUOUS',
  CANCELLED: 'CANCELLED',
  PENDING_RESULT: 'PENDING',
};

export function resultChip(result) {
  return el('span', { class: `chip ${RESULT_CLASS[result] || 'neutral'}`, text: RESULT_TEXT[result] || result });
}

const STATUS_CLASS = {
  PENDING: 'open',
  ACTIVE: 'open',
  TP1_HIT: 'win',
  TP2_HIT: 'win',
  TP3_HIT: 'win',
  SL_HIT: 'loss',
  CLOSED: 'neutral',
  CANCELLED: 'neutral',
  AMBIGUOUS: 'info',
};

export function statusChip(status) {
  return el('span', { class: `chip ${STATUS_CLASS[status] || 'neutral'}`, text: (status || '').replace(/_/g, ' ') });
}

export function directionChip(direction) {
  if (!direction) return el('span', { class: 'chip neutral', text: '—' });
  return el('span', { class: `chip ${direction === 'BUY' ? 'buy' : 'sell'}`, text: direction });
}

export function emptyState(message) {
  return el('div', { class: 'empty', text: message });
}

export function debounce(fn, wait = 250) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
