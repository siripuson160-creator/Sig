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
import { languageSwitch, t } from './i18n.js';

const TABS = [
  { id: 'overview', label: t('Overview') },
  { id: 'signals', label: t('Signals') },
  { id: 'daily', label: t('Daily') },
  { id: 'weekly', label: t('Weekly') },
  { id: 'monthly', label: t('Monthly') },
  { id: 'analytics', label: t('Analytics') },
  { id: 'methodology', label: t('Methodology') },
];

const state = {
  tab: 'overview',
  range: 'all',
  dateFrom: '',
  dateTo: '',
  signalId: null,
  signalPage: 0,
  signalFilter: { status: '', direction: '', result: '' },
};

/** Period selectors from sections 29 and 40. */
const RANGES = [
  ['today', 'Today'],
  ['yesterday', 'Yesterday'],
  ['7d', '7 days'],
  ['30d', '30 days'],
  ['wtd', 'This week'],
  ['mtd', 'This month'],
  ['3m', '3 months'],
  ['6m', '6 months'],
  ['1y', '1 year'],
  ['ytd', 'This year'],
  ['all', 'All time'],
];

/** Appends range + custom dates to any API call. */
function rangeQuery(extra = {}) {
  const params = new URLSearchParams({ range: state.range, ...extra });
  if (state.range === 'custom') {
    if (state.dateFrom) params.set('date_from', state.dateFrom);
    if (state.dateTo) params.set('date_to', state.dateTo);
  }
  return params;
}

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
  const select = el(
    'select',
    {
      class: 'select',
      onchange: (event) => {
        state.range = event.target.value;
        onChange();
      },
    },
    [
      ...RANGES.map(([value, label]) =>
        el('option', { value, text: t(label), selected: state.range === value })
      ),
      el('option', { value: 'custom', text: t('Custom range…'), selected: state.range === 'custom' }),
    ]
  );

  const wrap = el('div', { class: 'row', style: 'gap:8px' }, [select]);

  if (state.range === 'custom') {
    const from = el('input', {
      class: 'input',
      type: 'date',
      value: state.dateFrom,
      onchange: (event) => {
        state.dateFrom = event.target.value;
        onChange();
      },
    });
    const to = el('input', {
      class: 'input',
      type: 'date',
      value: state.dateTo,
      onchange: (event) => {
        state.dateTo = event.target.value;
        onChange();
      },
    });
    wrap.append(from, el('span', { class: 'faint small', text: t('to') }), to);
  }
  return wrap;
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
    api(`/api/public/overview?${rangeQuery()}`),
    api(`/api/public/analytics?${rangeQuery()}`),
    api('/api/public/performance/daily?limit=30'),
  ]);

  const root = clear(view);
  const provenance = await selfReportedBanner();
  if (provenance) root.append(provenance);

  root.append(
    el('div', { class: 'row', style: 'justify-content:space-between;margin-bottom:16px' }, [
      el('div', { class: 'stack' }, [
        el('div', { style: 'font-size:15px;font-weight:600', text: t('Performance summary') }),
        el('div', {
          class: 'small faint',
          text: t('All figures in points · times in {tz}', { tz: overview.timezone }),
        }),
      ]),
      rangePicker(render),
    ])
  );

  root.append(
    el('div', { class: 'cards' }, [
      card(t('Total Signals'), String(overview.total_signals), {
        hint: t('{decided} decided · {open} open', {
          decided: overview.decided_signals,
          open: overview.pending,
        }),
      }),
      card(t('Win Rate'), percent(overview.win_rate), {
        hint: overview.decided_signals
          ? t('{w}W / {l}L', { w: overview.wins, l: overview.losses })
          : t('no decided signals yet'),
      }),
      card(t('Total P/L'), points(overview.total_pl_points), {
        tone: signClass(overview.total_pl_points),
        unit: t('Points'),
      }),
      card(t('Wins'), String(overview.wins), { tone: overview.wins ? 'pos' : '' }),
      card(t('Losses'), String(overview.losses), { tone: overview.losses ? 'neg' : '' }),
      card(t('Profit Factor'), overview.profit_factor === null ? '—' : overview.profit_factor.toFixed(2), {
        hint: t('gross profit / gross loss'),
      }),
      card(t('Max Drawdown'), points(overview.max_drawdown_points), {
        tone: overview.max_drawdown_points < 0 ? 'neg' : '',
        unit: t('Points'),
        hint: t('largest peak-to-trough drop'),
      }),
    ])
  );

  if (overview.pending || overview.ambiguous || overview.cancelled) {
    root.append(
      el('div', { class: 'panel' }, [
        el('div', { class: 'panel-body' }, [
          el('div', { class: 'notice' }, [
            el('div', {}, [
              el('strong', { text: t('Not included in the win rate: ') }),
              t(
                '{open} still open, {ambiguous} ambiguous (take profit and stop loss in the same candle), {cancelled} never filled. They stay listed under Signals.',
                {
                  open: overview.pending,
                  ambiguous: overview.ambiguous,
                  cancelled: overview.cancelled,
                }
              ),
            ]),
          ]),
        ]),
      ])
    );
  }

  root.append(
    panel(t('Cumulative P/L'), [
      analytics.equity_curve.length
        ? el('div', {}, [
            chartHost((width, height) => equityChart(analytics.equity_curve, { width, height }), { height: 220 }),
            legend([
              { color: 'var(--green)', label: t('Cumulative points') },
              { color: 'var(--text-faint)', label: t('Break-even') },
            ]),
          ])
        : emptyState(t('No decided signals in this range yet.')),
    ])
  );

  const recent = daily.items.slice(0, 14).reverse();
  root.append(
    panel(t('Daily P/L (last 14 days with signals)'), [
      recent.length
        ? chartHost(
            (width, height) =>
              barChart(recent.map((d) => ({ label: dayLabel(d.period).slice(0, 6), value: d.pl_points })), {
                width,
                height,
              }),
            { height: 200 }
          )
        : emptyState(t('No daily data yet.')),
    ])
  );

  const secondary = el('div', { class: 'cards', style: 'margin-top:20px' }, [
    card(t('Average Win'), points(overview.avg_win_points), { tone: 'pos', unit: 'Points' }),
    card(t('Average Loss'), points(overview.avg_loss_points), { tone: 'neg', unit: 'Points' }),
    card(t('Expectancy'), points(overview.expectancy_points), {
      tone: signClass(overview.expectancy_points),
      unit: t('Points'),
      hint: 'average points per decided signal',
    }),
    card(t('Best / Worst'), `${points(overview.best_points)} / ${points(overview.worst_points)}`),
    card(t('Longest Win Streak'), String(overview.longest_win_streak)),
    card(t('Longest Loss Streak'), String(overview.longest_loss_streak)),
    card(t('Avg Risk'), points(overview.avg_risk_points, { signed: false }), { unit: 'Points' }),
    card(t('Avg Reward'), points(overview.avg_reward_points, { signed: false }), { unit: 'Points' }),
    card(t('Risk : Reward'), overview.rr_display || '—', { hint: 'from the posted entry, SL and TP1' }),
  ]);
  root.append(secondary);

  root.append(
    panel(t('How far trades ran'), [
      el('div', { class: 'cards' }, [
        card(t('TP1 Hit'), String(overview.tp1_hit ?? 0), { tone: 'pos' }),
        card(t('TP2 Hit'), String(overview.tp2_hit ?? 0), { tone: 'pos' }),
        card(t('TP3 Hit'), String(overview.tp3_hit ?? 0), { tone: 'pos' }),
        card(t('SL Hit'), String(overview.sl_hit ?? 0), { tone: 'neg' }),
      ]),
      el('div', {
        class: 'panel-note',
        style: 'margin-top:12px',
        text: `Counting rule — ${overview.tp_counting_rule || 'cumulative'}.`,
      }),
    ])
  );

  root.append(disclaimerPanel());
}

