/* Admin console. The key is held in sessionStorage only — it is never put in
   a URL, never logged, and is cleared when the tab closes. */

import {
  api,
  clear,
  dateTime,
  directionChip,
  el,
  emptyState,
  points,
  price,
  resultChip,
  signClass,
  statusChip,
} from './util.js';

const KEY_STORE = 'signal-admin-key';
const view = document.getElementById('view');
const signOutButton = document.getElementById('sign-out');

const state = { tab: 'status', messageFilter: '' };

function adminKey() {
  try {
    return sessionStorage.getItem(KEY_STORE) || '';
  } catch (_) {
    return '';
  }
}

function setAdminKey(value) {
  try {
    if (value) sessionStorage.setItem(KEY_STORE, value);
    else sessionStorage.removeItem(KEY_STORE);
  } catch (_) {
    /* private mode — the key simply lives for this page load */
  }
}

async function adminApi(path, options = {}) {
  return api(path, { ...options, headers: { 'X-Admin-Key': adminKey(), ...(options.headers || {}) } });
}

/* ------------------------------------------------------------------ login */
function renderLogin(message = '') {
  signOutButton.hidden = true;
  const input = el('input', { class: 'input', type: 'password', placeholder: 'Admin key', style: 'flex:1' });
  const submit = async () => {
    setAdminKey(input.value.trim());
    await boot();
  };
  clear(view).append(
    el('div', { class: 'panel', style: 'max-width:440px;margin:48px auto' }, [
      el('div', { class: 'panel-head' }, [el('h2', { text: 'Administrator sign-in' })]),
      el('div', { class: 'panel-body stack', style: 'gap:12px' }, [
        message ? el('div', { class: 'notice warn' }, [message]) : null,
        el('div', { class: 'row' }, [
          input,
          el('button', { class: 'btn primary', text: 'Sign in', onclick: submit }),
        ]),
        el('div', {
          class: 'small faint',
          text: 'The key is the ADMIN_API_KEY from the server environment. It is kept in this tab only.',
        }),
      ]),
    ])
  );
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') submit();
  });
  input.focus();
}

/* ------------------------------------------------------------------ shell */
const TABS = [
  { id: 'status', label: 'Status' },
  { id: 'queue', label: 'LINE queue' },
  { id: 'signals', label: 'Signals' },
];

function renderShell(body) {
  clear(view).append(
    el(
      'nav',
      { class: 'tabs' },
      TABS.map((tab) =>
        el('button', {
          class: `tab${state.tab === tab.id ? ' active' : ''}`,
          text: tab.label,
          onclick: () => {
            state.tab = tab.id;
            renderTab();
          },
        })
      )
    ),
    body
  );
}

function panel(title, children, head = null) {
  return el('div', { class: 'panel' }, [
    el('div', { class: 'panel-head' }, [el('h2', { text: title }), head]),
    el('div', { class: 'panel-body' }, children),
  ]);
}

function kv(key, value, tone = '') {
  return el('div', { class: 'kv' }, [
    el('div', { class: 'k', text: key }),
    el('div', { class: `v ${tone}`, text: value }),
  ]);
}

/* ----------------------------------------------------------------- status */
async function renderStatus() {
  const status = await adminApi('/api/admin/status');
  const delivery = status.delivery || {};

  renderShell(
    el('div', {}, [
      panel('System', [
        el('div', { class: 'detail-grid' }, [
          kv('Database', status.database ? 'connected' : 'unreachable', status.database ? 'pos' : 'neg'),
          kv('LINE delivery', status.line_enabled ? (status.line_configured ? 'ready' : 'not configured') : 'disabled',
            status.line_enabled && status.line_configured ? 'pos' : 'neg'),
          kv('Telegram source', (status.telegram_source || []).join(', ') || 'not set'),
          kv('Price provider', `${status.price_provider.name}${status.price_provider.available ? '' : ' (no data)'}`,
            status.price_provider.available ? 'pos' : 'flat'),
          kv('Open signals', String(status.open_signals)),
          kv('Timezone', status.timezone),
        ]),
        el('div', { class: 'row', style: 'margin-top:16px' }, [
          el('button', {
            class: 'btn',
            text: 'Test LINE credentials',
            onclick: async (event) => {
              const button = event.target;
              button.disabled = true;
              button.textContent = 'Testing…';
              try {
                const result = await adminApi('/api/admin/line/test', { method: 'POST' });
                toast(result.ok ? `LINE ok — ${result.detail}` : `LINE failed — ${result.detail}`, result.ok);
              } catch (error) {
                toast(error.message, false);
              }
              button.disabled = false;
              button.textContent = 'Test LINE credentials';
            },
          }),
        ]),
      ]),
      panel('Delivery queue', [
        el('div', { class: 'detail-grid' }, [
          kv('Pending', String(delivery.PENDING ?? 0), delivery.PENDING ? 'flat' : ''),
          kv('Sent', String(delivery.SENT ?? 0), 'pos'),
          kv('Failed', String(delivery.FAILED ?? 0), delivery.FAILED ? 'neg' : ''),
          kv('Skipped', String(delivery.SKIPPED ?? 0)),
        ]),
        delivery.FAILED
          ? el('div', { class: 'notice warn', style: 'margin-top:14px' }, [
              `${delivery.FAILED} message(s) could not be delivered to LINE. Open the LINE queue tab to inspect and requeue them.`,
            ])
          : null,
      ]),
    ])
  );
}

