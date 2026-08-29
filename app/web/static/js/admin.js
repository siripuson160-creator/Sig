/* Admin console (sections 42, 43, 44, 56, 64).
   Two things this file deliberately does not have: any input that sets a
   statistic, and any way to delete history. */

import {
  api,
  clear,
  dateTime,
  debounce,
  directionChip,
  el,
  emptyState,
  percent,
  points,
  price,
  resultChip,
  signClass,
  statusChip,
} from './util.js';
import { languageSwitch, t } from './i18n.js';

const TOKEN_STORE = 'signal-admin-token';
const view = document.getElementById('view');
const signOutButton = document.getElementById('sign-out');

const state = {
  tab: 'overview',
  messageFilter: '',
  auditFilter: '',
  broadcastFilter: '',
  broadcastSearch: '',
  broadcastOffset: 0,
  broadcastView: 'preview',
};

/** Previous / next controls for an endpoint that returns {total, limit, offset}. */
function pager(data, onChange, stateKey) {
  const limit = data.limit || data.items.length || 1;
  const offset = data.offset || 0;
  const last = Math.max(0, Math.ceil(data.total / limit) - 1);
  const page = Math.floor(offset / limit);
  if (data.total <= limit) return null;

  const go = (next) => {
    state[stateKey] = Math.max(0, next) * limit;
    onChange();
  };
  return el('div', { class: 'pager' }, [
    el('span', { class: 'faint', text: `${offset + 1}–${Math.min(offset + limit, data.total)} of ${data.total}` }),
    el('button', { class: 'btn', text: t('Previous'), disabled: page <= 0, onclick: () => go(page - 1) }),
    el('button', { class: 'btn', text: t('Next'), disabled: page >= last, onclick: () => go(page + 1) }),
  ]);
}

/* ------------------------------------------------------------------- auth */
function token() {
  try {
    return sessionStorage.getItem(TOKEN_STORE) || '';
  } catch (_) {
    return '';
  }
}

function setToken(value) {
  try {
    if (value) sessionStorage.setItem(TOKEN_STORE, value);
    else sessionStorage.removeItem(TOKEN_STORE);
  } catch (_) {
    /* private mode: the session lasts for this page load only */
  }
}

async function adminApi(path, options = {}) {
  return api(path, {
    ...options,
    headers: { Authorization: `Bearer ${token()}`, ...(options.headers || {}) },
  });
}

function renderLogin(message = '') {
  signOutButton.hidden = true;
  const input = el('input', { class: 'input', type: 'password', placeholder: t('Admin password'), style: 'flex:1' });

  const submit = async () => {
    try {
      const result = await api('/api/admin/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password: input.value }),
      });
      setToken(result.token);
      await boot();
    } catch (error) {
      renderLogin(error.status === 401 ? 'Wrong password.' : error.message);
    }
  };

  clear(view).append(
    el('div', { class: 'panel', style: 'max-width:440px;margin:48px auto' }, [
      el('div', { class: 'panel-head' }, [el('h2', { text: t('Administrator sign-in') })]),
      el('div', { class: 'panel-body stack', style: 'gap:12px' }, [
        message ? el('div', { class: 'notice warn' }, [message]) : null,
        el('div', { class: 'row' }, [input, el('button', { class: 'btn primary', text: t('Sign in'), onclick: submit })]),
        el('div', {
          class: 'small faint',
          text: t('ADMIN_PASSWORD from the server environment. Sign-ins, successful or not, are recorded in the audit log.'),
        }),
      ]),
    ])
  );
  input.addEventListener('keydown', (event) => event.key === 'Enter' && submit());
  input.focus();
}

