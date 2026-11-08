'use strict';
// Dependency-free DOM fixture for the read-only Browser UI; run using Node 22.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ui = path.join(__dirname, '..', 'mirror_api', 'static');
const html = fs.readFileSync(path.join(ui, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(ui, 'app.js'), 'utf8');
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
    readErrors: {}, truncated: {}}
});
const mirror = fixture('docke-windows-mirror', sha('a'), 'my-task', {checked: false});
const approvals = fixture('fortis-approvals', sha('c'), 'gw-task', {checked: false});
let clipboard = null;
let pulled = false;
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
    requestId: '22345678-1234-4234-8234-123456789abc', reasons: []
  })};
  if (url.endsWith('/pull-execute')) {
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['X-CSRF-Token'], 'fixture-csrf');
    assert.equal(JSON.parse(options.body).confirm, 'PULL_WINDOWS_MAIN');
    pulled = true;
    return {ok: true, json: async () => ({state: 'EXECUTED', head: sha('d'),
      requestId: '22345678-1234-4234-8234-123456789abc'})};
  }
  if (url === '/api/v1/repositories') return {ok: true, json: async () => ({
    checkedAt: '2026-11-08T00:00:00Z', errors: [], repositories: [mirror, approvals]})};
  if (url.startsWith('/api/v1/repositories/') && url.endsWith('?remote=true')) {
    const name = decodeURIComponent(url.slice('/api/v1/repositories/'.length, -'?remote=true'.length));
    return {ok: true, json: async () => ({repository: fixture(name, name === 'docke-windows-mirror' ? sha('a') : (pulled ? sha('d') : sha('c')),
      'updated-task', {checked: true, relation: 'equal'})})};
  }
  throw Error('unexpected fetch ' + url);
};
const sandbox = vm.createContext({
  document: {getElementById: key => elements[key], createElement: tag => new Element(tag)},
  navigator: {clipboard: {writeText: async value => { clipboard = value; }}},
  fetch, console, AbortController, Map, Promise,
  window: {confirm: () => { throw Error('Unexpected second confirmation modal'); }}
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
  await elements['copy-details'].onclick();
  assert.equal(clipboard, elements['worktree-full'].textContent, 'Copy entire selected worktree detail block');
  list[1].onclick();
  assert.match(elements['row-full'].textContent, /fortis-approvals/);
  assert.match(elements['worktree-full'].textContent, /gw-task/);
  assert.equal(elements['panel-worktrees'].hidden, false, 'Tab selection preserved per repo');
  elements['tab-row'].onclick();
  const check = elements['selected-remote'].onclick();
  await check;
  assert.ok(requests.includes('/api/v1/repositories/fortis-approvals?remote=true'));
  assert.equal(elements['primary-rows'].children.length, 2, 'Remote refresh does not add task rows');
  assert.equal(elements['primary-rows'].children[1].classList.contains('selected'), true);
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'АКТУАЛЕН');
  assert.equal(elements['tab-row'].attributes['aria-selected'], 'true');
  assert.match(elements['row-full'].textContent, /РАВЕН GITHUB/);
  assert.equal(elements['pull-windows'].disabled, false, 'Manual Pull is enabled on selected row');
  await elements['pull-windows'].onclick();
  assert.equal(requests.filter(url => url.endsWith('/pull-execute')).length, 1,
    'Explicit owner approval triggers exactly one Execute');
  assert.match(elements['pull-result'].textContent, /Windows main обновлён/);
  assert.match(elements['pull-result'].textContent, new RegExp(sha('d')));
  assert.equal(elements['primary-rows'].children.length, 2);
  assert.equal(elements['primary-rows'].children[1].children[11].textContent, 'АКТУАЛЕН');
  const previewOptions = requestOptions.filter((_, index) => requests[index].endsWith('/pull-preview'));
  assert.equal(previewOptions.length, 1);
  assert.ok(previewOptions.every(opt => opt.method === 'POST' && opt.headers['X-CSRF-Token'] === 'fixture-csrf'));
  console.log('MIRROR_PRIMARY_TABLE_SMOKE_PASS');
})().catch(error => {console.error(error); process.exitCode = 1;});
