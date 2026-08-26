/* First-run setup wizard.
 *
 * One screen per decision, in the order the answers depend on each other:
 * Telegram credentials -> sign in -> pick the source group -> LINE -> prices
 * and rules -> write .env and restart.
 *
 * The Telegram code and 2FA password are typed here and posted to this same
 * server, which hands them to Telegram and forgets them. They are never put in
 * a URL, never kept in a variable after the request, and never stored in the
 * browser. Only the setup token is remembered (sessionStorage), so a reload
 * mid-wizard does not lock the operator out.
 */

import { api, clear, el } from './util.js';

const view = document.getElementById('view');
const stepsBar = document.getElementById('steps');

const STEPS = [
  ['telegram', 'Telegram'],
  ['signin', 'Sign in'],
  ['group', 'Source group'],
  ['line', 'LINE'],
  ['prices', 'Prices & rules'],
  ['done', 'Finish'],
];

const state = {
  token: '',
  step: 'telegram',
  reached: new Set(['telegram']),
  secure: false,
  apiId: '',
  apiHash: '',
  phone: '',
  account: '',
  groups: [],
  chatId: '',
  chatName: '',
  lineToken: '',
  lineDestination: '',
  lineVerified: false,
  lineEnabled: true,
  dryRun: false,
  provider: 'twelvedata',
  priceKey: '',
  symbol: 'XAUUSD',
  ambiguity: 'SL_FIRST',
  resultMode: 'BEST_TP',
  timezone: 'Asia/Bangkok',
  adminPassword: '',
  apiPort: 8000,
};

/* ------------------------------------------------------------------ plumbing */
function setupApi(path, options = {}) {
  return api(`/api/setup${path}`, {
    ...options,
    headers: { 'X-Setup-Token': state.token, ...(options.headers || {}) },
  });
}