/* ------------------------------------------------------------------ shell */
const TABS = [
  { id: 'overview', label: t('Overview') },
  { id: 'messages', label: t('Messages') },
  { id: 'broadcast', label: t('Sent to LINE') },
  { id: 'signals', label: t('Signals') },
  { id: 'edits', label: t('Edit history') },
  { id: 'statistics', label: t('Statistics') },
  { id: 'audit', label: t('Audit log') },
  { id: 'system', label: t('System status') },
  { id: 'settings', label: t('Settings') },
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

/** GREEN / YELLOW / RED light for one component (section 56). */
function lamp(label, colour, detail = '') {
  const tone = { GREEN: 'ok', YELLOW: 'warn', RED: 'bad' }[colour] || '';
  return el('div', { class: 'kv' }, [
    el('div', { class: 'k', text: label }),
    el('div', { class: 'row', style: 'gap:8px;margin-top:4px' }, [
      el('span', { class: `dot ${tone}` }),
      el('span', { text: colour }),
    ]),
    detail ? el('div', { class: 'small faint', style: 'margin-top:4px', text: detail }) : null,
  ]);
}

/* --------------------------------------------------------------- overview */
async function renderOverview() {
  const status = await adminApi('/api/admin/status');
  const delivery = status.delivery || {};

  renderShell(
    el('div', {}, [
      status.dry_run
        ? el('div', { class: 'panel' }, [
            el('div', { class: 'panel-body' }, [
              el('div', { class: 'notice warn' }, [
                el('strong', { text: t('Test mode. ') }),
                'DRY_RUN=true — messages are received, parsed and stored, but nothing is sent to LINE.',
              ]),
            ]),
          ])
        : null,
      panel(t('Status'), [
        el('div', { class: 'detail-grid' }, [
          lamp('Telegram', status.lights.telegram, (status.components.telegram || {}).detail || ''),
          lamp('LINE', status.lights.line, (status.components.line || {}).detail || ''),
          lamp('Database', status.lights.database),
          lamp('Message queue', status.lights.queue, `${delivery.PENDING ?? 0} pending`),
          lamp('Dashboard', status.lights.dashboard),
        ]),
      ]),
      panel(t('Delivery queue'), [
        el('div', { class: 'detail-grid' }, [
          kv(t('Pending'), String(delivery.PENDING ?? 0)),
          kv(t('Sent'), String(delivery.SENT ?? 0), 'pos'),
          kv(t('Failed'), String(delivery.FAILED ?? 0), delivery.FAILED ? 'neg' : ''),
          kv(t('Skipped'), String(delivery.SKIPPED ?? 0)),
          kv(t('Open signals'), String(status.open_signals)),
        ]),
        delivery.FAILED
          ? el('div', { class: 'notice warn', style: 'margin-top:14px' }, [
              `${delivery.FAILED} message(s) could not be delivered. Open Messages, filter FAILED, and requeue them.`,
            ])
          : null,
      ]),
      panel(t('Connections'), [
        el('div', { class: 'detail-grid' }, [
          kv(t('Telegram source'), (status.telegram_source || []).join(', ') || 'not set'),
          kv(t('LINE configured'), status.delivery_configured ? 'yes' : 'no', status.delivery_configured ? 'pos' : 'neg'),
          kv(t('Price provider'), `${status.price_provider.name}${status.price_provider.available ? '' : ' (no data)'}`),
          kv(t('Timezone'), status.timezone),
          kv(t('Signed in as'), status.signed_in_as),
        ]),
        el('div', { class: 'row', style: 'margin-top:16px' }, [
          el('button', {
            class: 'btn',
            text: t('Test LINE credentials'),
            onclick: async (event) => {
              const button = event.target;
              button.disabled = true;
              try {
                const result = await adminApi('/api/admin/line/test', { method: 'POST' });
                toast(result.ok ? `LINE ok — ${result.detail}` : `LINE failed — ${result.detail}`, result.ok);
              } catch (error) {
                toast(error.message, false);
              }
              button.disabled = false;
            },
          }),
        ]),
      ]),
    ])
  );
}

/* -------------------------------------------------------------- broadcast */
/* The archive of what the LINE group actually received.
 *
 * Different from the Messages tab, which is the delivery queue: this shows the
 * exact text that was pushed — the EDITED prefix included — because that is
 * what a member saw, and it is the record to check a complaint against. */
async function renderBroadcast() {
  const query = new URLSearchParams({ limit: '100', offset: String(state.broadcastOffset || 0) });
  if (state.broadcastFilter) query.set('status', state.broadcastFilter);
  if (state.broadcastSearch) query.set('q', state.broadcastSearch);
  const data = await adminApi(`/api/admin/broadcast?${query}`);
  const config = await adminApi('/api/admin/settings').catch(() => ({}));
  // The channel's display name is not worth a LINE round-trip on every page
  // load; the destination id identifies the group well enough for a preview.
  const botName = 'Signal Bot';
  const toTelegram = config.delivery_target === 'telegram';
  const groupLabel =
    config.delivery_destination ||
    config.line_destination ||
    (toTelegram ? 'Telegram channel (not configured yet)' : 'LINE group (not configured yet)');

  const search = el('input', {
    class: 'input',
    placeholder: t('Search text or message id…'),
    value: state.broadcastSearch || '',
    oninput: debounce((event) => {
      state.broadcastSearch = event.target.value.trim();
      state.broadcastOffset = 0;
      renderTab();
    }, 350),
  });

  const filter = el(
    'select',
    {
      class: 'select',
      onchange: (event) => {
        state.broadcastFilter = event.target.value;
        state.broadcastOffset = 0;
        renderTab();
      },
    },
    [
      el('option', { value: '', text: t('All') }),
      ...['SENT', 'PENDING', 'FAILED', 'SKIPPED'].map((status) =>
        el('option', { value: status, text: status, selected: state.broadcastFilter === status })
      ),
    ]
  );

  const modes = [
    ['preview', 'LINE preview'],
    ['details', 'Details'],
  ];
  const toggle = el(
    'div',
    { class: 'segmented' },
    modes.map(([id, label]) =>
      el('button', {
        class: state.broadcastView === id || (!state.broadcastView && id === 'preview') ? 'active' : '',
        text: label,
        onclick: () => {
          state.broadcastView = id;
          renderTab();
        },
      })
    )
  );

  const view = state.broadcastView || 'preview';
  const body =
    view === 'preview'
      ? linePreview(data.items, botName, groupLabel, toTelegram)
      : el('div', { class: 'broadcast-list' }, data.items.map(broadcastDetail));

  renderShell(
    panel(
      `Sent to LINE · ${data.total} message${data.total === 1 ? '' : 's'}`,
      [
        data.items.length ? body : emptyState(t('Nothing matches that filter.')),
        pager(data, () => renderTab(), 'broadcastOffset'),
      ],
      el('div', { class: 'row', style: 'margin-left:auto;gap:8px' }, [toggle, search, filter])
    )
  );
}

/* A mock-up of the LINE group, so the operator can see what members see.
 *
 * A bot pushing into a group appears the way any other member does: on the
 * left, in a white bubble, under its display name — never on the right, which
 * is reserved for the reader's own messages. Getting that wrong would make the
 * preview reassuring and wrong.
 *
 * Oldest at the top, like a real conversation, so the API's newest-first order
 * is reversed here. */
function linePreview(items, botName, groupLabel, toTelegram = false) {
  const chat = [];
  let lastDay = null;
  // When nothing was delivered the reason is the same for every message, so it
  // is said once at the top rather than repeated under every bubble.
  const noneDelivered = items.length > 0 && items.every((item) => item.status === 'SKIPPED');

  for (const item of [...items].reverse()) {
    const when = item.posted_at || item.received_at;
    const day = when ? dateTime(when, { withTime: false }) : '';
    if (day && day !== lastDay) {
      chat.push(el('div', { class: 'line-day' }, [el('span', { text: day })]));
      lastDay = day;
    }
    chat.push(lineBubble(item, botName, when, { quiet: noneDelivered, toTelegram }));
  }

  return el('div', { class: 'line-preview' }, [
    el('div', { class: 'line-header' }, [
      el('span', { class: 'line-back', text: '‹' }),
      el('span', { class: 'line-title', text: groupLabel }),
      el('span', { class: 'line-count', text: `${items.length}` }),
    ]),
    noneDelivered
      ? el('div', { class: 'line-banner' }, [
          el('strong', { text: t('Test mode. ') }),
          t('This is how the messages would look — none of them were actually posted.'),
        ])
      : null,
    el('div', { class: 'line-chat' }, chat),
    // What happens to a photo depends on where the message is going, and a
    // simulation that says the wrong one is worse than no simulation at all.
    el('p', { class: 'line-note small faint' }, [
      t('Oldest first. Each bubble is the exact text the bridge pushes — the same string, character for character. '),
      ...(toTelegram
        ? [t('The destination is a Telegram channel, so a photo is posted as the picture itself with this text as its caption.')]
        : [
            t('Everything is sent to LINE as a text message, so a Telegram photo arrives as '),
            el('code', { text: t('[photo]') }),
            t(' and the picture itself does not travel.'),
          ]),
      t(' An edit arrives as a new message prefixed '),
      el('code', { text: t('EDITED') }),
      t('; it never replaces the one before it.'),
    ]),
  ]);
}

function lineBubble(item, botName, when, { quiet = false, toTelegram = false } = {}) {
  const undelivered = item.status !== 'SENT';
  const text = item.line_text || '';

  // Everything LINE receives is a text message: the bridge pushes
  // {"type":"text"}, never an image. A Telegram photo arrives as the literal
  // string "[photo]" in front of its caption, so that is what the bubble
  // shows. Drawing a picture frame here would be a comfortable lie — members
  // do not get the chart.
  const body = [];
  if (item.is_edit && text.startsWith('EDITED')) {
    body.push(el('span', { class: 'line-edited', text: t('EDITED') }));
    body.push(document.createTextNode(text.replace(/^EDITED\n*/, '')));
  } else {
    body.push(document.createTextNode(text));
  }

  const meta = el('div', { class: 'line-meta' }, [
    item.status === 'SENT' ? el('span', { class: 'line-read', text: t('Read') }) : null,
    el('span', { class: 'line-time', text: when ? dateTime(when, { withDate: false }) : '' }),
  ]);

  const notes = [];
  if (item.has_media) {
    notes.push(
      el('span', {
        class: toTelegram ? 'line-note-ok' : 'line-notsent',
        text: toTelegram
          ? t('the picture is posted too, with this text as its caption')
          : t('the image itself is not forwarded — only this text'),
      })
    );
  }
  if (undelivered && !quiet) {
    notes.push(el('span', { class: 'line-notsent', text: statusExplanation(item) }));
  }

  return el('div', { class: 'line-row' }, [
    el('div', { class: 'line-avatar', text: botName.slice(0, 1).toUpperCase() }),
    el('div', { class: 'line-stack' }, [
      el('span', { class: 'line-sender', text: botName }),
      el('div', { class: 'line-bubble-wrap' }, [
        el('div', { class: 'line-bubble' }, [
          text ? el('div', { class: 'line-text' }, body) : el('div', { class: 'line-empty', text: t('nothing to send — this message produces no LINE post') }),
        ]),
        meta,
      ]),
      ...notes,
    ]),
  ]);
}

function statusExplanation(item) {
  if (item.status === 'SKIPPED') return 'not sent — test mode';
  if (item.status === 'PENDING') return 'waiting in the queue';
  if (item.status === 'FAILED') return `failed — ${item.last_error || 'see the details view'}`;
  return 'not delivered';
}

function broadcastDetail(item) {
  const chip =
    item.status === 'SENT' ? 'win' : item.status === 'FAILED' ? 'loss' : item.status === 'PENDING' ? 'open' : 'neutral';
  return el('article', { class: 'broadcast-entry' }, [
    el('header', { class: 'broadcast-head' }, [
      el('span', { class: 'small faint num', text: dateTime(item.posted_at || item.received_at) }),
      item.is_edit ? el('span', { class: 'chip open', text: t('EDITED') }) : null,
      item.has_media ? el('span', { class: 'chip neutral', text: 'media' }) : null,
      el('span', { class: 'chip ' + chip, text: item.status }),
      el('span', { class: 'small faint num spacer', text: `#${item.message_id}·v${item.version}` }),
    ]),
    el('pre', { class: 'broadcast-text', text: item.line_text || '(empty)' }),
    el('footer', { class: 'broadcast-foot small faint' }, [
      `${item.characters} characters`,
      item.sent_at ? ` · delivered ${dateTime(item.sent_at, { withDate: false })}` : '',
      item.line_message_id ? ` · LINE id ${item.line_message_id}` : '',
      item.last_error ? el('span', { class: 'neg', text: ` · ${item.last_error}` }) : null,
    ]),
  ]);
}

/* --------------------------------------------------------------- messages */
async function renderMessages() {
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
      el('option', { value: '', text: t('All statuses') }),
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
        el('div', {
          class: 'small',
          style: 'font-family:var(--mono);white-space:pre-wrap',
          text: truncate(message.content, 220),
        }),
        message.last_error ? el('div', { class: 'small neg', text: message.last_error }) : null,
      ]),
      el('td', {}, [
        el('span', {
          class: `chip ${
            message.status === 'SENT' ? 'win' : message.status === 'FAILED' ? 'loss' : message.status === 'PENDING' ? 'open' : 'neutral'
          }`,
          text: message.status,
        }),
      ]),
      el('td', { class: 'right num small', text: String(message.send_attempts) }),
      el('td', {}, [
        message.status === 'SENT'
          ? el('span', { class: 'faint small', text: dateTime(message.sent_at, { withDate: false }) })
          : el('button', {
              class: 'btn',
              text: t('Requeue'),
              onclick: async (event) => {
                event.target.disabled = true;
                try {
                  await adminApi(`/api/admin/messages/${message.id}/requeue`, { method: 'POST' });
                  toast(t('Requeued for delivery.'), true);
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
        el('h2', { text: t('Telegram → LINE messages') }),
        el('span', { class: 'panel-note', text: `${data.total} total` }),
        filter,
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [
            el('table', {}, [
              tableHead([t('Received'), t('Message'), t('Type'), t('Content'), t('Status'), t('Tries'), '']),
              el('tbody', {}, rows),
            ]),
          ])
        : emptyState(t('No messages recorded yet.')),
    ])
  );
}

function tableHead(labels) {
  return el('thead', {}, [el('tr', {}, labels.map((label) => el('th', { text: label })))]);
}

function truncate(text, limit) {
  if (!text) return '';
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

/* ----------------------------------------------------------- edit history */
async function renderEdits() {
  const data = await adminApi('/api/admin/edit-history?limit=50');

  renderShell(
    el('div', {}, [
      el('div', { class: 'panel' }, [
        el('div', { class: 'panel-head' }, [
          el('h2', { text: t('Edited messages') }),
          el('span', { class: 'panel-note', text: t('every version is kept; nothing here can be deleted') }),
        ]),
        el('div', { class: 'panel-body' }, [
          data.items.length
            ? el(
                'div',
                {},
                data.items.map((thread) =>
                  el('div', { style: 'margin-bottom:28px' }, [
                    el('div', { class: 'row', style: 'gap:8px;margin-bottom:10px' }, [
                      el('span', { class: 'chip info', text: `${thread.versions} versions` }),
                      el('span', { class: 'muted small', text: `message ${thread.message_id} in chat ${thread.chat_id}` }),
                    ]),
                    el(
                      'div',
                      { class: 'timeline' },
                      thread.history.map((message) =>
                        el('div', { class: `timeline-item${message.event_type === 'EDIT' ? ' edit' : ''}` }, [
                          el('div', { class: 'timeline-head' }, [
                            el('span', {
                              class: `chip ${message.event_type === 'EDIT' ? 'open' : 'info'}`,
                              text: `${message.event_type} · v${message.version}`,
                            }),
                            el('span', { text: dateTime(message.edited_at || message.created_at) }),
                            el('span', { class: 'faint small', text: `hash ${(message.content_hash || '').slice(0, 12)}` }),
                          ]),
                          el('div', { class: 'msg', text: message.content }),
                        ])
                      )
                    ),
                  ])
                )
              )
            : emptyState(t('No message has been edited yet.')),
        ]),
      ]),
    ])
  );
}

/* --------------------------------------------------------------- audit log */
async function renderAudit() {
  const query = new URLSearchParams({ limit: '150' });
  if (state.auditFilter) query.set('event', state.auditFilter);
  const data = await adminApi(`/api/admin/audit?${query}`);

  const filter = el(
    'select',
    {
      class: 'select',
      style: 'margin-left:auto',
      onchange: (event) => {
        state.auditFilter = event.target.value;
        renderTab();
      },
    },
    [
      el('option', { value: '', text: t('All events') }),
      ...(data.events || []).map((event) =>
        el('option', { value: event, text: event.replace(/_/g, ' '), selected: state.auditFilter === event })
      ),
    ]
  );

  const tone = (event) => {
    if (event.includes('FAILED') || event === 'SL_HIT') return 'loss';
    if (event === 'TP_HIT' || event === 'LINE_SEND') return 'win';
    if (event.startsWith('ADMIN')) return 'info';
    return 'neutral';
  };

  const rows = data.items.map((entry) =>
    el('tr', {}, [
      el('td', { class: 'small muted', text: dateTime(entry.ts) }),
      el('td', {}, [el('span', { class: `chip ${tone(entry.event)}`, text: entry.event.replace(/_/g, ' ') })]),
      el('td', { style: 'white-space:normal;max-width:360px' }, [
        el('div', { text: entry.summary }),
        entry.reason ? el('div', { class: 'small faint', text: `Reason: ${entry.reason}` }) : null,
        entry.old_value || entry.new_value
          ? el('div', { class: 'small diff' }, [
              entry.old_value ? el('span', { class: 'neg', text: `− ${JSON.stringify(entry.old_value)}` }) : null,
              entry.old_value && entry.new_value ? el('br') : null,
              entry.new_value ? el('span', { class: 'pos', text: `+ ${JSON.stringify(entry.new_value)}` }) : null,
            ])
          : null,
      ]),
      el('td', { class: 'small', text: entry.actor }),
      el('td', { class: 'small faint', text: entry.entity_id ? String(entry.entity_id).slice(0, 18) : '—' }),
    ])
  );

  renderShell(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: t('Audit log') }),
        el('span', { class: 'panel-note', text: `${data.total} entries · append-only` }),
        filter,
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [
            el('table', {}, [tableHead([t('When'), t('Event'), t('What happened'), t('Actor'), t('Entity')]), el('tbody', {}, rows)]),
          ])
        : emptyState(t('Nothing recorded yet.')),
    ])
  );
}

