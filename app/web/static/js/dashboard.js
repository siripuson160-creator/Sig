/* Public member dashboard (brief section 22). Read-only by construction:
   this file never issues anything but GET requests. */

import { barChart, barRows, chartHost, equityChart, legend } from './charts.js';
import {
  api,
  clear,
  dateTime,
  dayLabel,
  directionChip,
  el,
  emptyState,
  percent,
  points,
  pointsLabel,
  price,
  resultChip,
  signClass,
  statusChip,
} from './util.js';

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'signals', label: 'Signals' },
  { id: 'daily', label: 'Daily' },
  { id: 'weekly', label: 'Weekly' },
  { id: 'monthly', label: 'Monthly' },
  { id: 'analytics', label: 'Analytics' },
  { id: 'methodology', label: 'Methodology' },
];

const state = {
  tab: 'overview',
  range: 'all',
  signalId: null,
  signalPage: 0,
  signalFilter: { status: '', direction: '', result: '' },
};

const view = document.getElementById('view');

/* --------------------------------------------------------------- routing */
function readHash() {
  const hash = window.location.hash.replace(/^#\/?/, '');
  const [head, query] = hash.split('?');
  const parts = head.split('/').filter(Boolean);
  const params = new URLSearchParams(query || '');

  if (parts[0] === 'signal' && parts[1]) {
    state.signalId = decodeURIComponent(parts[1]);
    return;
  }
  state.signalId = null;
  state.tab = TABS.some((t) => t.id === parts[0]) ? parts[0] : 'overview';
  if (params.get('range')) state.range = params.get('range');
}

function go(hash) {
  window.location.hash = hash;
}

/* ------------------------------------------------------------------ tabs */
function renderTabs() {
  const nav = clear(document.getElementById('tabs'));
  for (const tab of TABS) {
    nav.append(
      el('button', {
        class: `tab${!state.signalId && state.tab === tab.id ? ' active' : ''}`,
        role: 'tab',
        text: tab.label,
        onclick: () => go(`#/${tab.id}`),
      })
    );
  }
}

function rangePicker(onChange) {
  const options = [
    ['today', 'Today'],
    ['7d', '7 days'],
    ['30d', '30 days'],
    ['mtd', 'This month'],
    ['all', 'All time'],
  ];
  return el(
    'div',
    { class: 'segmented' },
    options.map(([value, label]) =>
      el('button', {
        class: state.range === value ? 'active' : '',
        text: label,
        onclick: () => {
          state.range = value;
          onChange();
        },
      })
    )
  );
}

/* -------------------------------------------------------------- overview */
function card(label, value, { tone = '', hint = '', unit = '' } = {}) {
  return el('div', { class: 'card' }, [
    el('div', { class: 'label', text: label }),
    el('div', { class: `value num ${tone}` }, [value, unit ? el('span', { class: 'unit', text: unit }) : null]),
    hint ? el('div', { class: 'hint', text: hint }) : null,
  ]);
}

async function renderOverview() {
  const [overview, analytics, daily] = await Promise.all([
    api(`/api/public/overview?range=${state.range}`),
    api(`/api/public/analytics?range=${state.range}`),
    api('/api/public/performance/daily?limit=30'),
  ]);

  const root = clear(view);

  root.append(
    el('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:16px' }, [
      el('div', { class: 'stack' }, [
        el('div', { style: 'font-size:15px;font-weight:600', text: 'Performance summary' }),
        el('div', {
          class: 'small faint',
          text: `All figures in points · times in ${overview.timezone}`,
        }),
      ]),
      rangePicker(render),
    ])
  );

  root.append(
    el('div', { class: 'cards' }, [
      card('Total Signals', String(overview.total_signals), {
        hint: `${overview.decided_signals} decided · ${overview.pending} open`,
      }),
      card('Win Rate', percent(overview.win_rate), {
        hint: overview.decided_signals ? `${overview.wins}W / ${overview.losses}L` : 'no decided signals yet',
      }),
      card('Total P/L', points(overview.total_pl_points), {
        tone: signClass(overview.total_pl_points),
        unit: 'Points',
      }),
      card('Wins', String(overview.wins), { tone: overview.wins ? 'pos' : '' }),
      card('Losses', String(overview.losses), { tone: overview.losses ? 'neg' : '' }),
      card('Profit Factor', overview.profit_factor === null ? '—' : overview.profit_factor.toFixed(2), {
        hint: 'gross profit / gross loss',
      }),
      card('Max Drawdown', points(overview.max_drawdown_points), {
        tone: overview.max_drawdown_points < 0 ? 'neg' : '',
        unit: 'Points',
        hint: 'largest peak-to-trough drop',
      }),
    ])
  );

  if (overview.pending || overview.ambiguous || overview.cancelled) {
    root.append(
      el('div', { class: 'panel' }, [
        el('div', { class: 'panel-body' }, [
          el('div', { class: 'notice' }, [
            el('div', {}, [
              el('strong', { text: 'Not included in the win rate: ' }),
              `${overview.pending} still open, ${overview.ambiguous} ambiguous (take profit and stop loss in the same candle), ${overview.cancelled} never filled. They stay listed under Signals.`,
            ]),
          ]),
        ]),
      ])
    );
  }

  root.append(
    panel('Cumulative P/L', [
      analytics.equity_curve.length
        ? el('div', {}, [
            chartHost((width, height) => equityChart(analytics.equity_curve, { width, height }), { height: 220 }),
            legend([
              { color: 'var(--green)', label: 'Cumulative points' },
              { color: 'var(--text-faint)', label: 'Break-even' },
            ]),
          ])
        : emptyState('No decided signals in this range yet.'),
    ])
  );

  const recent = daily.items.slice(0, 14).reverse();
  root.append(
    panel('Daily P/L (last 14 days with signals)', [
      recent.length
        ? chartHost(
            (width, height) =>
              barChart(recent.map((d) => ({ label: dayLabel(d.period).slice(0, 6), value: d.pl_points })), {
                width,
                height,
              }),
            { height: 200 }
          )
        : emptyState('No daily data yet.'),
    ])
  );

  const secondary = el('div', { class: 'cards', style: 'margin-top:20px' }, [
    card('Average Win', points(overview.avg_win_points), { tone: 'pos', unit: 'Points' }),
    card('Average Loss', points(overview.avg_loss_points), { tone: 'neg', unit: 'Points' }),
    card('Expectancy', points(overview.expectancy_points), {
      tone: signClass(overview.expectancy_points),
      unit: 'Points',
      hint: 'average points per decided signal',
    }),
    card('Best / Worst', `${points(overview.best_points)} / ${points(overview.worst_points)}`),
    card('Longest Win Streak', String(overview.longest_win_streak)),
    card('Longest Loss Streak', String(overview.longest_loss_streak)),
  ]);
  root.append(secondary);
}