/* ------------------------------------------------------------------ queue */
async function renderQueue() {
  const query = new URLSearchParams({ limit: '100' });
  if (state.messageFilter) query.set('status', state.messageFilter);
  const data = await adminApi(`/api/admin/messages?${query}`);

  const filter = el(
    'select',
    {
      class: 'select',
      style: 'margin-left:auto',
      onchange: (event) => {
        state.messageFilter = event.target.value;
        renderTab();
      },
    },
    [
      el('option', { value: '', text: 'All statuses' }),
      ...['PENDING', 'SENT', 'FAILED', 'SKIPPED'].map((status) =>
        el('option', { value: status, text: status, selected: state.messageFilter === status })
      ),
    ]
  );

  const rows = data.items.map((message) =>
    el('tr', {}, [
      el('td', { class: 'small muted', text: dateTime(message.received_at) }),
      el('td', { class: 'num small', text: `${message.message_id}·v${message.version}` }),
      el('td', {}, [
        el('span', { class: `chip ${message.event_type === 'EDIT' ? 'open' : 'info'}`, text: message.event_type }),
      ]),
      el('td', { style: 'white-space:normal;max-width:420px' }, [
        el('div', { class: 'small', style: 'font-family:var(--mono);white-space:pre-wrap', text: truncate(message.content, 220) }),
        message.last_error ? el('div', { class: 'small neg', text: message.last_error }) : null,
      ]),
      el('td', {}, [
        el('span', {
          class: `chip ${message.status === 'SENT' ? 'win' : message.status === 'FAILED' ? 'loss' : message.status === 'PENDING' ? 'open' : 'neutral'}`,
          text: message.status,
        }),
      ]),
      el('td', { class: 'right num small', text: String(message.send_attempts) }),
      el('td', {}, [
        message.status === 'SENT'
          ? el('span', { class: 'faint small', text: dateTime(message.sent_at, { withDate: false }) })
          : el('button', {
              class: 'btn',
              text: 'Requeue',
              onclick: async (event) => {
                event.target.disabled = true;
                try {
                  await adminApi(`/api/admin/messages/${message.id}/requeue`, { method: 'POST' });
                  toast('Message requeued for delivery.', true);
                  renderTab();
                } catch (error) {
                  toast(error.message, false);
                  event.target.disabled = false;
                }
              },
            }),
      ]),
    ])
  );

  renderShell(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: 'Telegram → LINE queue' }),
        el('span', { class: 'panel-note', text: `${data.total} messages` }),
        filter,
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [
            el('table', {}, [
              el('thead', {}, [
                el('tr', {}, [
                  el('th', { text: 'Received' }),
                  el('th', { text: 'Message' }),
                  el('th', { text: 'Type' }),
                  el('th', { text: 'Content' }),
                  el('th', { text: 'Status' }),
                  el('th', { class: 'right', text: 'Tries' }),
                  el('th', { text: '' }),
                ]),
              ]),
              el('tbody', {}, rows),
            ]),
          ])
        : emptyState('No messages recorded yet.'),
    ])
  );
}

function truncate(text, limit) {
  if (!text) return '';
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/* ---------------------------------------------------------------- signals */
async function renderSignals() {
  const data = await api('/api/public/signals?limit=100&complete_only=false');

  const rows = data.items.map((signal) =>
    el('tr', {}, [
      el('td', { class: 'small muted', text: dateTime(signal.signal_time) }),
      el('td', {}, [directionChip(signal.direction)]),
      el('td', { class: 'right num', text: price(signal.entry) }),
      el('td', { class: 'right num', text: price(signal.sl) }),
      el('td', { class: 'right num', text: price(signal.tp1) }),
      el('td', {}, [statusChip(signal.status)]),
      el('td', {}, [
        resultChip(signal.result),
        signal.manual_override ? el('span', { class: 'chip info', style: 'margin-left:6px', text: 'MANUAL' }) : null,
      ]),
      el('td', {
        class: `right num ${signal.result === 'PENDING_RESULT' ? 'flat' : signClass(signal.net_points)}`,
        text: signal.result === 'PENDING_RESULT' ? '—' : points(signal.net_points),
      }),
      el('td', { class: 'row', style: 'gap:6px' }, [
        el('button', { class: 'btn', text: 'Re-parse', onclick: () => action(`/api/admin/signals/${signal.signal_id}/reparse`) }),
        el('button', { class: 'btn', text: 'Evaluate', onclick: () => action(`/api/admin/signals/${signal.signal_id}/evaluate`) }),
        el('button', { class: 'btn', text: 'Override', onclick: () => openOverride(signal) }),
      ]),
    ])
  );

  renderShell(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: 'Signals' }),
        el('span', { class: 'panel-note', text: `${data.total} total` }),
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [
            el('table', {}, [
              el('thead', {}, [
                el('tr', {}, [
                  el('th', { text: 'Posted' }),
                  el('th', { text: 'Dir' }),
                  el('th', { class: 'right', text: 'Entry' }),
                  el('th', { class: 'right', text: 'SL' }),
                  el('th', { class: 'right', text: 'TP1' }),
                  el('th', { text: 'Status' }),
                  el('th', { text: 'Result' }),
                  el('th', { class: 'right', text: 'P/L' }),
                  el('th', { text: 'Actions' }),
                ]),
              ]),
              el('tbody', {}, rows),
            ]),
          ])
        : emptyState('No signals recorded yet.'),
    ])
  );
}