/* --------------------------------------------------------------- statistics */
async function renderStatistics() {
  const data = await adminApi('/api/admin/statistics');
  const o = data.overview;

  renderShell(
    el('div', {}, [
      el('div', { class: 'panel' }, [
        el('div', { class: 'panel-body' }, [
          el('div', { class: 'notice' }, [
            el('strong', { text: t('These numbers cannot be edited. ') }),
            'They are computed from the signals table by the statistics engine every time this page loads. ' +
              'There is no field anywhere in this console that sets a win rate, a profit total or a signal count.',
          ]),
        ]),
      ]),
      panel(t('All time'), [
        el('div', { class: 'detail-grid' }, [
          kv(t('Total signals'), String(o.total_signals)),
          kv(t('Wins'), String(o.wins), 'pos'),
          kv(t('Losses'), String(o.losses), 'neg'),
          kv(t('Win rate'), percent(o.win_rate)),
          kv(t('Total P/L'), `${points(o.total_pl_points)} pts`, signClass(o.total_pl_points)),
          kv(t('Profit factor'), o.profit_factor === null ? '—' : o.profit_factor.toFixed(2)),
          kv(t('Max drawdown'), `${points(o.max_drawdown_points)} pts`, 'neg'),
          kv(t('Average P/L'), `${points(o.expectancy_points)} pts`, signClass(o.expectancy_points)),
          kv(t('Risk : reward'), o.rr_display || '—'),
          kv(t('Pending'), String(o.pending)),
          kv(t('Ambiguous'), String(o.ambiguous)),
          kv(t('Cancelled'), String(o.cancelled)),
        ]),
      ]),
    ])
  );
}