function panel(title, children, extraHead = null) {
  return el('div', { class: 'panel' }, [
    el('div', { class: 'panel-head' }, [el('h2', { text: title }), extraHead]),
    el('div', { class: 'panel-body' }, children),
  ]);
}

/* --------------------------------------------------------------- signals */
const PAGE_SIZE = 25;

async function renderSignals() {
  const root = clear(view);
  const query = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(state.signalPage * PAGE_SIZE),
    complete_only: 'false',
  });
  for (const [key, value] of Object.entries(state.signalFilter)) {
    if (value) query.set(key, value);
  }
  const data = await api(`/api/public/signals?${query}`);

  const filters = el('div', { class: 'row spacer', style: 'margin-left:auto' }, [
    select('All results', state.signalFilter.result, [
      ['WIN', 'Win'],
      ['LOSS', 'Loss'],
      ['PENDING_RESULT', 'Open'],
      ['AMBIGUOUS', 'Ambiguous'],
      ['CANCELLED', 'Cancelled'],
    ], (value) => {
      state.signalFilter.result = value;
      state.signalPage = 0;
      renderSignals();
    }),
    select('Both directions', state.signalFilter.direction, [
      ['BUY', 'Buy'],
      ['SELL', 'Sell'],
    ], (value) => {
      state.signalFilter.direction = value;
      state.signalPage = 0;
      renderSignals();
    }),
  ]);

  const table = el('table', {}, [
    el('thead', {}, [
      el('tr', {}, [
        el('th', { text: 'Time' }),
        el('th', { text: 'Signal' }),
        el('th', { class: 'right', text: 'Entry' }),
        el('th', { class: 'right', text: 'SL' }),
        el('th', { class: 'right', text: 'TP1' }),
        el('th', { class: 'right', text: 'TP2' }),
        el('th', { text: 'Status' }),
        el('th', { text: 'Result' }),
        el('th', { class: 'right', text: 'P/L' }),
      ]),
    ]),
    el(
      'tbody',
      {},
      data.items.map((signal) =>
        el(
          'tr',
          { class: 'clickable', onclick: () => go(`#/signal/${encodeURIComponent(signal.signal_id)}`) },
          [
            el('td', { class: 'small muted', text: dateTime(signal.signal_time) }),
            el('td', {}, [
              el('div', { class: 'row', style: 'gap:8px' }, [
                directionChip(signal.direction),
                el('span', { class: 'muted small', text: signal.symbol || '—' }),
                signal.is_complete ? null : el('span', { class: 'chip neutral', text: 'INCOMPLETE' }),
              ]),
            ]),
            el('td', { class: 'right num', text: price(signal.entry) }),
            el('td', { class: 'right num', text: price(signal.sl) }),
            el('td', { class: 'right num', text: price(signal.tp1) }),
            el('td', { class: 'right num', text: price(signal.tp2) }),
            el('td', {}, [statusChip(signal.status)]),
            el('td', {}, [resultChip(signal.result)]),
            el('td', {
              class: `right num ${signal.result === 'PENDING_RESULT' ? 'flat' : signClass(signal.net_points)}`,
              text: signal.result === 'PENDING_RESULT' ? '—' : points(signal.net_points),
            }),
          ]
        )
      )
    ),
  ]);

  const pages = Math.max(1, Math.ceil(data.total / PAGE_SIZE));
  root.append(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: 'Signals' }),
        el('span', { class: 'panel-note', text: `${data.total} total` }),
        filters,
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [table])
        : emptyState('No signals match this filter yet.'),
      el('div', { class: 'pager' }, [
        el('span', { class: 'faint small', text: `Page ${state.signalPage + 1} of ${pages}` }),
        el('button', {
          class: 'btn',
          text: 'Previous',
          disabled: state.signalPage === 0,
          onclick: () => {
            state.signalPage -= 1;
            renderSignals();
          },
        }),
        el('button', {
          class: 'btn',
          text: 'Next',
          disabled: state.signalPage >= pages - 1,
          onclick: () => {
            state.signalPage += 1;
            renderSignals();
          },
        }),
      ]),
    ])
  );
}