function post(path, body) {
  return setupApi(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function go(step) {
  state.step = step;
  state.reached.add(step);
  render();
}

/* --------------------------------------------------------------------- bits */
function field(label, input, hint) {
  return el('label', { class: 'field' }, [
    el('span', { class: 'field-label', text: label }),
    input,
    hint ? el('span', { class: 'field-hint', html: hint }) : null,
  ]);
}

function input(id, attrs = {}) {
  return el('input', { class: 'input', id, autocomplete: 'off', ...attrs });
}

function panel(title, note, children) {
  return el('section', { class: 'panel setup-panel' }, [
    el('div', { class: 'panel-head' }, [
      el('h2', { text: title }),
      note ? el('span', { class: 'panel-note spacer', text: note }) : null,
    ]),
    el('div', { class: 'panel-body' }, children),
  ]);
}

function actions(children) {
  return el('div', { class: 'setup-actions' }, children);
}

function errorBox(message) {
  return el('div', { class: 'notice bad', text: message });
}

/** Run an async handler with a busy button and a visible error on failure. */
function guard(button, box, fn) {
  return async () => {
    clear(box);
    const label = button.textContent;
    button.disabled = true;
    button.textContent = 'Working…';
    try {
      await fn();
    } catch (error) {
      box.append(errorBox(error.message || String(error)));
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  };
}

/* ------------------------------------------------------------------- screens */
function screenTelegram() {
  const apiId = input('api-id', { value: state.apiId, inputmode: 'numeric', placeholder: '1234567' });
  const apiHash = input('api-hash', { value: state.apiHash, placeholder: '0123456789abcdef…' });
  const phone = input('phone', { value: state.phone, placeholder: '+66812345678' });
  const box = el('div');
  const next = el('button', { class: 'btn primary', text: 'Send me the code' });

  next.onclick = guard(next, box, async () => {
    state.apiId = apiId.value.trim();
    state.apiHash = apiHash.value.trim();
    state.phone = phone.value.trim();
    const result = await post('/telegram/send-code', {
      api_id: Number(state.apiId),
      api_hash: state.apiHash,
      phone: state.phone,
    });
    if (result.already_signed_in) {
      state.account = result.account;
      go('group');
      loadGroups();
    } else {
      go('signin');
    }
  });

  return panel('Telegram account', 'step 1 of 5', [
    el('p', { class: 'setup-lede' }, [
      'The bridge reads the signal group as your own account, because a bot cannot join a group it does not administer. ',
      el('br'),
      el('span', { class: 'th', text: 'ระบบอ่านกลุ่มด้วยบัญชีของคุณเอง เพราะบอทเข้ากลุ่มที่ไม่ได้เป็นแอดมินไม่ได้' }),
    ]),
    el('p', { class: 'setup-lede' }, [
      'Get the API ID and hash from ',
      el('a', { href: 'https://my.telegram.org', target: '_blank', rel: 'noopener', text: 'my.telegram.org' }),
      ' → API development tools.',
    ]),
    field('API ID', apiId),
    field('API hash', apiHash),
    field('Phone number', phone, 'With the country code. This is the account that is in the signal group.'),
    box,
    actions([next]),
  ]);
}

function screenSignIn() {
  const code = input('code', { inputmode: 'numeric', placeholder: '12345', autocomplete: 'one-time-code' });
  const box = el('div');
  const submit = el('button', { class: 'btn primary', text: 'Sign in' });
  const passwordWrap = el('div', { hidden: true });
  const password = input('tfa', { type: 'password', autocomplete: 'current-password' });
  passwordWrap.append(
    field('Two-step verification password', password, 'Your Telegram password, not the code.'),
  );

  async function afterSignIn(result) {
    if (result.needs_password) {
      passwordWrap.hidden = false;
      submit.textContent = 'Confirm password';
      submit.onclick = guard(submit, box, async () => {
        const value = password.value;
        password.value = '';
        const done = await post('/telegram/password', { password: value });
        state.account = done.account;
        go('group');
        loadGroups();
      });
      box.append(
        el('div', { class: 'notice info', text: 'This account has two-step verification. Enter its password.' }),
      );
      return;
    }
    state.account = result.account;
    go('group');
    loadGroups();
  }

  submit.onclick = guard(submit, box, async () => {
    const value = code.value.trim();
    code.value = '';
    await afterSignIn(await post('/telegram/sign-in', { code: value }));
  });

  const resend = el('button', { class: 'btn', text: 'Send a new code' });
  resend.onclick = guard(resend, box, async () => {
    await post('/telegram/send-code', {
      api_id: Number(state.apiId),
      api_hash: state.apiHash,
      phone: state.phone,
    });
    box.append(el('div', { class: 'notice ok', text: 'A new code is on its way.' }));
  });

  return panel('The code Telegram just sent you', 'step 2 of 5', [
    el('p', { class: 'setup-lede' }, [
      'Check the Telegram app on your phone — the code arrives as a message from Telegram itself, not by SMS.',
      el('br'),
      el('span', { class: 'th', text: 'รหัสจะส่งเข้าแอป Telegram ในเครื่องคุณ ไม่ได้ส่งเป็น SMS' }),
    ]),
    el('div', { class: 'notice info' }, [
      el('strong', { text: 'Nobody else ever sees this. ' }),
      'The code goes from this page to your own server and straight to Telegram. It is not saved, not written to the log, and not put in the settings file.',
    ]),
    field('Login code', code),
    passwordWrap,
    box,
    actions([submit, resend]),
  ]);
}

async function loadGroups() {
  const target = document.getElementById('group-list');
  if (!target) return;
  clear(target);
  target.append(el('p', { class: 'muted', text: 'Reading your groups…' }));
  try {
    const { groups } = await setupApi('/telegram/groups');
    state.groups = groups;
    renderGroups();
  } catch (error) {
    clear(target);
    target.append(errorBox(error.message));
  }
}

function renderGroups(filter = '') {
  const target = document.getElementById('group-list');
  if (!target) return;
  clear(target);
  const needle = filter.trim().toLowerCase();
  const matches = state.groups.filter((g) => !needle || g.name.toLowerCase().includes(needle));

  if (!matches.length) {
    target.append(el('p', { class: 'muted', text: 'No groups matched.' }));
    return;
  }
  for (const group of matches) {
    const row = el('button', {
      class: `group-row${state.chatId === group.id ? ' selected' : ''}`,
      onclick: () => {
        state.chatId = group.id;
        state.chatName = group.name;
        renderGroups(filter);
      },
    }, [
      el('span', { class: 'group-name', text: group.name }),
      el('span', { class: 'chip neutral', text: group.kind }),
      el('span', { class: 'group-id num', text: group.id }),
    ]);
    target.append(row);
  }
}

function screenGroup() {
  const search = input('search', { placeholder: 'Filter by name…' });
  search.oninput = () => renderGroups(search.value);
  const box = el('div');
  const next = el('button', { class: 'btn primary', text: 'Use this group' });
  next.onclick = guard(next, box, async () => {
    if (!state.chatId) throw new Error('Pick the group the signals are posted in.');
    go('line');
  });

  const screen = panel('Which group are the signals in?', 'step 3 of 5', [
    el('p', { class: 'setup-lede' }, [
      `Signed in as ${state.account}. Pick the group the bridge should read.`,
      el('br'),
      el('span', { class: 'th', text: 'เลือกกลุ่มที่สัญญาณถูกโพสต์' }),
    ]),
    field('Search', search),
    el('div', { class: 'group-list', id: 'group-list' }),
    box,
    actions([next]),
  ]);
  queueMicrotask(() => (state.groups.length ? renderGroups() : loadGroups()));
  return screen;
}

function screenLine() {
  const token = input('line-token', { value: state.lineToken, placeholder: 'long token from the LINE console' });
  const dest = input('line-dest', { value: state.lineDestination, placeholder: 'Cxxxxxxxx…' });
  const box = el('div');
  const test = el('button', { class: 'btn', text: 'Test and send a message' });
  const next = el('button', { class: 'btn primary', text: 'Continue' });
  const skip = el('button', { class: 'btn', text: 'Skip for now' });

  test.onclick = guard(test, box, async () => {
    state.lineToken = token.value.trim();
    state.lineDestination = dest.value.trim();
    const result = await post('/line/test', {
      access_token: state.lineToken,
      destination: state.lineDestination,
      send_test: true,
    });
    state.lineVerified = true;
    box.append(
      el('div', { class: 'notice ok' }, [
        el('strong', { text: `${result.bot} is connected. ` }),
        'A test message was posted — check the LINE group to confirm it arrived.',
      ]),
    );
  });

  next.onclick = guard(next, box, async () => {
    state.lineToken = token.value.trim();
    state.lineDestination = dest.value.trim();
    if (!state.lineToken || !state.lineDestination) {
      throw new Error('Enter the channel access token and the destination id, or choose Skip for now.');
    }
    if (!state.lineVerified) {
      // Verify without pushing, so a typo is caught before the .env is written.
      await post('/line/test', {
        access_token: state.lineToken,
        destination: state.lineDestination,
        send_test: false,
      });
    }
    state.lineEnabled = true;
    state.dryRun = false;
    go('prices');
  });

  skip.onclick = () => {
    // Test mode: everything is received, parsed and stored, nothing is pushed.
    state.lineEnabled = false;
    state.dryRun = true;
    state.lineToken = '';
    state.lineDestination = '';
    go('prices');
  };

  return panel('Where should messages go?', 'step 4 of 5', [
    el('p', { class: 'setup-lede' }, [
      'From the LINE Developers console: Messaging API → Channel access token. The destination is the group id the bot posts into.',
      el('br'),
      el('span', { class: 'th', text: 'เอา token จาก LINE Developers console และใส่ id ของกลุ่มปลายทาง' }),
    ]),
    field('Channel access token', token),
    field(
      'Destination id',
      dest,
      'A group id starts with C, a room with R, a person with U. LINE only reveals a group id through a webhook event — see the operations guide.',
    ),
    box,
    actions([test, next, skip]),
    el('p', { class: 'field-hint', text: 'Skipping starts the bridge in test mode: it reads and parses everything, but posts nothing to LINE. You can turn delivery on later from /admin.' }),
  ]);
}

function screenPrices() {
  const box = el('div');
  const provider = el('select', { class: 'select', id: 'provider' }, [
    el('option', { value: 'twelvedata', text: 'Twelve Data — spot gold, free key needed' }),
    el('option', { value: 'yahoo', text: 'Yahoo — free, no key, but no spot metals' }),
    el('option', { value: 'csv', text: 'CSV files I supply myself' }),
    el('option', { value: 'none', text: 'None — record signals, never judge them' }),
  ]);
  provider.value = state.provider;

  const key = input('price-key', { value: state.priceKey, placeholder: 'API key' });
  const symbol = input('symbol', { value: state.symbol });
  const keyField = field('API key', key, 'Free key from <a href="https://twelvedata.com/pricing" target="_blank" rel="noopener">twelvedata.com</a> — the free plan allows 800 requests a day and this uses about 720.');
  const warn = el('div');

  function refresh() {
    state.provider = provider.value;
    keyField.hidden = provider.value !== 'twelvedata';
    clear(warn);
    const gold = ['XAUUSD', 'XAGUSD'].includes(symbol.value.trim().toUpperCase());
    if (provider.value === 'yahoo' && gold) {
      warn.append(
        errorBox(
          `Yahoo has no spot ${symbol.value.trim().toUpperCase()} — only the futures contract, which trades away from spot. Results would be wrong, so nothing would be judged. Use Twelve Data for gold.`,
        ),
      );
    }
    if (provider.value === 'none') {
      warn.append(
        el('div', { class: 'notice info', text: 'Signals will be recorded and shown, but every result stays PENDING. Nothing is ever invented. You can add a provider later.' }),
      );
    }
  }
  provider.onchange = refresh;
  symbol.oninput = refresh;

  const ambiguity = el('select', { class: 'select' }, [
    el('option', { value: 'SL_FIRST', text: 'Count it as the stop loss (conservative)' }),
    el('option', { value: 'TP_FIRST', text: 'Count it as the take profit' }),
    el('option', { value: 'AMBIGUOUS', text: 'Mark it ambiguous and exclude it' }),
  ]);
  ambiguity.value = state.ambiguity;

  const tz = input('tz', { value: state.timezone });
  const port = input('port', { value: String(state.apiPort), inputmode: 'numeric' });
  const password = input('admin-pw', { value: state.adminPassword, placeholder: 'leave blank and one is generated' });

  const next = el('button', { class: 'btn primary', text: 'Review and finish' });
  next.onclick = guard(next, box, async () => {
    state.provider = provider.value;
    state.priceKey = key.value.trim();
    state.symbol = symbol.value.trim().toUpperCase() || 'XAUUSD';
    state.ambiguity = ambiguity.value;
    state.timezone = tz.value.trim() || 'Asia/Bangkok';
    state.apiPort = Number(port.value) || 8000;
    state.adminPassword = password.value.trim();
    if (state.provider === 'twelvedata' && !state.priceKey) {
      throw new Error('Twelve Data needs an API key. Take the free one, or choose a different provider.');
    }
    go('done');
  });

  const screen = panel('Prices and scoring', 'step 5 of 5', [
    el('p', { class: 'setup-lede' }, [
      'Results are worked out from real price data. Without a provider the system records signals honestly and leaves them unjudged.',
      el('br'),
      el('span', { class: 'th', text: 'ผลลัพธ์คำนวณจากราคาจริง ถ้าไม่มีแหล่งราคา ระบบจะบันทึกสัญญาณไว้แต่ไม่ตัดสินผล' }),
    ]),
    field('Symbol', symbol),
    field('Price source', provider),
    keyField,
    warn,
    field('When a candle contains both the take profit and the stop', ambiguity),
    field('Timezone', tz),
    field('Dashboard port', port),
    field('Admin password', password, 'Used to sign in at /admin. Write it down; it is shown once more on the next screen.'),
    box,
    actions([next]),
  ]);
  queueMicrotask(refresh);
  return screen;
}

function summaryRow(label, value) {
  return el('div', { class: 'summary-row' }, [
    el('span', { class: 'summary-label', text: label }),
    el('span', { class: 'summary-value', text: value }),
  ]);
}

function screenDone() {
  const box = el('div');
  const finish = el('button', { class: 'btn primary', text: 'Save and start' });

  finish.onclick = guard(finish, box, async () => {
    const result = await post('/finish', {
      chat_id: state.chatId,
      line_access_token: state.lineToken,
      line_destination: state.lineDestination,
      line_enabled: state.lineEnabled,
      dry_run: state.dryRun,
      price_provider: state.provider,
      price_api_key: state.priceKey,
      price_symbol: state.symbol,
      ambiguity_rule: state.ambiguity,
      result_mode: state.resultMode,
      timezone: state.timezone,
      admin_password: state.adminPassword,
      api_port: state.apiPort,
    });
    sessionStorage.removeItem('setup-token');
    showFinished(result.admin_password);
  });

  return panel('Ready', 'last look', [
    summaryRow('Telegram account', state.account),
    summaryRow('Source group', `${state.chatName} (${state.chatId})`),
    summaryRow('LINE', state.dryRun ? 'test mode — nothing is posted yet' : `${state.lineDestination}`),
    summaryRow('Prices', state.provider),
    summaryRow('Symbol', state.symbol),
    summaryRow('Same-candle rule', state.ambiguity),
    summaryRow('Timezone', state.timezone),
    el('p', { class: 'field-hint', text: 'Saving writes the settings file, closes this setup page for good, and restarts the service with everything running.' }),
    box,
    actions([finish, el('button', { class: 'btn', text: 'Back', onclick: () => go('prices') })]),
  ]);
}

function showFinished(password) {
  clear(stepsBar);
  clear(view);
  const port = state.apiPort;
  view.append(
    panel('Done', null, [
      el('p', { class: 'setup-lede', text: 'The service is restarting with your settings. Give it about ten seconds.' }),
      el('div', { class: 'notice ok' }, [
        el('strong', { text: 'Admin password: ' }),
        el('code', { class: 'num', text: password }),
        el('br'),
        'Write this down now — it is not shown again. It is also in the settings file on the server.',
      ]),
      state.dryRun
        ? el('div', { class: 'notice info', text: 'Started in test mode: messages are read, parsed and stored, but nothing is posted to LINE. Turn delivery on from /admin when you are happy with the parsing.' })
        : null,
      el('p', {}, [
        el('a', { class: 'btn primary', href: '/dashboard', text: 'Open the dashboard' }),
        ' ',
        el('a', { class: 'btn', href: '/admin', text: 'Open admin' }),
      ]),
      el('p', { class: 'field-hint', text: `If the pages do not load yet, wait a moment and reload. The setup link no longer works — that is deliberate.` }),
      port !== 8000
        ? el('p', { class: 'field-hint', text: `The dashboard now listens on port ${port}.` })
        : null,
    ]),
  );
}

/* -------------------------------------------------------------------- token */
function screenToken(message) {
  const token = input('token', { placeholder: 'paste the token from the installer' });
  const box = el('div');
  const go_ = el('button', { class: 'btn primary', text: 'Unlock setup' });
  go_.onclick = guard(go_, box, async () => {
    state.token = token.value.trim();
    await setupApi('/verify-token', { method: 'POST' });
    sessionStorage.setItem('setup-token', state.token);
    render();
  });

  return panel('Setup link', null, [
    message ? errorBox(message) : null,
    el('p', { class: 'setup-lede' }, [
      'Setup is locked to whoever can read the server. The installer printed a link containing this token; you can also read it on the server with:',
    ]),
    el('pre', { class: 'code-block', text: 'sudo cat /opt/signal/data/setup-token' }),
    field('Setup token', token),
    box,
    actions([go_]),
  ]);
}

/* ------------------------------------------------------------------- render */
function renderSteps() {
  clear(stepsBar);
  if (!state.token) return;
  for (const [key, label] of STEPS) {
    const done = state.reached.has(key) && key !== state.step;
    stepsBar.append(
      el('li', {
        class: `step${key === state.step ? ' active' : ''}${done ? ' done' : ''}`,
        text: label,
      }),
    );
  }
}

const SCREENS = {
  telegram: screenTelegram,
  signin: screenSignIn,
  group: screenGroup,
  line: screenLine,
  prices: screenPrices,
  done: screenDone,
};

function render() {
  renderSteps();
  clear(view);
  if (!state.token) {
    view.append(screenToken());
    return;
  }
  if (!state.secure) {
    view.append(
      el('div', { class: 'notice warn' }, [
        el('strong', { text: 'This page is not encrypted. ' }),
        'Anything typed here — including your Telegram code and the LINE token — crosses the network in the clear. Fine on a private network you trust. Otherwise close this, and either install with a domain so it is served over HTTPS, or reach it through an SSH tunnel:',
        el('pre', { class: 'code-block', text: `ssh -L 8000:localhost:${state.apiPort} root@your-server\n# then open http://localhost:8000/setup` }),
      ]),
    );
  }
  view.append(SCREENS[state.step]());
}

async function boot() {
  const params = new URLSearchParams(location.search);
  state.token = params.get('token') || sessionStorage.getItem('setup-token') || '';
  if (params.get('token')) {
    sessionStorage.setItem('setup-token', state.token);
    // Keep the token out of the address bar, browser history and any referrer.
    history.replaceState(null, '', location.pathname);
  }

  const dot = document.querySelector('#conn .dot');
  const text = document.getElementById('conn-text');
  try {
    const status = await api('/api/setup/status');
    state.secure = status.secure;
    if (status.timezone) state.timezone = status.timezone;
    if (status.api_port) state.apiPort = status.api_port;
    if (status.configured) {
      dot.className = 'dot ok';
      text.textContent = 'already configured';
      clear(view);
      clear(stepsBar);
      view.append(
        panel('Already set up', null, [
          el('p', { class: 'setup-lede', text: 'This install is configured, so the setup wizard is closed. Change settings from the admin dashboard.' }),
          el('p', {}, [el('a', { class: 'btn primary', href: '/dashboard', text: 'Open the dashboard' })]),
        ]),
      );
      return;
    }
    dot.className = status.secure ? 'dot ok' : 'dot warn';
    text.textContent = status.secure ? 'encrypted' : 'not encrypted';
  } catch (error) {
    dot.className = 'dot bad';
    text.textContent = 'cannot reach the server';
  }

  if (state.token) {
    // A stale token from a previous session should ask again, not fail later.
    try {
      await setupApi('/verify-token', { method: 'POST' });
    } catch (error) {
      state.token = '';
      sessionStorage.removeItem('setup-token');
      clear(view);
      view.append(screenToken(error.status === 401 ? 'That setup token is not right.' : error.message));
      return;
    }
  }
  render();
}

boot();