/* ------------------------------------------------------------ system status */
async function renderSystem() {
  const status = await adminApi('/api/admin/status');
  const components = status.components || {};

  renderShell(
    el('div', {}, [
      panel(t('Components'), [
        Object.keys(components).length
          ? el('div', { class: 'table-wrap' }, [
              el('table', {}, [
                tableHead([t('Component'), t('Status'), t('Detail'), t('Last heartbeat')]),
                el(
                  'tbody',
                  {},
                  Object.entries(components).map(([name, info]) =>
                    el('tr', {}, [
                      el('td', { text: name }),
                      el('td', {}, [
                        el('span', {
                          class: `chip ${info.status === 'UP' ? 'win' : info.status === 'DEGRADED' ? 'open' : 'loss'}`,
                          text: info.status,
                        }),
                      ]),
                      el('td', { class: 'small muted', text: info.detail || '—' }),
                      el('td', { class: 'small faint', text: `${Math.round(info.seconds_ago)}s ago` }),
                    ])
                  )
                ),
              ]),
            ])
          : emptyState(t('No component has reported in yet. Start the bridge with python -m app.main.')),
        el('div', {
          class: 'panel-note',
          style: 'margin-top:12px',
          text: t('A component that has not checked in for two minutes is shown as DOWN.'),
        }),
      ]),
      panel(t('Price provider'), [
        el('div', { class: 'detail-grid' }, [
          kv(t('Provider'), status.price_provider.name),
          kv(t('Price data'), status.price_provider.available ? 'available' : 'none configured'),
          kv(t('Available providers'), (status.available_price_providers || []).join(', ')),
        ]),
      ]),
    ])
  );
}