function select(placeholder, value, options, onChange) {
  const node = el('select', { class: 'select', onchange: (event) => onChange(event.target.value) }, [
    el('option', { value: '', text: placeholder }),
    ...options.map(([optionValue, label]) =>
      el('option', { value: optionValue, text: label, selected: value === optionValue })
    ),
  ]);
  return node;
}

/* --------------------------------------------------------- signal detail */
async function renderSignalDetail() {
  const root = clear(view);
  let signal;
  try {
    signal = await api(`/api/public/signals/${encodeURIComponent(state.signalId)}`);
  } catch (error) {
    root.append(emptyState(error.status === 404 ? 'Signal not found.' : error.message));
    return;
  }

  root.append(
    el('div', { class: 'row', style: 'margin-bottom:16px' }, [
      el('button', { class: 'btn', text: '← Back to signals', onclick: () => go('#/signals') }),
    ])
  );

  const net = signal.result === 'PENDING_RESULT' ? null : signal.net_points;
  root.append(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        directionChip(signal.direction),
        el('h2', { text: `${signal.symbol || 'Signal'} @ ${price(signal.entry)}` }),
        statusChip(signal.status),
        resultChip(signal.result),
        el('span', { class: 'spacer' }),
        el('span', { class: `num ${signClass(net)}`, style: 'font-size:20px;font-weight:600', text: net === null ? 'Open' : pointsLabel(net) }),
      ]),
      el('div', { class: 'panel-body' }, [
        el('div', { class: 'detail-grid' }, [
          kv('Entry', price(signal.entry)),
          kv('Stop Loss', price(signal.sl)),
          kv('Take Profit 1', price(signal.tp1)),
          kv('Take Profit 2', price(signal.tp2)),
          kv('Take Profit 3', price(signal.tp3)),
          kv('Posted', dateTime(signal.signal_time)),
          kv('Entry filled', dateTime(signal.entry_filled_at)),
          kv('Resolved', dateTime(signal.resolved_at)),
          kv('Price source', signal.price_source || '—'),
        ]),
        signal.note
          ? el('div', { class: 'notice warn', style: 'margin-top:16px' }, [
              el('div', {}, [el('strong', { text: 'Note: ' }), signal.note]),
            ])
          : null,
        signal.manual_override
          ? el('div', { class: 'notice', style: 'margin-top:12px' }, [
              'This result was set manually by an administrator rather than by the price engine.',
            ])
          : null,
      ]),
    ])
  );

  /* Edit history — every version, nothing removed (brief sections 7 and 12). */
  root.append(
    panel(
      `Message history (${signal.message_history.length} version${signal.message_history.length === 1 ? '' : 's'})`,
      [
        el(
          'div',
          { class: 'timeline' },
          signal.message_history.map((message) =>
            el('div', { class: `timeline-item${message.event_type === 'EDIT' ? ' edit' : ''}` }, [
              el('div', { class: 'timeline-head' }, [
                el('span', {
                  class: `chip ${message.event_type === 'EDIT' ? 'open' : 'info'}`,
                  text: message.event_type === 'EDIT' ? `EDITED · v${message.version}` : `ORIGINAL · v${message.version}`,
                }),
                el('span', { text: dateTime(message.edited_at || message.created_at || message.received_at) }),
                message.status
                  ? el('span', {
                      class: `chip ${message.status === 'SENT' ? 'win' : message.status === 'FAILED' ? 'loss' : 'neutral'}`,
                      text: `LINE ${message.status}`,
                    })
                  : null,
              ]),
              el('div', { class: 'msg', text: message.content }),
            ])
          )
        ),
      ]
    )
  );

  const parses = signal.parse_history.filter((entry) => entry.parsed);
  if (parses.length > 1) {
    root.append(
      panel('How each version was read', [
        el('div', { class: 'table-wrap' }, [
          el('table', {}, [
            el('thead', {}, [
              el('tr', {}, [
                el('th', { text: 'Version' }),
                el('th', { text: 'Direction' }),
                el('th', { class: 'right', text: 'Entry' }),
                el('th', { class: 'right', text: 'SL' }),
                el('th', { class: 'right', text: 'TP1' }),
                el('th', { class: 'right', text: 'TP2' }),
                el('th', { text: 'Complete' }),
              ]),
            ]),
            el(
              'tbody',
              {},
              parses.map((entry) =>
                el('tr', {}, [
                  el('td', { class: 'muted', text: `v${entry.telegram_version}` }),
                  el('td', {}, [directionChip(entry.parsed.direction)]),
                  el('td', { class: 'right num', text: price(entry.parsed.entry) }),
                  el('td', { class: 'right num', text: price(entry.parsed.sl) }),
                  el('td', { class: 'right num', text: price(entry.parsed.tp1) }),
                  el('td', { class: 'right num', text: price(entry.parsed.tp2) }),
                  el('td', {}, [
                    el('span', {
                      class: `chip ${entry.parsed.is_complete ? 'win' : 'neutral'}`,
                      text: entry.parsed.is_complete ? 'YES' : 'NO',
                    }),
                  ]),
                ])
              )
            ),
          ]),
        ]),
      ])
    );
  }
}