const DISCLAIMER =
  'Trading involves significant risk. Historical signal performance does not guarantee future results. ' +
  'Actual trading results may differ due to spread, slippage, commissions, execution speed, liquidity and ' +
  'other market conditions. Displayed performance is based on the stated calculation methodology and is ' +
  'not a guarantee of future profitability.';

/* Self-reported numbers must announce themselves.
 *
 * When results come from the provider's own messages rather than from price
 * history, that changes what the figures below mean, so it is said on the page
 * a member lands on — not buried in the methodology tab. Cached because the
 * setting cannot change without a restart. */
let _methodologyCache = null;

async function selfReportedBanner() {
  try {
    _methodologyCache = _methodologyCache || (await api('/api/public/methodology'));
  } catch (_) {
    return null; // never block the dashboard on this
  }
  if (_methodologyCache.result_source !== 'message') return null;

  return el('div', { class: 'notice warn', style: 'margin: 16px 0' }, [
    el('strong', { text: t('These results are reported by the signal provider. ') }),
    t('Each outcome below is taken from what the provider announced about its own trade — a message such as "90 Pips! Can secure as TP2" — and has not been checked against price history. They reflect what was posted in the group, not an independent measurement.'),
  ]);
}

function disclaimerPanel() {
  return el('div', { class: 'panel', style: 'margin-top:24px' }, [
    el('div', { class: 'panel-body' }, [
      el('div', { class: 'small faint', style: 'line-height:1.6' }, [
        el('strong', { text: t('Risk disclaimer. ') }),
        DISCLAIMER,
      ]),
    ]),
  ]);
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
  const query = rangeQuery({
    limit: String(PAGE_SIZE),
    offset: String(state.signalPage * PAGE_SIZE),
    complete_only: 'false',
  });
  for (const [key, value] of Object.entries(state.signalFilter)) {
    if (value) query.set(key, value);
  }
  const data = await api(`/api/public/signals?${query}`);

  const refilter = (key, value) => {
    state.signalFilter[key] = value;
    state.signalPage = 0;
    renderSignals();
  };

  const filters = el('div', { class: 'row', style: 'margin-left:auto;gap:8px' }, [
    select(
      'All results',
      state.signalFilter.result,
      [
        ['WIN', 'Win'],
        ['LOSS', 'Loss'],
        ['PENDING_RESULT', 'Open'],
        ['AMBIGUOUS', 'Ambiguous'],
        ['CANCELLED', 'Cancelled'],
      ],
      (value) => refilter('result', value)
    ),
    select(
      'Any status',
      state.signalFilter.status,
      [
        ['TP1_HIT', 'TP1 hit'],
        ['TP2_HIT', 'TP2 hit'],
        ['TP3_HIT', 'TP3 hit'],
        ['SL_HIT', 'SL hit'],
        ['ACTIVE', 'Active'],
        ['PENDING', 'Pending'],
        ['CLOSED', 'Closed'],
        ['CANCELLED', 'Cancelled'],
      ],
      (value) => refilter('status', value)
    ),
    select(
      'Both directions',
      state.signalFilter.direction,
      [
        ['BUY', 'Buy'],
        ['SELL', 'Sell'],
      ],
      (value) => refilter('direction', value)
    ),
    rangePicker(() => {
      state.signalPage = 0;
      renderSignals();
    }),
  ]);

  const table = el('table', {}, [
    el('thead', {}, [
      el('tr', {}, [
        el('th', { text: t('Time') }),
        el('th', { text: t('Signal') }),
        el('th', { class: 'right', text: t('Entry') }),
        el('th', { class: 'right', text: t('SL') }),
        el('th', { class: 'right', text: t('TP1') }),
        el('th', { class: 'right', text: t('TP2') }),
        el('th', { text: t('Status') }),
        el('th', { text: t('Result') }),
        el('th', { class: 'right', text: t('P/L') }),
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
                signal.is_complete ? null : el('span', { class: 'chip neutral', text: t('INCOMPLETE') }),
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
        el('h2', { text: t('Signals') }),
        el('span', { class: 'panel-note', text: `${data.total} total` }),
        filters,
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [table])
        : emptyState(t('No signals match this filter yet.')),
      el('div', { class: 'pager' }, [
        el('span', { class: 'faint small', text: `Page ${state.signalPage + 1} of ${pages}` }),
        el('button', {
          class: 'btn',
          text: t('Previous'),
          disabled: state.signalPage === 0,
          onclick: () => {
            state.signalPage -= 1;
            renderSignals();
          },
        }),
        el('button', {
          class: 'btn',
          text: t('Next'),
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
      el('button', { class: 'btn', text: t('← Back to signals'), onclick: () => go('#/signals') }),
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
          kv(t('Entry'), price(signal.entry)),
          kv(t('Stop Loss'), price(signal.sl)),
          kv(t('Take Profit 1'), price(signal.tp1)),
          kv(t('Take Profit 2'), price(signal.tp2)),
          kv(t('Take Profit 3'), price(signal.tp3)),
          kv(t('Posted'), dateTime(signal.signal_time)),
          kv(t('Entry filled'), dateTime(signal.entry_filled_at)),
          kv(t('Resolved'), dateTime(signal.resolved_at)),
          kv(t('Price source'), signal.price_source || '—'),
          kv(t('Risk'), signal.risk_points === null ? '—' : `${points(signal.risk_points, { signed: false })} pts`),
          kv(t('Reward'), signal.reward_points === null ? '—' : `${points(signal.reward_points, { signed: false })} pts`),
          kv(t('Risk : Reward'), signal.rr_display || '—'),
        ]),
        el('div', { class: 'detail-grid', style: 'margin-top:12px' }, [
          kv(t('Signal ID'), signal.signal_id),
          kv(t('Telegram message ID'), String(signal.telegram_message_id)),
          kv(t('Telegram chat ID'), String(signal.telegram_chat_id)),
          kv(t('Version'), `v${signal.source_version}`),
        ]),
        signal.note
          ? el('div', { class: 'notice warn', style: 'margin-top:16px' }, [
              el('div', {}, [el('strong', { text: t('Note: ') }), signal.note]),
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
      panel(t('How each version was read'), [
        el('div', { class: 'table-wrap' }, [
          el('table', {}, [
            el('thead', {}, [
              el('tr', {}, [
                el('th', { text: t('Version') }),
                el('th', { text: t('Direction') }),
                el('th', { class: 'right', text: t('Entry') }),
                el('th', { class: 'right', text: t('SL') }),
                el('th', { class: 'right', text: t('TP1') }),
                el('th', { class: 'right', text: t('TP2') }),
                el('th', { text: t('Complete') }),
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
    root.append(panel(`${cap(granularity)} performance`, [emptyState(t('No signals recorded yet.'))]));
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
              el('th', { class: 'right', text: t('Signals') }),
              el('th', { class: 'right', text: t('Wins') }),
              el('th', { class: 'right', text: t('Losses') }),
              el('th', { class: 'right', text: t('Win Rate') }),
              el('th', { class: 'right', text: t('P/L') }),
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
              el('td', { class: 'muted', text: t('Total') }),
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
  const data = await api(`/api/public/analytics?${rangeQuery()}`);

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
    panel(t('By direction'), [
      data.by_direction.length
        ? barRows(
            data.by_direction.map((row) => ({
              label: row.direction,
              value: row.pl_points,
              caption: `${points(row.pl_points)} · ${percent(row.win_rate)}`,
              tone: row.pl_points >= 0 ? 'var(--green)' : 'var(--red)',
            }))
          )
        : emptyState(t('No data yet.')),
    ])
  );

  root.append(
    panel(t('Win rate by hour of day'), [
      data.by_hour.length ? barRows(winRateRows(data.by_hour, 'hour')) : emptyState(t('No data yet.')),
      el('div', { class: 'panel-note', style: 'margin-top:10px', text: t('Local time. Hours with no signals are omitted.') }),
    ])
  );

  root.append(
    panel(t('Win rate by weekday'), [
      data.by_weekday.length ? barRows(winRateRows(data.by_weekday, 'weekday')) : emptyState(t('No data yet.')),
    ])
  );

  root.append(
    panel(t('How far trades ran'), [
      data.tp_distribution.some((row) => row.count)
        ? barRows(
            data.tp_distribution.map((row) => ({
              label: row.level,
              value: row.count,
              caption: String(row.count),
              tone: row.level === 'SL' ? 'var(--red)' : 'var(--green)',
            }))
          )
        : emptyState(t('No decided signals yet.')),
      el('div', {
        class: 'panel-note',
        style: 'margin-top:10px',
        text: t('Take profits are a ladder: a signal that reached TP2 also counts under TP1.'),
      }),
    ])
  );

  if (data.by_symbol.length > 1) {
    root.append(
      panel(t('By symbol'), [
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
    panel(t('Distribution of results (points)'), [
      data.points_distribution.length
        ? barRows(
            data.points_distribution.map((bucket) => ({
              label: `${points(bucket.from)} → ${points(bucket.to)}`,
              value: bucket.count,
              caption: `${bucket.count}`,
              tone: bucket.from >= 0 ? 'var(--green)' : 'var(--red)',
            }))
          )
        : emptyState(t('No decided signals yet.')),
    ])
  );
}

/* ----------------------------------------------------------- methodology */
async function renderMethodology() {
  const root = clear(view);
  const data = await api('/api/public/methodology');

  root.append(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [el('h2', { text: t('How these numbers are produced') })]),
      el('div', { class: 'panel-body prose' }, [
        el('p', {
          text:
            'This page exists so the performance figures can be checked rather than taken on trust. ' +
            'Every rule the system applies is listed here, including the ones that work against the numbers.',
        }),
        el('h3', { text: t('Rules') }),
        el(
          'ol',
          {},
          data.rules.map((rule) => el('li', { text: rule }))
        ),
        el('h3', { text: t('Settings in force') }),
        el('div', { class: 'detail-grid' }, [
          kv(t('Unit'), 'Points'),
          kv(t('Point size'), String(data.point_size)),
          kv(t('Timezone'), data.timezone),
          kv(t('Result source'), data.result_source === 'message' ? 'The provider’s own reports' : 'Price history'),
          kv(t('Price source'), data.price_source),
          kv(t('Price timeframe'), data.price_timeframe),
          kv(t('Same-candle rule'), data.ambiguity_rule),
          kv(t('Result mode'), data.result_mode),
          kv(t('Entry fill window'), `${data.entry_fill_window_hours} h`),
          kv(t('Signal expiry'), `${data.signal_expiry_hours} h`),
        ]),
        el('h3', { text: t('Message parsers') }),
        el(
          'ul',
          {},
          data.parsers.map((parser) =>
            el('li', {}, [
              el('strong', { text: parser.name }),
              // Docstrings use reST double backticks; strip them for display.
              ` — ${parser.doc.split('\n')[0].replace(/``?/g, '')}`,
            ])
          )
        ),
        el('h3', { text: t('What is not shown') }),
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
        el('h3', { text: t('What cannot happen') }),
        el('ul', {}, [
          el('li', { text: t('A losing signal cannot be deleted or hidden; there is no delete route.') }),
          el('li', { text: t('Entry, stop and target cannot be rewritten after the fact to improve a result.') }),
          el('li', { text: t('Statistics cannot be typed in by an administrator — they are computed from the signals table.') }),
          el('li', { text: t('Edit history cannot be removed; every version of every message is kept.') }),
          el('li', {
            text: t('A correction made by hand is written to the audit log with the old value, the new value, who changed it, when, and why.'),
          }),
        ]),
        el('h3', { text: t('Risk disclaimer') }),
        el('p', { text: data.disclaimer || DISCLAIMER }),
      ]),
    ])
  );
}

/* ------------------------------------------------------------------ boot */
let refreshSeconds = 10;

async function updateStatus() {
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  try {
    const health = await api('/api/public/health');
    refreshSeconds = health.refresh_seconds || refreshSeconds;
    dot.className = 'dot ok';
    text.textContent = `${health.signals} signals · ${health.messages} messages`;
    if (health.dry_run) text.textContent += ' · test mode';
    return health;
  } catch (_) {
    dot.className = 'dot bad';
    text.textContent = 'offline';
    return null;
  }
}

/**
 * Keeps the page current (section 47): a server-sent event when the data
 * changes, and a plain poll as the fallback if the stream is unavailable.
 */
function watchForChanges() {
  let fingerprint = null;
  let pollTimer = null;

  const onChange = (next) => {
    if (fingerprint !== null && next !== fingerprint) render();
    fingerprint = next;
    updateStatus();
  };

  const startPolling = () => {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      const health = await updateStatus();
      if (health) onChange(`${health.signals}:${health.messages}`);
    }, Math.max(5, refreshSeconds) * 1000);
  };

  if (typeof EventSource === 'undefined') {
    startPolling();
    return;
  }

  const source = new EventSource('/api/public/stream');
  source.addEventListener('changed', (event) => {
    try {
      onChange(JSON.parse(event.data).fingerprint);
    } catch (_) {
      /* malformed frame; the next one will do */
    }
  });
  source.onerror = () => {
    // The browser retries on its own; polling covers the gap and any proxy
    // that buffers event streams.
    startPolling();
  };
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
updateStatus().then(watchForChanges);


/* The language switch lives in the top bar of every page. Changing it reloads,
   which is simpler and more reliable than re-rendering a half-built view. */
const TOPBAR_TEXT = [['h1', 'Signal Performance'], ['#brand-sub', 'Gold signals · results in points']];

(function mountLanguageSwitch() {
  const host = document.getElementById('lang-switch');
  if (host) host.append(languageSwitch());
  // The masthead is static HTML, so it is translated here rather than being
  // duplicated per language in the template.
  for (const [selector, english] of TOPBAR_TEXT) {
    const node = document.querySelector(selector);
    if (node) node.textContent = t(english);
  }
  const signOut = document.getElementById('sign-out');
  if (signOut) signOut.textContent = t('Sign out');
  const memberLink = document.querySelector('a.status-pill[href="/dashboard"]');
  if (memberLink) memberLink.textContent = t('Member dashboard →');
})();