/* ---------------------------------------------------------------- settings */
async function renderSettings() {
  const data = await adminApi('/api/admin/settings/editable');

  /* Grouping is presentation only. The server's allow-list decides what may be
   * edited, so a key it serves must always reach the page — see the ungrouped
   * catch-all below, which exists because adding DELIVERY_TARGET to the
   * allow-list without touching this array made it invisible. */
  const GROUPS = [
    ['Where messages go', ['DELIVERY_TARGET', 'TELEGRAM_TARGET_CHAT_ID']],
    ['Telegram', ['TELEGRAM_API_ID', 'TELEGRAM_API_HASH', 'TELEGRAM_SOURCE_CHAT_ID']],
    ['LINE', ['LINE_CHANNEL_ACCESS_TOKEN', 'LINE_GROUP_ID', 'LINE_ENABLED', 'DRY_RUN',
              'ADD_EDITED_PREFIX', 'LINE_EDIT_PREFIX']],
    ['Results and prices', ['RESULT_SOURCE', 'PRICE_DATA_PROVIDER', 'PRICE_API_KEY', 'PRICE_SYMBOL',
                            'POINT_SIZE', 'PIP_SIZE', 'AMBIGUITY_RULE']],
    ['Dashboard', ['TIMEZONE', 'PUBLIC_BROADCAST_ENABLED']],
  ];

  const byKey = Object.fromEntries(data.items.map((item) => [item.key, item]));
  const inputs = {};

  // Anything the server offers that no group claims. Better an unsorted field
  // than a setting the operator cannot find and assumes was never deployed.
  const grouped = new Set(GROUPS.flatMap(([, keys]) => keys));
  const ungrouped = data.items.map((item) => item.key).filter((key) => !grouped.has(key));
  if (ungrouped.length) GROUPS.push(['Other settings', ungrouped]);

  function control(item) {
    if (item.kind === 'bool') {
      const select = el('select', { class: 'select' }, [
        el('option', { value: 'true', text: t('On') }),
        el('option', { value: 'false', text: t('Off') }),
      ]);
      select.value = String(item.value).toLowerCase() === 'true' ? 'true' : 'false';
      return select;
    }
    if (item.kind === 'choice') {
      const select = el('select', { class: 'select' }, (item.choices || []).map((choice) =>
        el('option', { value: choice, text: choice })
      ));
      select.value = item.value || (item.choices || [])[0];
      return select;
    }
    return el('input', {
      class: 'input',
      type: item.secret ? 'password' : 'text',
      value: item.secret ? '' : item.value || '',
      autocomplete: 'off',
      // A stored secret is never sent back to the browser, so the box shows
      // the masked hint and staying blank keeps what is already stored.
      placeholder: item.secret
        ? item.is_set
          ? `stored (${item.value}) — leave blank to keep`
          : 'not set'
        : '',
    });
  }

  const sections = GROUPS.filter(([, keys]) => keys.some((key) => byKey[key])).map(([title, keys]) =>
    el('section', { class: 'settings-group' }, [
      el('h3', { text: t(title) }),
      ...keys.filter((key) => byKey[key]).map((key) => {
        const item = byKey[key];
        const input = control(item);
        inputs[key] = input;
        return el('label', { class: 'field' }, [
          el('span', { class: 'field-label num', text: key }),
          input,
          SETTING_HINTS[key] ? el('span', { class: 'field-hint', text: t(SETTING_HINTS[key]) }) : null,
        ]);
      }),
    ])
  );

  const box = el('div');
  const save = el('button', { class: 'btn primary', text: t('Save and restart') });

  save.onclick = async () => {
    clear(box);
    save.disabled = true;
    const label = save.textContent;
    save.textContent = 'Saving…';
    try {
      const values = {};
      for (const [key, input] of Object.entries(inputs)) values[key] = input.value;
      const result = await adminApi('/api/admin/settings/editable', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ values, restart: true }),
      });
      if (!result.changed.length) {
        box.append(el('div', { class: 'notice', text: t('Nothing changed.') }));
      } else {
        box.append(
          el('div', { class: 'notice ok' }, [
            el('strong', { text: t('Saved: {keys}. ', { keys: result.changed.join(', ') }) }),
            t('The service is restarting — give it about ten seconds, then reload this page.'),
          ])
        );
      }
    } catch (error) {
      box.append(el('div', { class: 'notice bad', text: error.message }));
    } finally {
      save.disabled = false;
      save.textContent = label;
    }
  };

  // The test checks whichever destination is configured, so the button must not
  // promise LINE when the messages are going to a Telegram channel.
  const targetNow = (byKey.DELIVERY_TARGET && byKey.DELIVERY_TARGET.value) || 'line';
  const targetName = targetNow === 'telegram' ? 'Telegram' : 'LINE';
  const testLine = el('button', { class: 'btn', text: t('Test {target} now', { target: targetName }) });
  testLine.onclick = async () => {
    clear(box);
    testLine.disabled = true;
    try {
      const result = await adminApi('/api/admin/line/test', { method: 'POST' });
      const name = result.target === 'telegram' ? 'Telegram' : 'LINE';
      box.append(
        el('div', { class: `notice ${result.ok ? 'ok' : 'bad'}`, text:
          result.ok ? `${name} ok — ${result.detail}` : `${name} failed — ${result.detail}` })
      );
    } catch (error) {
      box.append(el('div', { class: 'notice bad', text: error.message }));
    }
    testLine.disabled = false;
  };

  renderShell(
    panel(t('Settings'), [
      el('div', { class: 'notice' }, [
        el('strong', { text: t('These are the live settings. ') }),
        t('Saving rewrites the settings file and restarts the service, so what is running is always what is on disk. Leave a secret blank to keep the stored value.'),
      ]),
      el('div', { class: 'settings-grid' }, sections),
      box,
      el('div', { class: 'setup-actions' }, [save, testLine]),
      el('p', { class: 'field-hint num', text: data.env_path }),
    ])
  );
}