function kv(key, value) {
  return el('div', { class: 'kv' }, [
    el('div', { class: 'k', text: key }),
    el('div', { class: 'v num', text: value }),
  ]);
}

/* ---------------------------------------------------------- performance */
async function renderPerformance(granularity) {
  const root = clear(view);
  const data = await api(`/api/public/performance/${granularity}?limit=180`);
  const rows = data.items;

  if (!rows.length) {
    root.append(panel(`${cap(granularity)} performance`, [emptyState('No signals recorded yet.')]));
    return;
  }

  const chartSeries = rows
    .slice(0, 40)
    .reverse()
    .map((row) => ({ label: dayLabel(row.period).slice(0, 6), value: row.pl_points }));

  root.append(
    panel(`${cap(granularity)} P/L`, [
      chartHost((width, height) => barChart(chartSeries, { width, height }), { height: 200 }),
    ])
  );

  const totals = rows.reduce(
    (acc, row) => ({
      signals: acc.signals + row.signals,
      wins: acc.wins + row.wins,
      losses: acc.losses + row.losses,
      pl: acc.pl + row.pl_points,
    }),
    { signals: 0, wins: 0, losses: 0, pl: 0 }
  );

  root.append(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: `${cap(granularity)} breakdown` }),
        el('span', { class: 'panel-note', text: `times in ${data.timezone}` }),
      ]),
      el('div', { class: 'table-wrap' }, [
        el('table', {}, [
          el('thead', {}, [
            el('tr', {}, [
              el('th', { text: granularity === 'monthly' ? 'Month' : granularity === 'weekly' ? 'Week of' : 'Date' }),
              el('th', { class: 'right', text: 'Signals' }),
              el('th', { class: 'right', text: 'Wins' }),
              el('th', { class: 'right', text: 'Losses' }),
              el('th', { class: 'right', text: 'Win Rate' }),
              el('th', { class: 'right', text: 'P/L' }),
            ]),
          ]),
          el(
            'tbody',
            {},
            rows.map((row) =>
              el('tr', {}, [
                el('td', { text: dayLabel(row.period) }),
                el('td', { class: 'right num', text: String(row.signals) }),
                el('td', { class: 'right num pos', text: String(row.wins) }),
                el('td', { class: 'right num neg', text: String(row.losses) }),
                el('td', { class: 'right num', text: percent(row.win_rate) }),
                el('td', { class: `right num ${signClass(row.pl_points)}`, text: points(row.pl_points) }),
              ])
            )
          ),
          el('tfoot', {}, [
            el('tr', {}, [
              el('td', { class: 'muted', text: 'Total' }),
              el('td', { class: 'right num', text: String(totals.signals) }),
              el('td', { class: 'right num pos', text: String(totals.wins) }),
              el('td', { class: 'right num neg', text: String(totals.losses) }),
              el('td', {
                class: 'right num',
                text: totals.wins + totals.losses ? percent((totals.wins / (totals.wins + totals.losses)) * 100) : '—',
              }),
              el('td', { class: `right num ${signClass(totals.pl)}`, text: points(Math.round(totals.pl * 100) / 100) }),
            ]),
          ]),
        ]),
      ]),
    ])
  );
}