async function action(path) {
  try {
    await adminApi(path, { method: 'POST' });
    toast('Done.', true);
    renderTab();
  } catch (error) {
    toast(error.message, false);
  }
}

function openOverride(signal) {
  const status = el('select', { class: 'select' }, [
    ...['TP1_HIT', 'TP2_HIT', 'TP3_HIT', 'SL_HIT', 'CLOSED', 'CANCELLED', 'AMBIGUOUS', 'ACTIVE', 'PENDING'].map((value) =>
      el('option', { value, text: value, selected: signal.status === value })
    ),
  ]);
  const result = el('select', { class: 'select' }, [
    ...['WIN', 'LOSS', 'BREAKEVEN', 'AMBIGUOUS', 'CANCELLED', 'PENDING_RESULT'].map((value) =>
      el('option', { value, text: value, selected: signal.result === value })
    ),
  ]);
  const profit = el('input', { class: 'input', type: 'number', step: '0.01', min: '0', value: signal.profit_points ?? '' });
  const loss = el('input', { class: 'input', type: 'number', step: '0.01', min: '0', value: signal.loss_points ?? '' });
  const note = el('input', { class: 'input', placeholder: 'Why is this being set by hand?', style: 'flex:1' });

  const body = el('div', { class: 'panel-body stack', style: 'gap:12px' }, [
    el('div', { class: 'notice warn' }, [
      'A manual result freezes this signal: the price engine and later Telegram edits will no longer change it. ' +
        'The dashboard marks it as manually set.',
    ]),
    field('Status', status),
    field('Result', result),
    field('Profit points', profit),
    field('Loss points', loss),
    field('Note', note),
    el('div', { class: 'row', style: 'justify-content:flex-end' }, [
      el('button', { class: 'btn', text: 'Release override', onclick: () => submit({ release_override: true }) }),
      el('button', { class: 'btn primary', text: 'Save override', onclick: () => submit(null) }),
    ]),
  ]);

  async function submit(override) {
    const payload = override || {
      status: status.value,
      result: result.value,
      profit_points: profit.value === '' ? null : Number(profit.value),
      loss_points: loss.value === '' ? null : Number(loss.value),
      note: note.value || null,
    };
    try {
      await adminApi(`/api/admin/signals/${signal.signal_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      toast('Signal updated.', true);
      renderTab();
    } catch (error) {
      toast(error.message, false);
    }
  }

  renderShell(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: `Override ${signal.direction} ${signal.symbol || ''} @ ${price(signal.entry)}` }),
        el('span', { class: 'spacer' }),
        el('button', { class: 'btn', text: 'Cancel', onclick: renderTab }),
      ]),
      body,
    ])
  );
}

function field(label, input) {
  return el('label', { class: 'row', style: 'gap:12px' }, [
    el('span', { class: 'muted small', style: 'width:110px', text: label }),
    input,
  ]);
}

/* ------------------------------------------------------------------ toast */
function toast(message, ok) {
  const node = el('div', {
    class: `notice${ok ? '' : ' warn'}`,
    style: 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:50;max-width:min(520px,92vw);box-shadow:var(--shadow)',
    text: message,
  });
  document.body.append(node);
  setTimeout(() => node.remove(), 4200);
}

/* ------------------------------------------------------------------- boot */
const RENDERERS = { status: renderStatus, queue: renderQueue, signals: renderSignals };

async function renderTab() {
  try {
    await RENDERERS[state.tab]();
  } catch (error) {
    if (error.status === 401) {
      setAdminKey('');
      renderLogin('That key was rejected.');
      return;
    }
    if (error.status === 503) {
      renderLogin('The admin API is disabled: ADMIN_API_KEY is not set on the server.');
      return;
    }
    renderShell(el('div', { class: 'panel' }, [el('div', { class: 'panel-body' }, [el('div', { class: 'notice warn' }, [error.message])])]));
  }
}

async function boot() {
  if (!adminKey()) {
    renderLogin();
    return;
  }
  signOutButton.hidden = false;
  await renderTab();
}

signOutButton.addEventListener('click', () => {
  setAdminKey('');
  renderLogin('Signed out.');
});

boot();