const SETTING_HINTS = {
  DELIVERY_TARGET: 'Which app the messages are posted into.',
  TELEGRAM_TARGET_CHAT_ID: 'The channel to post into, e.g. @mychannel. Only used when the target is telegram.',
  TELEGRAM_API_ID: 'From my.telegram.org → API development tools.',
  TELEGRAM_API_HASH: 'From the same page. Changing it needs a fresh sign-in.',
  TELEGRAM_SOURCE_CHAT_ID: 'The group the signals are read from, e.g. -1001234567890.',
  LINE_CHANNEL_ACCESS_TOKEN: 'Messaging API → Channel access token (long-lived).',
  LINE_GROUP_ID: 'Starts with C for a group, R for a room, U for a person.',
  LINE_ENABLED: 'Off stores messages without pushing them.',
  DRY_RUN: 'On is test mode: read and parse everything, post nothing.',
  ADD_EDITED_PREFIX: 'Whether an edited message is delivered marked EDITED.',
  RESULT_SOURCE: 'price = checked against price history. message = the provider’s own reports.',
  PRICE_API_KEY: 'Twelve Data key. Only needed when the provider is twelvedata.',
  POINT_SIZE: 'The unit statistics are reported in. 0.01 makes a $7 move read as 700.',
  PIP_SIZE: 'What one pip is worth in price, for targets quoted as "TP: 50/100Pips".',
  AMBIGUITY_RULE: 'When one candle holds both the target and the stop.',
  PUBLIC_BROADCAST_ENABLED: 'Show members the archive of everything posted to LINE.',
};