function cap(word) {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/* ------------------------------------------------------------- analytics */
async function renderAnalytics() {
  const root = clear(view);
  const data = await api(`/api/public/analytics?range=${state.range}`);

  root.append(
    el('div', { class: 'row', style: 'justify-content:flex-end;margin-bottom:16px' }, [rangePicker(render)])
  );

  const winRateRows = (rows, key) =>
    rows.map((row) => ({
      label: row[key] !== undefined ? String(row.period) : row.period,
      value: row.win_rate ?? 0,
      caption: `${percent(row.win_rate)} · ${row.signals}`,
      tone: (row.win_rate ?? 0) >= 50 ? 'var(--green)' : 'var(--red)',
    }));

  root.append(
    panel('By direction', [
      data.by_direction.length
        ? barRows(
            data.by_direction.map((row) => ({
              label: row.direction,
              value: row.pl_points,
              caption: `${points(row.pl_points)} · ${percent(row.win_rate)}`,
              tone: row.pl_points >= 0 ? 'var(--green)' : 'var(--red)',
            }))
          )
        : emptyState('No data yet.'),
    ])
  );

  root.append(
    panel('Win rate by hour of day', [
      data.by_hour.length ? barRows(winRateRows(data.by_hour, 'hour')) : emptyState('No data yet.'),
      el('div', { class: 'panel-note', style: 'margin-top:10px', text: 'Local time. Hours with no signals are omitted.' }),
    ])
  );

  root.append(
    panel('Win rate by weekday', [
      data.by_weekday.length ? barRows(winRateRows(data.by_weekday, 'weekday')) : emptyState('No data yet.'),
    ])
  );

  root.append(
    panel('How far trades ran', [
      data.tp_distribution.some((row) => row.count)
        ? barRows(
            data.tp_distribution.map((row) => ({
              label: row.level,
              value: row.count,
              caption: String(row.count),
              tone: row.level === 'SL' ? 'var(--red)' : 'var(--green)',
            }))
          )
        : emptyState('No decided signals yet.'),
      el('div', {
        class: 'panel-note',
        style: 'margin-top:10px',
        text: 'Take profits are a ladder: a signal that reached TP2 also counts under TP1.',
      }),
    ])
  );

  if (data.by_symbol.length > 1) {
    root.append(
      panel('By symbol', [
        barRows(
          data.by_symbol.map((row) => ({
            label: row.symbol,
            value: row.pl_points,
            caption: `${points(row.pl_points)} · ${row.signals}`,
            tone: row.pl_points >= 0 ? 'var(--green)' : 'var(--red)',
          }))
        ),
      ])
    );
  }

  root.append(
    panel('Distribution of results (points)', [
      data.points_distribution.length
        ? barRows(
            data.points_distribution.map((bucket) => ({
              label: `${points(bucket.from)} → ${points(bucket.to)}`,
              value: bucket.count,
              caption: `${bucket.count}`,
              tone: bucket.from >= 0 ? 'var(--green)' : 'var(--red)',
            }))
          )
        : emptyState('No decided signals yet.'),
    ])
  );
}

/* ----------------------------------------------------------- methodology */
async function renderMethodology() {
  const root = clear(view);
  const data = await api('/api/public/methodology');

  root.append(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [el('h2', { text: 'How these numbers are produced' })]),
      el('div', { class: 'panel-body prose' }, [
        el('p', {
          text:
            'This page exists so the performance figures can be checked rather than taken on trust. ' +
            'Every rule the system applies is listed here, including the ones that work against the numbers.',
        }),
        el('h3', { text: 'Rules' }),
        el(
          'ol',
          {},
          data.rules.map((rule) => el('li', { text: rule }))
        ),
        el('h3', { text: 'Settings in force' }),
        el('div', { class: 'detail-grid' }, [
          kv('Unit', 'Points'),
          kv('Point size', String(data.point_size)),
          kv('Timezone', data.timezone),
          kv('Price source', data.price_source),
          kv('Price timeframe', data.price_timeframe),
          kv('Same-candle rule', data.ambiguity_rule),
          kv('Result mode', data.result_mode),
          kv('Entry fill window', `${data.entry_fill_window_hours} h`),
          kv('Signal expiry', `${data.signal_expiry_hours} h`),
        ]),
        el('h3', { text: 'Message parsers' }),
        el(
          'ul',
          {},
          data.parsers.map((parser) =>
            el('li', {}, [el('strong', { text: parser.name }), ` — ${parser.doc.split('\n')[0]}`])
          )
        ),
        el('h3', { text: 'What is not shown' }),
        el('p', {
          text:
            'No money amounts are reported. Lot size, contract size, spread, commission, swap and slippage are ' +
            'unknown to this system, so a points result cannot honestly be converted into a currency result.',
        }),
        data.price_source === 'none'
          ? el('div', { class: 'notice warn' }, [
              'No price feed is configured yet, so signals are recorded but not judged. They stay at PENDING until ' +
                'a price provider is connected, at which point they are evaluated against historical prices.',
            ])
          : null,
      ]),
    ])
  );
}

