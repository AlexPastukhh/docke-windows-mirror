'use strict';
// Dependency-free DOM fixture for the read-only Browser UI; run using Node 22.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ui = path.join(__dirname, '..', 'mirror_api', 'static');
const html = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(ui, 'app.js'), 'utf8');
assert.ok(!html.includes('Граница заголовка — изменить ширину; двойной щелчок — сбросить'),
  'Requested column resize hint is removed');
assert.ok(!html.includes('Одна строка на Docker primary · task worktrees только в нижней вкладке'),
  'Requested intro hint is removed');
assert.ok(html.indexOf('id="group-controls"') > html.indexOf('id="main-scroll"') &&
  html.indexOf('id="group-controls"') < html.indexOf('id="main-table"'),
  'Docker/Windows fold controls live immediately above the corresponding table headers');
const IDs = [...html.matchAll(/\bid="([a-z0-9-]+)"/g)].map(x => x[1]);
const unique = new Set(IDs);
assert.equal(unique.size, IDs.length, 'All DOM ids must be unique');
for (const item of ['primary-rows', 'row-full', 'worktree-rows', 'worktree-full',
                    'tab-row', 'tab-worktrees', 'copy-details', 'main-scroll']) {
  assert.ok(unique.has(item), 'Missing HTML id: ' + item);
}
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.textContent = ''; this.title = '';
    this.dataset = {}; this.attributes = {}; this.disabled = false; this.hidden = false;
    this.style = {}; this.value = ''; this.checked = false; this.tabIndex = -1;
    this.scrollLeft = 0; this.scrollTop = 0; this.classSet = new Set();
    this.classList = {add: value => this.classSet.add(value),
      toggle: (value, on) => { if (on) this.classSet.add(value); else this.classSet.delete(value); },
      contains: value => this.classSet.has(value)};
  }
  set className(value) { this.classSet = new Set(String(value).split(' ').filter(Boolean)); }
  get className() { return [...this.classSet].join(' '); }
  append(child) { this.children.push(child); }
  replaceChildren(...next) { this.children = [...next]; }
  setAttribute(key, value) { this.attributes[key] = value; }
}
const elements = Object.fromEntries(IDs.map(key => [key, new Element()]));
// DOM fixture must contain the 13 actual header cells from index.html.
elements['main-headers'].children = Array.from({length: 13}, () => new Element('th'));
const browserStorage = new Map();
const makeStorage = () => ({
  getItem: key => browserStorage.has(key) ? browserStorage.get(key) : null,
  setItem: (key, value) => browserStorage.set(key, String(value)),
  removeItem: key => browserStorage.delete(key)
});
const localStorage = makeStorage();
const sessionStorage = makeStorage();
const winEvents = new Map();
const sha = digit => digit.repeat(40);
const fixture = (name, head, task, opts = {}) => ({
  repoId: name,
  docker: {
    primary: {kind: 'primary', path: '/workspace/repo/Repos/' + name, branch: 'main',
      head, clean: true, changed: 0, changedFiles: [], origin: `https://github.com/AlexPastukhh/${name}.git`,
      status: 'registered'},
    worktrees: [{kind: 'primary', branch: 'main', path: '/workspace/repo/Repos/' + name, head},
      {kind: 'worktree', branch: 'task/' + task, path: '/workspace/repo/Worktrees/' + name + '/' + task,
        head: sha('b'), remoteSha: sha('b'), changed: 2, clean: false, changedFiles: [' M task.txt', '?? new.txt'],
        status: 'registered', registered: true, targetEligible: true,
        pullRequest: {status: 'OPEN', data: {number: 7, title: 'Example', url: `https://github.com/AlexPastukhh/${name}/pull/7`,
          head: {sha: sha('b')}}}, ci: {status: 'OBSERVED_SUCCESS_NOT_REQUIRED_GATE', workflowRuns: []}}]
  },
  windows: {exists: true, branch: 'main', head: head, clean: true, changedCount: 0,
    changedFiles: [], originValid: true, relation: opts.relation || 'unknown',
    origin: `https://github.com/AlexPastukhh/${name}.git`, error: null},
  github: {head: opts.checked ? head : null, branches: opts.checked ? {main: head} : {},
    error: opts.checked ? null : 'NOT_CHECKED', requiredCI: 'NOT_VERIFIED',
    readErrors: {}, truncated: {}},
  actions: {pull_main_browser: {available: true}}
});
const mirror = fixture('docke-windows-mirror', sha('a'), 'my-task', {checked: false});
const approvals = fixture('fortis-approvals', sha('c'), 'gw-task', {checked: false});
let clipboard = null;
let pulled = false, uncertainMode = false, uncertainReads = 0, failRemoteOnce = false;
let denyGatewayRead = false, receiptOverride = null, safetyBlocked = false;
let deferRefresh = false, releaseRefresh = null;
let deferCheckRepo = null, releaseCheck = null;
const heldCheckRepos = new Set(), heldCheckReleases = new Map(), seenChecks = [];
const requests = [], requestOptions = [];
const fetch = async (url, options = {}) => {
  requests.push(url); requestOptions.push(options);
  if (url === '/api/v1/capabilities') return {ok: true, json: async () => ({
    readOnly: false, browserPull: {configured: true, mode: 'local_owner_confirm'}})};
  if (url === '/api/v1/csrf') return {ok: true, json: async () => ({csrf: 'fixture-csrf'})};
  if (url.endsWith('/pull-preview')) return {ok: true, json: async () => ({
    available: true, repoId: 'fortis-approvals',
    windowsHead: sha('c'), targetSha: sha('d'), dockerHead: sha('d'),
    origin: 'https://github.com/AlexPastukhh/fortis-approvals.git',
    fastForward: 'TO_BE_VERIFIED_AFTER_FETCH', requiredCI: 'NOT_VERIFIED',
    previewId: '12345678-1234-4234-8234-123456789abc',
    requestId: '22345678-1234-4234-8234-123456789abc', reasons: [],
    timingsMs: {previewTotal: 150, previewGateway: 90}
  })};
  if (url.endsWith('/pull-execute')) {
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-CSRF-Token'], 'fixture-csrf');
    assert.equal(JSON.parse(options.body).confirm, 'PULL_WINDOWS_MAIN');
    pulled = true;
    return {ok: true, json: async () => uncertainMode ? {
      state: 'UNCERTAIN', head: null, error: 'WINDOWS_READBACK_UNCONFIRMED',
      readbackIssues: ['WORKTREE_NOT_CLEAN'], requestId: '22345678-1234-4234-8234-123456789abc'
    } : {state: 'EXECUTED', head: sha('d'),
      requestId: '22345678-1234-4234-8234-123456789abc',
      timingsMs: {gatewayMain: 200, windowsFetch: 350, fastForward: 4, executeTotal: 650}}};
  }
  if (url.includes('/pull-results/')) {
    if (receiptOverride) return receiptOverride;
    uncertainReads++;
    return {ok: true, json: async () => ({
      state: uncertainMode && uncertainReads < 2 ? 'UNCERTAIN' : 'EXECUTED',
      head: uncertainMode && uncertainReads < 2 ? null : sha('d'),
      error: uncertainMode && uncertainReads < 2 ? 'WINDOWS_READBACK_UNCONFIRMED' : undefined,
      readbackIssues: uncertainMode && uncertainReads < 2 ? ['WORKTREE_NOT_CLEAN'] : [],
      requestId: '22345678-1234-4234-8234-123456789abc',
      timingsMs: {gatewayMain: 200, windowsFetch: 350, fastForward: 4, executeTotal: 650}
    })};
  }
  if (url === '/api/v1/repositories') return {ok: true, json: async () => ({
    checkedAt: '2026-11-08T00:00:00Z', errors: [], repositories: [mirror, approvals]})};
  if (url.startsWith('/api/v1/repositories/') && url.endsWith('?remote=details')) {
    const name = decodeURIComponent(url.slice('/api/v1/repositories/'.length, -'?remote=details'.length));
    return {ok: true, json: async () => ({checkedAt:'2026-11-09T12:00:00Z', repository:
      fixture(name, name === 'docke-windows-mirror' ? sha('a') : (pulled ? sha('d') : sha('c')),
      'reviewed-task', {checked:true,relation:'equal'})})};
  }
  if (url.startsWith('/api/v1/repositories/') && url.endsWith('/check-windows')) {
    assert.equal(options.method, 'POST', 'Owner Git metadata fetch requires POST');
    assert.equal(options.headers['X-CSRF-Token'], 'fixture-csrf', 'Owner check requires CSRF');
    assert.equal(options.body, '{}', 'No caller-supplied ref/URL/command');
    if (failRemoteOnce) {failRemoteOnce = false; throw Error('Simulated Git metadata fetch failure');}
    const name = decodeURIComponent(url.slice('/api/v1/repositories/'.length, -'/check-windows'.length));
    seenChecks.push(name);
    if (heldCheckRepos.has(name)) await new Promise(resolve => {heldCheckReleases.set(name,resolve);});
    if (deferCheckRepo === name) await new Promise(resolve => {releaseCheck = resolve;});
    const row = fixture(name, name === 'docke-windows-mirror' ? sha('a') : (pulled ? sha('d') : sha('c')),
                        'updated-task', {checked:true, relation:'equal'});
    if (denyGatewayRead && name === 'fortis-approvals') {
      row.github.head = null; row.github.branches = {};
      row.github.error = 'GITHUB_HTTP_403';
      row.github.readErrors = {branches: 'GITHUB_HTTP_403'};
      row.github.githubRepository = 'fortis-approvals';
      row.actions.pull_main_browser = {available: false, reasons: ['GITHUB_MAIN_UNVERIFIED']};
    }
    return {ok: true, json: async () => ({checkedAt:'2026-11-09T12:00:00Z', repository: row})};
  }
  if (url.startsWith('/api/v1/repositories/') && url.endsWith('?remote=true')) {
    const name = decodeURIComponent(url.slice('/api/v1/repositories/'.length, -'?remote=true'.length));
    if (deferRefresh && name === 'fortis-approvals')
      await new Promise(resolve => {releaseRefresh = resolve;});
    return {ok: true, json: async () => ({repository: {...fixture(name, name === 'docke-windows-mirror' ? sha('a') : (pulled ? sha('d') : sha('c')),
      'updated-task', {checked: true, relation: 'equal'}), pullSafety:{blocked:safetyBlocked}}})};
  }
  throw Error('unexpected fetch ' + url);
};
const sandbox = vm.createContext({
  document: {getElementById: key => elements[key], createElement: tag => new Element(tag)},
  navigator: {clipboard: {writeText: async value => { clipboard = value; }}},
  fetch, console, AbortController, Map, Promise, localStorage, sessionStorage,
  window: {confirm: () => { throw Error('Unexpected second confirmation modal'); },
    addEventListener: (event, callback) => winEvents.set(event, callback),
    removeEventListener: event => winEvents.delete(event)}
});
vm.runInContext(source, sandbox, {filename: 'app.js'});
(async () => {
  // Async initialization first fetches capabilities then local primary inventory.
  for (let count = 0; count < 15 && elements['primary-rows'].children.length !== 2; count++) {
    await new Promise(resolve => setImmediate(resolve));
  }
  const list = elements['primary-rows'].children;
  assert.equal(list.length, 2, 'Only primary repos appear as top-level rows');
  assert.ok(list.every(row => row.children.length === 13), 'Old 13-column schema');
  assert.equal(elements['panel-row'].hidden, false, 'Detail Row tab is initial');
  assert.equal(elements['panel-worktrees'].hidden, true, 'Worktrees must be opt-in');
  assert.match(elements['row-full'].textContent, /MAIN: DOCKER \/ GITHUB \/ WINDOWS/);
  // Independent group visibility: both hidden => only repository name remains.
  const headers = elements['main-headers'].children;
  const columns = elements['main-cols'].children;
  assert.equal(columns.length, 13);
  assert.equal(elements['toggle-docker'].style.left, '205px',
    'Docker control above first Docker column');
  assert.equal(elements['toggle-windows'].style.left, '760px',
    'Windows control above first Windows column');
  assert.equal(columns[1].style.width, '105px');
  assert.equal(columns[5].style.width, '113px');
  assert.equal(columns[9].style.width, '155px');
  assert.equal(columns[12].style.width, '226px');
  elements['toggle-docker'].onclick();
  assert.equal(headers[1].hidden, true);
  assert.equal(headers[6].hidden, false);
  assert.equal(elements['toggle-windows'].style.left, '337px',
    'Collapsed Docker control remains available before visible Windows group');
  assert.equal(headers[11].hidden, false);
  elements['toggle-windows'].onclick();
  assert.equal(headers[1].hidden, true);
  assert.equal(headers[6].hidden, true);
  assert.equal(headers[11].hidden, true);
  assert.equal(headers[0].hidden, false);
  assert.equal(elements['main-table'].style.width, '205px');
  assert.equal(elements['primary-rows'].children[0].children.filter(cell => !cell.hidden).length, 1);
  assert.equal(elements['toggle-docker'].attributes['aria-pressed'], 'false');
  elements['toggle-docker'].onclick();
  elements['toggle-windows'].onclick();
  assert.equal(headers[11].hidden, false);
  const grip = headers[1].children[0];
  grip.onpointerdown({button: 0, clientX: 100, preventDefault() {}, stopPropagation() {}});
  winEvents.get('pointermove')({clientX: 157});
  winEvents.get('pointerup')({});
  assert.equal(columns[1].style.width, '162px', 'Drag resizes first compact column');
  assert.equal(JSON.parse(browserStorage.get('fortis.mirror.columns.v1')).widths[1], 162,
    'Resized column width persisted');
  grip.onkeydown({key: 'ArrowRight', preventDefault() {}, stopPropagation() {}});
  assert.equal(columns[1].style.width, '174px', 'Keyboard resizing works');
  assert.equal(vm.runInContext('readLayout().widths[1]', sandbox), 174,
    'Stored layout survives re-reading preferences');
  grip.ondblclick({preventDefault() {}, stopPropagation() {}});
  assert.equal(columns[1].style.width, '105px', 'Double click restores the default');
  elements['repo-filter'].value = 'fortis';
  elements['repo-filter'].oninput();
  assert.equal(elements['primary-rows'].children.length, 1, 'Search narrows the repository list');
  elements['repo-filter'].value = '';
  elements['repo-filter'].oninput();
  assert.equal(elements['primary-rows'].children.length, 2);
  elements['repo-filter'].value = 'not-found-anywhere';
  elements['repo-filter'].oninput();
  assert.match(elements['primary-rows'].children[0].children[0].textContent, /ничего не найдено/);
  elements['toggle-docker'].onclick();
  elements['toggle-windows'].onclick();
  assert.equal(elements['primary-rows'].children[0].children[0].colSpan, 1,
    'Empty-filter placeholder remains functional with both groups collapsed');
  elements['toggle-docker'].onclick();
  elements['toggle-windows'].onclick();
  elements['repo-filter'].value = '';
  elements['repo-filter'].oninput();
  elements['primary-rows'].children[0].onclick();
  assert.match(elements['row-full'].textContent, new RegExp(sha('a')));
  assert.match(elements['row-full'].textContent, /Windows · Ветка для просмотра: main/);
  assert.match(elements['row-full'].textContent, /GitHub main SHA: НЕ ПРОВЕРЕН/);
  assert.ok(!elements['row-full'].textContent.includes('task/my-task'), 'Worktrees do not clutter primary details');
  await elements['copy-details'].onclick();
  assert.equal(clipboard, elements['row-full'].textContent, 'Copy entire primary detail block');
  elements['tab-worktrees'].onclick();
  assert.equal(elements['panel-row'].hidden, true);
  assert.equal(elements['panel-worktrees'].hidden, false);
  assert.equal(elements['worktree-rows'].children.length, 1, 'Only one linked worktree, no primary');
  assert.match(elements['worktree-full'].textContent, /task\/my-task/);
  assert.match(elements['worktree-full'].textContent, /PR номер: 7/);
  assert.match(elements['worktree-check-status'].textContent, /не проверены/,
    'Primary row listing does not imply fresh PR/CI verification');
  await elements['check-worktree-details'].onclick();
  assert.ok(requests.includes('/api/v1/repositories/docke-windows-mirror?remote=details'),
    'Owner can opt in to complete worktree PR/CI inspection');
  assert.match(elements['worktree-check-status'].textContent, /Подробные PR\/CI проверены/);
  await elements['copy-details'].onclick();
  assert.equal(clipboard, elements['worktree-full'].textContent, 'Copy entire selected worktree detail block');
  list[1].onclick();
  assert.match(elements['row-full'].textContent, /fortis-approvals/);
  assert.match(elements['worktree-full'].textContent, /gw-task/);
  assert.equal(elements['panel-worktrees'].hidden, false, 'Tab selection preserved per repo');
  elements['tab-row'].onclick();
  const check = elements['selected-remote'].onclick();
  await check;
  assert.ok(requests.includes('/api/v1/repositories/fortis-approvals/check-windows'));
  assert.ok(requests.filter(url => url.endsWith('/check-windows')).every(url =>
    requestOptions[requests.indexOf(url)].method === 'POST'));
  assert.ok(requests.includes('/api/v1/csrf'));
  assert.ok(!requests.includes('/api/v1/repositories/fortis-approvals?remote=details'),
    'Check selected does not fan out slow PR/CI inspection');
  assert.ok(!requests.includes('/api/v1/repositories/docke-windows-mirror/check-windows'),
    'Check selected does not fetch metadata for other repositories');
  assert.equal(elements['primary-rows'].children.length, 2, 'Remote refresh does not add task rows');
  assert.equal(elements['primary-rows'].children[1].classList.contains('selected'), true);
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'АКТУАЛЕН');
  // Docker main can be proven behind while Windows also trails GitHub:
  // the browser must name both discrepancies and the actual Pull blocker.
  const stale = vm.runInContext("rows.find(row => row.repoId === 'fortis-approvals')", sandbox);
  stale.github.head = sha('d'); stale.github.branches.main = sha('d');
  stale.docker.primary.remoteRelation = 'behind';
  stale.docker.primary.remoteRelationEvidence = 'GATEWAY_MAIN_HISTORY';
  stale.windows.relation = 'behind';
  stale.actions.pull_main_browser = {available: false, reasons: ['DOCKER_MAIN_NOT_AT_GITHUB_MAIN']};
  vm.runInContext('render()', sandbox);
  assert.equal(elements['primary-rows'].children[1].children[3].textContent, 'ОТСТАЁТ ОТ GITHUB');
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'DOCKER ОТСТАЁТ');
  assert.match(elements['row-full'].textContent, /Windows main ↔ GitHub: ОТСТАЁТ ОТ GITHUB/);
  assert.match(elements['row-full'].textContent, /Доказательство Docker ↔ GitHub: GATEWAY_MAIN_HISTORY/);
  assert.match(elements['row-full'].textContent, /Почему Windows Pull недоступен: Docker main не совпадает/);
  assert.equal(elements['pull-windows'].disabled, true);
  assert.match(elements['pull-windows'].title, /сначала обновите Docker main/);
  // Successful HTTP/API response with a denied GitHub read is NOT a successful check.
  denyGatewayRead = true;
  await elements['selected-remote'].onclick();
  assert.match(elements['banner'].textContent, /GitHub не подтверждён: 1/);
  assert.match(elements['banner'].textContent, /ошибок запросов: 0/);
  assert.equal(elements['primary-rows'].children[1].children[3].textContent, 'GITHUB: HTTP 403');
  assert.match(elements['row-full'].textContent, /Код ошибки GitHub: GITHUB_HTTP_403/);
  assert.match(elements['row-full'].textContent, /GitHub репозиторий из Docker origin: fortis-approvals/);
  assert.equal(elements['pull-windows'].disabled, true);
  const denied = vm.runInContext("rows.find(row => row.repoId === 'fortis-approvals')", sandbox);
  denied.github.error = 'ORIGIN_MISSING'; denied.github.githubRepository = null;
  vm.runInContext('render()', sandbox);
  assert.equal(elements['primary-rows'].children[1].children[3].textContent, 'НЕТ ORIGIN');
  assert.match(elements['row-full'].textContent, /Код ошибки GitHub: ORIGIN_MISSING/);
  denyGatewayRead = false;
  await elements['selected-remote'].onclick(); // Fresh valid check replaces the diagnostic fixture.
  const beforeAll = requests.filter(url => url.endsWith('/check-windows')).length;
  await elements['remote'].onclick();
  const allRequests = requests.filter(url => url.endsWith('/check-windows')).slice(beforeAll);
  assert.equal(allRequests.length, 2, 'Check all invokes same CSRF POST for both repositories');
  assert.ok(allRequests.includes('/api/v1/repositories/docke-windows-mirror/check-windows'));
  assert.ok(allRequests.includes('/api/v1/repositories/fortis-approvals/check-windows'));
  // Marking is orthogonal to active row/details and survives a bulk check.
  const activeBeforeMark = vm.runInContext('active', sandbox);
  for (const tr of elements['primary-rows'].children) {
    const box = tr.children[0].children[0];
    assert.equal(box.tagName, 'input');
    box.checked = true;
    box.onclick({stopPropagation() {}});
  }
  assert.equal(vm.runInContext('active', sandbox), activeBeforeMark,
    'Checkboxes do not change the detail-row selection');
  assert.match(elements['marked-count'].textContent, /2/);
  assert.equal(elements['marked-remote'].disabled, false);
  const beforeMarked = requests.filter(url => url.endsWith('/check-windows')).length;
  await elements['marked-remote'].onclick();
  const markedRequests = requests.filter(url => url.endsWith('/check-windows')).slice(beforeMarked);
  assert.equal(markedRequests.length, 2, 'Exactly the two marked repositories are checked');
  elements['clear-marked'].onclick();
  assert.match(elements['marked-count'].textContent, /0/);
  assert.equal(elements['marked-remote'].disabled, true);
  elements['mark-visible'].onclick();
  assert.match(elements['marked-count'].textContent, /2/);
  elements['clear-marked'].onclick();
  assert.equal(elements['tab-row'].attributes['aria-selected'], 'true');
  assert.match(elements['row-full'].textContent, /РАВЕН GITHUB/);
  elements['only-attention'].checked = true;
  elements['only-attention'].onchange();
  assert.equal(elements['primary-rows'].children.length, 1,
    'Attention filter excludes verified-current repository');
  elements['only-attention'].checked = false;
  elements['only-attention'].onchange();
  elements['primary-rows'].children[1].onclick();
  failRemoteOnce = true;
  await elements['selected-remote'].onclick();
  assert.match(elements['row-full'].textContent, /Проверка GitHub: ОШИБКА/,
    'Gateway failure must not retain a seemingly-fresh remote status');
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'ОШИБКА ПРОВЕРКИ');
  assert.equal(elements['pull-windows'].disabled, true, 'Unknown GitHub state blocks Pull UI');
  await elements['selected-remote'].onclick();
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'АКТУАЛЕН');
  assert.equal(elements['pull-windows'].disabled, false, 'Manual Pull is enabled on selected row');
  // A slow check of a DIFFERENT clone must not disable this owner's Pull.
  elements['primary-rows'].children[0].onclick();
  deferCheckRepo = 'docke-windows-mirror';
  const slowCheck = elements['selected-remote'].onclick();
  for (let attempts = 0; !releaseCheck && attempts < 15; attempts++)
    await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof releaseCheck, 'function', 'First selected check is in-flight');
  elements['primary-rows'].children[1].onclick();
  assert.equal(elements['pull-windows'].disabled, false,
    'Pull of another clone remains enabled during an unrelated Windows check');
  assert.equal(elements['selected-remote'].disabled, false,
    'Personal check of another clone remains enabled during an unrelated check');
  deferRefresh = true; // Slow Gateway table-read must not block confirmed Pull.
  await elements['pull-windows'].onclick();
  deferCheckRepo = null;
  releaseCheck();
  await slowCheck;
  releaseCheck = null;
  assert.equal(typeof releaseRefresh, 'function', 'Success schedules a separate table refresh');
  assert.match(elements['pull-result'].textContent, /Время Pull/);
  assert.match(elements['pull-result'].textContent, /Execute GitHub main: 200 мс/);
  assert.match(elements['pull-result'].textContent, /Windows fetch: 350 мс/);
  assert.match(elements['pull-result'].textContent, /Git merge --ff-only: 4 мс/);
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  deferRefresh = false;
  releaseRefresh();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests.filter(url => url.endsWith('/pull-execute')).length, 1,
    'Explicit owner approval triggers exactly one Execute');
  assert.match(elements['pull-result'].textContent, /Windows Pull подтверждён/);
  assert.match(elements['pull-result'].textContent, new RegExp(sha('d')));
  assert.equal(elements['primary-rows'].children.length, 2);
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'АКТУАЛЕН');
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  // A normal UNCERTAIN response reconciles without another Execute.
  uncertainMode = true;
  uncertainReads = 0;
  elements['primary-rows'].children[0].onclick();
  const beforeUncertain = requests.filter(url => url.endsWith('/pull-execute')).length;
  await elements['pull-windows'].onclick();
  assert.equal(requests.filter(url => url.endsWith('/pull-execute')).length, beforeUncertain + 1);
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  assert.ok(browserStorage.has('fortis.mirror.pendingPulls.v2'),
    'Uncertain request remains available across page reload in this tab');
  assert.match(elements['pull-result'].textContent, /Git видит локальные изменения/);
  assert.equal(elements['pull-windows'].disabled, true, 'No second Pull while receipt unresolved');
  assert.equal(elements['selected-remote'].disabled, false);
  const beforeRecovery = requests.length;
  await elements['selected-remote'].onclick();
  assert.ok(requests.slice(beforeRecovery).some(url => url.endsWith('?remote=true')));
  assert.ok(!requests.slice(beforeRecovery).some(url => url.endsWith('/check-windows')), 'Pending recovery never fetches Git metadata');
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  assert.ok(!browserStorage.has('fortis.mirror.pendingPulls.v2'),
    'Confirmed receipt is cleared from session storage');
  assert.match(elements['pull-result'].textContent, /Receipt EXECUTED/);
  assert.equal(requests.filter(url => url.endsWith('/pull-execute')).length, beforeUncertain + 1,
    'Reconciliation performs no additional Git operation');
  const previewOptions = requestOptions.filter((_, index) => requests[index].endsWith('/pull-preview'));
  assert.equal(previewOptions.length, 2);
  assert.ok(previewOptions.every(opt => opt.method === 'POST' && opt.headers['X-CSRF-Token'] === 'fixture-csrf'));
  // Different clones retain independent UNCERTAIN receipts on tab switch.
  sandbox.pendingA = {repo:'docke-windows-mirror',requestId:'33333333-3333-4333-8333-333333333333',targetSha:sha('a')};
  sandbox.pendingB = {repo:'fortis-approvals',requestId:'44444444-4444-4444-8444-444444444444',targetSha:sha('d')};
  vm.runInContext('rememberPendingPull(pendingA.repo,pendingA.requestId,pendingA.targetSha)', sandbox);
  vm.runInContext('rememberPendingPull(pendingB.repo,pendingB.requestId,pendingB.targetSha)', sandbox);
  assert.equal(JSON.parse(browserStorage.get('fortis.mirror.pendingPulls.v2')).length,2);
  elements['primary-rows'].children[0].onclick();
  assert.match(elements['pull-result'].textContent,/33333333/);
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  elements['primary-rows'].children[1].onclick();
  assert.match(elements['pull-result'].textContent,/44444444/);
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  vm.runInContext("clearPendingPull('docke-windows-mirror')",sandbox);
  assert.equal(JSON.parse(browserStorage.get('fortis.mirror.pendingPulls.v2')).length,1);
  assert.ok(!unique.has('check-pull-result'), 'Ordinary selected check handles recovery');
  vm.runInContext("clearPendingPull('fortis-approvals')",sandbox);
  assert.ok(!browserStorage.has('fortis.mirror.pendingPulls.v2'));
  // A still-unknown receipt also refreshes the ordinary selected row.
  elements['primary-rows'].children[1].onclick();
  vm.runInContext('rememberPendingPull(pendingB.repo,pendingB.requestId,pendingB.targetSha)',sandbox);
  receiptOverride = {ok:true,json:async () => ({state:'UNCERTAIN',error:'WINDOWS_FF_FAILED',
    phase:'MERGE',requestId:sandbox.pendingB.requestId,expectedHead:sha('c'),targetSha:sha('d'),
    observation:{head:sha('c'),checkedAt:'2026-11-16T01:02:03Z'}})};
  safetyBlocked = true;
  const beforeUnknown = requests.length;
  await elements['selected-remote'].onclick();
  assert.ok(requests.slice(beforeUnknown).some(url => url.endsWith('?remote=true')));
  assert.ok(!requests.slice(beforeUnknown).some(url => url.endsWith('/check-windows') || url.endsWith('/pull-execute')));
  assert.match(elements['pull-result'].textContent,/2026-11-16T01:02:03Z/);
  assert.equal(elements['selected-remote'].disabled,false);
  assert.equal(elements['pull-windows'].disabled,true);
  // Lost in-memory ownership clears only stale UI binding, not server fencing.
  receiptOverride = {ok:false,status:404,json:async () => ({error:'PULL_RECEIPT_NOT_OWNED'})};
  await elements['selected-remote'].onclick();
  assert.ok(!browserStorage.has('fortis.mirror.pendingPulls.v2'));
  assert.match(elements['pull-result'].textContent,/Сессия предыдущего Pull завершилась/);
  assert.equal(elements['pull-windows'].disabled,true,'Server safety survives stale session cleanup');
  assert.match(elements['row-full'].textContent,/PULL: НЕТ ПОДТВЕРЖДЕНИЯ/);
  assert.match(elements['row-full'].textContent,/Ручной Windows Pull \(оценка\): заблокирован предыдущим Pull/);
  assert.ok(!elements['row-full'].textContent.includes('можно запросить свежий Preview'));
  assert.ok(!elements['row-full'].textContent.includes('Workflow · Итог: ТРЕБУЕТ ПРОВЕРКИ'));
  assert.match(elements['pull-windows'].title,/Новый Pull заблокирован/);
  assert.match(elements['pull-windows'].title,/предыдущего Pull/);
  // Known fetch failure shows its category and fresh facts, never auto-retries.
  vm.runInContext('rememberPendingPull(pendingB.repo,pendingB.requestId,pendingB.targetSha)',sandbox);
  receiptOverride = {ok:true,json:async () => ({state:'FAILED',error:'WINDOWS_FETCH_FAILED',phase:'FETCH',
    requestId:sandbox.pendingB.requestId,expectedHead:sha('c'),targetSha:sha('d'),
    diagnostic:{cause:'GIT_AUTH_REQUIRED',exitCode:128},
    observation:{head:sha('c'),checkedAt:'2026-11-16T02:03:04Z'}})};
  safetyBlocked = false;
  const beforeFailed = requests.length;
  await elements['selected-remote'].onclick();
  assert.match(elements['pull-result'].textContent,/Git требует авторизацию/);
  assert.match(elements['pull-result'].textContent,/exit 128/);
  assert.ok(!browserStorage.has('fortis.mirror.pendingPulls.v2'));
  assert.ok(!requests.slice(beforeFailed).some(url => url.endsWith('/pull-execute') || url.endsWith('/check-windows')));
  receiptOverride = null;
  // Check-all + selected checks share a bounded global queue. A queued row
  // can be promoted, and selected controls remain usable during other work.
  sandbox.extraRows = ['alpha','beta','gamma','delta'].map(name =>
    fixture(name,sha('a'),'task-'+name,{checked:false}));
  vm.runInContext('rows.push(...extraRows);render()',sandbox);
  for (const name of ['docke-windows-mirror','fortis-approvals','alpha','beta'])
    heldCheckRepos.add(name);
  const beforeBulk = seenChecks.length;
  const bulk = elements['remote'].onclick();
  for (let tries=0; heldCheckReleases.size<4 && tries<30; tries++)
    await new Promise(resolve=>setImmediate(resolve));
  assert.equal(heldCheckReleases.size,4,'At most four concurrent Windows checks');
  assert.equal(seenChecks.length-beforeBulk,4,'Queued fifth and sixth checks are not started');
  elements['primary-rows'].children[5].onclick();
  assert.equal(elements['selected-remote'].disabled,false,'Queued row can be promoted');
  await elements['selected-remote'].onclick();
  assert.match(elements['banner'].textContent,/начало очереди/);
  heldCheckReleases.get('docke-windows-mirror')();
  heldCheckReleases.delete('docke-windows-mirror');
  for (let tries=0;seenChecks.length<beforeBulk+5 && tries<30;tries++)
    await new Promise(resolve=>setImmediate(resolve));
  assert.equal(seenChecks[beforeBulk+4],'delta','Selected queued repo runs next');
  for (const release of heldCheckReleases.values()) release();
  heldCheckReleases.clear();heldCheckRepos.clear();
  await bulk;
  assert.equal(seenChecks.length-beforeBulk,6,'All six unique repos checked once');
  const legacyId = '22345678-1234-4234-8234-123456789abc';
  const recovered = {requestId:legacyId, state:'EXECUTED', evidence:'TARGET_INCLUDED_READBACK',
    targetIncluded:true, targetSha:sha('d'), head:sha('e'),
    observation:{head:sha('e'),branch:'main',clean:true,originValid:true,error:null}};
  const savedLegacy = {repo:'fortis-approvals',requestId:legacyId,targetSha:sha('d')};
  sandbox.savedLegacy = savedLegacy;
  const beforeHistoryRecovery = requests.length;
  for (const change of [{targetIncluded:false}, {requestId:'wrong'}, {targetSha:sha('a')},
                        {observation:{...recovered.observation,clean:false}}]) {
    receiptOverride = {ok:true,json:async()=>({...recovered,...change})};
    vm.runInContext('rememberPendingPull(savedLegacy.repo,savedLegacy.requestId,savedLegacy.targetSha)',sandbox);
    assert.equal(await vm.runInContext('reconcilePullResult(savedLegacy,{refresh:false})',sandbox),false);
    assert.equal(vm.runInContext('pendingPulls.has(savedLegacy.repo)',sandbox),true);
  }
  receiptOverride = {ok:true,json:async()=>recovered};
  assert.equal(await vm.runInContext('reconcilePullResult(savedLegacy,{refresh:false})',sandbox),true);
  assert.equal(vm.runInContext('pendingPulls.has(savedLegacy.repo)',sandbox),false);
  const historyMessage = vm.runInContext('pullMessages.get(savedLegacy.repo)',sandbox);
  assert.match(historyMessage,/содержится в текущей истории/);
  assert.ok(historyMessage.includes(sha('d')) && historyMessage.includes(sha('e')));
  assert.ok(requests.slice(beforeHistoryRecovery).every(url=>url.includes('/pull-results/')),
    'Legacy convergence only reads the original receipt; no Preview/Execute/fetch');
  receiptOverride = null;
  console.log('MIRROR_PRIMARY_TABLE_SMOKE_PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