/* ----------------------------------------------------------------- signals */
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
        el('button', { class: 'btn', text: 'Correct', onclick: () => openOverride(signal) }),
      ]),
    ])
  );

  renderShell(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: t('Signals') }),
        el('span', { class: 'panel-note', text: `${data.total} total` }),
      ]),
      data.items.length
        ? el('div', { class: 'table-wrap' }, [
            el('table', {}, [
              tableHead([t('Posted'), 'Dir', t('Entry'), t('SL'), t('TP1'), t('Status'), t('Result'), t('P/L'), 'Actions']),
              el('tbody', {}, rows),
            ]),
          ])
        : emptyState(t('No signals recorded yet.')),
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

/** Correcting one trade — with a mandatory reason (section 46). */
function openOverride(signal) {
  const status = el(
    'select',
    { class: 'select' },
    ['TP1_HIT', 'TP2_HIT', 'TP3_HIT', 'SL_HIT', 'CLOSED', 'CANCELLED', 'AMBIGUOUS', 'ACTIVE', 'PENDING'].map((value) =>
      el('option', { value, text: value, selected: signal.status === value })
    )
  );
  const result = el(
    'select',
    { class: 'select' },
    ['WIN', 'LOSS', 'BREAKEVEN', 'AMBIGUOUS', 'CANCELLED', 'PENDING_RESULT'].map((value) =>
      el('option', { value, text: value, selected: signal.result === value })
    )
  );
  const profit = el('input', { class: 'input', type: 'number', step: '0.01', min: '0', value: signal.profit_points ?? '' });
  const loss = el('input', { class: 'input', type: 'number', step: '0.01', min: '0', value: signal.loss_points ?? '' });
  const reason = el('input', {
    class: 'input',
    placeholder: 'Why is this being corrected? (required)',
    style: 'flex:1',
  });

  async function submit(override) {
    if (!reason.value || reason.value.trim().length < 3) {
      toast('A reason is required — it goes into the audit log.', false);
      reason.focus();
      return;
    }
    const payload = override
      ? { release_override: true, reason: reason.value }
      : {
          status: status.value,
          result: result.value,
          profit_points: profit.value === '' ? null : Number(profit.value),
          loss_points: loss.value === '' ? null : Number(loss.value),
          reason: reason.value,
        };
    try {
      await adminApi(`/api/admin/signals/${signal.signal_id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      toast('Signal corrected; the change is in the audit log.', true);
      state.tab = 'signals';
      renderTab();
    } catch (error) {
      toast(error.message, false);
    }
  }

  renderShell(
    el('div', { class: 'panel' }, [
      el('div', { class: 'panel-head' }, [
        el('h2', { text: `Correct ${signal.direction} ${signal.symbol || ''} @ ${price(signal.entry)}` }),
        el('span', { class: 'spacer' }),
        el('button', { class: 'btn', text: 'Cancel', onclick: renderTab }),
      ]),
      el('div', { class: 'panel-body stack', style: 'gap:12px' }, [
        el('div', { class: 'notice warn' }, [
          'This records the old value, the new value, your account, the time and your reason in the audit log, ' +
            'and marks the signal as manually set on the public dashboard. It also freezes the signal: the price ' +
            'engine and later Telegram edits will stop changing it.',
        ]),
        field('Status', status),
        field('Result', result),
        field('Profit points', profit),
        field('Loss points', loss),
        field('Reason', reason),
        el('div', { class: 'row', style: 'justify-content:flex-end' }, [
          el('button', { class: 'btn', text: 'Release override', onclick: () => submit(true) }),
          el('button', { class: 'btn primary', text: 'Save correction', onclick: () => submit(false) }),
        ]),
      ]),
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
    style:
      'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:50;max-width:min(520px,92vw);box-shadow:var(--shadow)',
    text: message,
  });
  document.body.append(node);
  setTimeout(() => node.remove(), 4200);
}

/* ------------------------------------------------------------------- boot */

const RENDERERS = {
  overview: renderOverview,
  messages: renderMessages,
  broadcast: renderBroadcast,
  signals: renderSignals,
  edits: renderEdits,
  statistics: renderStatistics,
  audit: renderAudit,
  system: renderSystem,
  settings: renderSettings,
};

async function renderTab() {
  try {
    await RENDERERS[state.tab]();
  } catch (error) {
    if (error.status === 401) {
      setToken('');
      renderLogin('Your session expired. Sign in again.');
      return;
    }
    if (error.status === 503) {
      renderLogin('Admin is disabled: ADMIN_PASSWORD is not set on the server.');
      return;
    }
    renderShell(
      el('div', { class: 'panel' }, [el('div', { class: 'panel-body' }, [el('div', { class: 'notice warn' }, [error.message])])])
    );
  }
}

async function boot() {
  if (!token()) {
    renderLogin();
    return;
  }
  signOutButton.hidden = false;
  await renderTab();
}

signOutButton.addEventListener('click', () => {
  setToken('');
  renderLogin('Signed out.');
});

boot();


/* The language switch lives in the top bar of every page. Changing it reloads,
   which is simpler and more reliable than re-rendering a half-built view. */
const TOPBAR_TEXT = [['h1', 'Signal Admin'], ['.brand .sub', 'operations · not visible to members']];

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