/* ------------------------------------------------------------------ boot */
async function updateStatus() {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  try {
    const health = await api('/api/public/health');
    dot.className = 'dot ok';
    text.textContent = `${health.signals} signals · ${health.messages} messages`;
  } catch (_) {
    dot.className = 'dot bad';
    text.textContent = 'offline';
  }
}

const RENDERERS = {
  overview: renderOverview,
  signals: renderSignals,
  daily: () => renderPerformance('daily'),
  weekly: () => renderPerformance('weekly'),
  monthly: () => renderPerformance('monthly'),
  analytics: renderAnalytics,
  methodology: renderMethodology,
};

async function render() {
  readHash();
  renderTabs();
  clear(view).append(el('div', { class: 'panel' }, [el('div', { class: 'panel-body' }, [el('div', { class: 'skeleton', style: 'width:40%' })])]));
  try {
    if (state.signalId) await renderSignalDetail();
    else await (RENDERERS[state.tab] || renderOverview)();
  } catch (error) {
    clear(view).append(
      el('div', { class: 'panel' }, [
        el('div', { class: 'panel-body' }, [
          el('div', { class: 'notice warn' }, [`Could not load this view: ${error.message}`]),
        ]),
      ])
    );
  }
  window.scrollTo({ top: 0, behavior: 'instant' in window ? 'instant' : 'auto' });
}

window.addEventListener('hashchange', render);
render();
updateStatus();
setInterval(updateStatus, 30000);
