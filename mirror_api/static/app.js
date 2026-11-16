'use strict';

// Only Docker primary clones belong in the main table. Linked task worktrees
// are available on demand in the lower Worktrees tab; no Git checkout occurs.
const MAIN_COLUMNS = [
  'Репозиторий',
  'Docker · Ветка для просмотра', 'Docker · Активная ветка',
  'Docker · GitHub', 'Docker · Изменения', 'Docker · SHA',
  'Windows · Ветка для просмотра', 'Windows · Активная ветка',
  'Windows · GitHub', 'Windows · Изменения', 'Windows · SHA',
  'Workflow · Итог', 'Workflow · Действие'
];
let rows = [], active = null, detailTab = 'row';
let browserPullEnabled = false, localBusy = false;
const markedRepos = new Set();
const rowVersions = new Map();
const repoChecks = new Map();
const checkQueue = [];
let runningChecks = 0, runningDetailChecks = 0;
const MAX_CONCURRENT_CHECKS = 4, MAX_CONCURRENT_DETAILS = 1;
const pullBusyRepos = new Set(), readbackBusyRepos = new Set();
const pendingPulls = new Map(), pullMessages = new Map();
const LAYOUT_KEY = 'fortis.mirror.columns.v1';
const PENDING_PULL_KEY = 'fortis.mirror.pendingPull.v1';
const PENDING_PULLS_KEY = 'fortis.mirror.pendingPulls.v2';
// Retain generous widths for the requested key columns; keep others compact.
const DEFAULT_WIDTHS = [205, 105, 106, 130, 101, 113, 105, 106, 130, 155, 113, 186, 226];
const MIN_WIDTH = 68, MAX_WIDTH = 640;
let layout = {docker: true, windows: true, widths: [...DEFAULT_WIDTHS]};
const resizeHandles = [];
const selectedWorktrees = new Map();
const $ = id => document.getElementById(id);
const el = (tag, value, cls) => {
  const result = document.createElement(tag);
  if (cls) result.className = cls;
  if (value !== undefined) result.textContent = String(value);
  return result;
};
const known = value => value !== null && value !== undefined && value !== '';
const show = value => known(value) ? String(value) : '—';
const short = value => typeof value === 'string' && /^[0-9a-f]{40}$/.test(value) ? value.slice(0, 8) : '—';
const ghChecked = row => Boolean(!row.remoteCheckError && row.github && row.github.head && !row.github.error);
const GITHUB_FAILURE_LABELS = {
  GITHUB_WINDOWS_REQUIRED: 'GITHUB: НУЖЕН WINDOWS',
  GITHUB_CLI_UNAVAILABLE: 'GITHUB: GH CLI НЕДОСТУПЕН',
  GITHUB_AUTH_OR_READ_FAILED: 'GITHUB: ПРОВЕРЬТЕ GH AUTH',
  GITHUB_HTTP_401: 'GITHUB: HTTP 401',
  GITHUB_HTTP_403: 'GITHUB: HTTP 403',
  GITHUB_HTTP_404: 'GITHUB: HTTP 404',
  GITHUB_HTTP_429: 'GITHUB: HTTP 429',
  GITHUB_TIMEOUT: 'GITHUB: ТАЙМ-АУТ',
  GITHUB_MAIN_UNVERIFIED: 'GITHUB MAIN НЕ ПОДТВЕРЖДЁН',
  ORIGIN_MISSING: 'НЕТ ORIGIN',
  ORIGIN_NOT_ALLOWED: 'ORIGIN НЕ РАЗРЕШЁН',
  GATEWAY_HTTP_403: 'GATEWAY: HTTP 403',
  GATEWAY_HTTP_404: 'GATEWAY: HTTP 404',
  GATEWAY_HTTP_429: 'GATEWAY: HTTP 429',
  GATEWAY_HTTP_500: 'GATEWAY: HTTP 500',
  GATEWAY_BRANCH_READ_UNAVAILABLE: 'ОШИБКА ЧТЕНИЯ GATEWAY',
  GATEWAY_READ_UNAVAILABLE: 'GATEWAY НЕДОСТУПЕН',
  GATEWAY_MAIN_UNVERIFIED: 'GITHUB MAIN НЕ ПОДТВЕРЖДЁН',
  GATEWAY_REPOSITORY_NOT_REGISTERED: 'GATEWAY: НЕТ ДОСТУПА К РЕПОЗИТОРИЮ'
};
function githubFailure(row) {
  if (row.remoteCheckError) return 'ОШИБКА ПРОВЕРКИ';
  const code = row.github && row.github.error;
  return code && code !== 'NOT_CHECKED' ? (GITHUB_FAILURE_LABELS[code] || code) : null;
}
const primary = row => row.docker && row.docker.primary || null;
const changedLabel = count => count === null || count === undefined ? 'НЕ ПРОВЕРЕНО' :
  count === 0 ? 'Нет' : 'Есть: ' + count;
const isSha = value => typeof value === 'string' && /^[0-9a-f]{40}$/.test(value);
const isRequestId = value => typeof value === 'string' &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(value);
const isRepoId = value => typeof value === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}$/.test(value);
function boundedWidth(value, index) {
  const minimum = index === 0 ? 150 : MIN_WIDTH;
  return Number.isSafeInteger(value) && value >= minimum && value <= MAX_WIDTH ? value : DEFAULT_WIDTHS[index];
}
function readLayout() {
  try {
    const raw = JSON.parse(localStorage.getItem(LAYOUT_KEY));
    if (raw && typeof raw.docker === 'boolean' && typeof raw.windows === 'boolean' &&
        Array.isArray(raw.widths) && raw.widths.length === MAIN_COLUMNS.length) {
      return {docker: raw.docker, windows: raw.windows,
        widths: raw.widths.map((width, index) => boundedWidth(width, index))};
    }
  } catch (ignored) { /* No storage permissions: keep in-memory layout. */ }
  return {docker: true, windows: true, widths: [...DEFAULT_WIDTHS]};
}
function saveLayout() {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); }
  catch (ignored) { /* Reading and resizing must work without storage. */ }
}
function columnVisible(index) {
  if (index === 0) return true;
  if (index <= 5) return layout.docker;
  if (index <= 10) return layout.windows;
  return layout.docker || layout.windows;
}
function applyLayout() {
  let total = 0;
  const headers = $('main-headers').children;
  const columns = $('main-cols').children;
  MAIN_COLUMNS.forEach((header, index) => {
    const visible = columnVisible(index), width = layout.widths[index];
    if (visible) total += width;
    headers[index].hidden = !visible;
    headers[index].style.width = width + 'px';
    columns[index].hidden = !visible;
    columns[index].style.width = width + 'px';
    const handle = resizeHandles[index];
    if (handle) handle.setAttribute('aria-valuenow', String(width));
  });
  $('main-table').style.width = total + 'px';
  // These buttons live in a thin bar immediately ABOVE the first Docker and
  // Windows table columns (not in hidden THs). Keep them available when one
  // or both groups are collapsed: the owner must always be able to reopen.
  const repoWidth = layout.widths[0];
  const dockerWidth = layout.widths.slice(1, 6).reduce((sum, width) => sum + width, 0);
  const dockerLeft = repoWidth;
  const windowsLeft = repoWidth + (layout.docker ? dockerWidth : 132);
  $('toggle-docker').style.left = dockerLeft + 'px';
  $('toggle-windows').style.left = windowsLeft + 'px';
  $('group-controls').style.width = Math.max(total, windowsLeft + 138) + 'px';
  for (const tr of $('primary-rows').children) {
    if (tr.children.length === 1) {
      tr.children[0].colSpan = MAIN_COLUMNS.filter((_, index) => columnVisible(index)).length;
    } else {
      for (let index = 0; index < MAIN_COLUMNS.length; index++)
        tr.children[index].hidden = !columnVisible(index);
    }
  }
  for (const group of ['docker', 'windows']) {
    const button = $('toggle-' + group);
    button.setAttribute('aria-pressed', String(layout[group]));
    button.textContent = (group === 'docker' ? 'Docker' : 'Windows') +
      (layout[group] ? ': скрыть' : ': показать');
  }
}
function setColumnWidth(index, value) {
  const minimum = index === 0 ? 150 : MIN_WIDTH;
  layout.widths[index] = Math.max(minimum, Math.min(MAX_WIDTH, Math.round(value)));
  applyLayout();
  saveLayout();
}
function setupColumns() {
  layout = readLayout();
  const headers = $('main-headers').children;
  if (headers.length !== MAIN_COLUMNS.length) throw new Error('TABLE_HEADER_MISMATCH');
  for (let index = 0; index < MAIN_COLUMNS.length; index++) {
    const column = el('col');
    $('main-cols').append(column);
    const grip = el('span', undefined, 'column-resizer');
    grip.tabIndex = 0;
    grip.setAttribute('role', 'separator');
    grip.setAttribute('aria-orientation', 'vertical');
    grip.setAttribute('aria-valuemin', String(index === 0 ? 150 : MIN_WIDTH));
    grip.setAttribute('aria-valuemax', String(MAX_WIDTH));
    grip.setAttribute('aria-label', 'Ширина столбца: ' + MAIN_COLUMNS[index]);
    grip.title = 'Перетащить — изменить ширину; двойной щелчок — сбросить';
    grip.onkeydown = event => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      event.preventDefault();
      event.stopPropagation();
      setColumnWidth(index, layout.widths[index] + (event.key === 'ArrowRight' ? 12 : -12));
    };
    grip.ondblclick = event => {
      event.preventDefault();
      event.stopPropagation();
      setColumnWidth(index, DEFAULT_WIDTHS[index]);
    };
    grip.onpointerdown = event => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      const start = event.clientX, width = layout.widths[index];
      const move = next => setColumnWidth(index, width + next.clientX - start);
      const stop = () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', stop);
        window.removeEventListener('pointercancel', stop);
      };
      window.addEventListener('pointermove', move);
      window.addEventListener('pointerup', stop);
      window.addEventListener('pointercancel', stop);
    };
    headers[index].title = MAIN_COLUMNS[index];
    headers[index].append(grip);
    resizeHandles.push(grip);
  }
  applyLayout();
}
// Each clone owns an independent pending receipt. A legacy single-receipt
// session is migrated without ever auto-repeating its Git operation.
function savePendingPulls() {
  try {
    if (pendingPulls.size)
      sessionStorage.setItem(PENDING_PULLS_KEY, JSON.stringify([...pendingPulls.values()]));
    else sessionStorage.removeItem(PENDING_PULLS_KEY);
    sessionStorage.removeItem(PENDING_PULL_KEY);
  } catch (ignored) { /* In-page readback still works without storage. */ }
}
function restorePendingPull() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(PENDING_PULLS_KEY));
    const legacy = JSON.parse(sessionStorage.getItem(PENDING_PULL_KEY));
    const values = Array.isArray(saved) ? saved : (legacy ? [legacy] : []);
    for (const item of values.slice(0, 64)) {
      if (item && isRepoId(item.repo) && isRequestId(item.requestId) && isSha(item.targetSha))
        pendingPulls.set(item.repo, {repo: item.repo, requestId: item.requestId, targetSha: item.targetSha});
    }
    if (legacy && pendingPulls.has(legacy.repo)) savePendingPulls();
  } catch (ignored) { /* Invalid session data cannot authorize a Pull. */ }
}
function rememberPendingPull(repo, requestId, targetSha) {
  pullMessages.delete(repo); // A new receipt must not show stale success.
  pendingPulls.set(repo, {repo, requestId, targetSha});
  savePendingPulls();
  renderPullState();
}
function clearPendingPull(repo) {
  pendingPulls.delete(repo);
  savePendingPulls();
  renderPullState();
}
function renderPullState() {
  const pending = pendingPulls.get(active);
  const message = pullMessages.get(active) || (pending ?
    'Есть неподтверждённый Pull для ' + active + ' · Request ID: ' + pending.requestId +
    '. Нажмите «Проверить выбранный» для обновления состояния.' : '');
  $('pull-result').textContent = message;
  $('pull-result').hidden = !message;
}
function pullEligible(row) {
  return !!(row && row.actions && row.actions.pull_main_browser &&
    row.actions.pull_main_browser.available === true && !row.remoteCheckError && !row.pullSafety?.blocked);
}


const PULL_BLOCKER_TEXT = {
  DOCKER_MAIN_NOT_VERIFIED: 'Docker primary main не подтверждён как чистый и зарегистрированный',
  DOCKER_MAIN_NOT_AT_GITHUB_MAIN: 'Docker main не совпадает с GitHub main; сначала обновите Docker main штатным Gateway workflow',
  GITHUB_MAIN_UNVERIFIED: 'GitHub main не подтверждён',
  PREVIOUS_PULL_UNRESOLVED: 'Новый Pull заблокирован: исход предыдущего Pull не подтверждён',
  WINDOWS_ORIGIN_NOT_VERIFIED: 'Windows origin / Git-клон не подтверждён',
  WINDOWS_NOT_ON_MAIN: 'Windows не на активной ветке main',
  WINDOWS_NOT_CLEAN: 'В Windows есть незакоммиченные изменения или состояние не подтверждено',
  WINDOWS_ALREADY_CURRENT: 'Windows уже находится на актуальном GitHub main',
  WINDOWS_NOT_BEHIND_GITHUB: 'Windows впереди GitHub или история разошлась',
  WINDOWS_FETCH_FAILED: 'Не удалось прочитать новые Git-коммиты на Windows',
  WINDOWS_FETCH_TIMEOUT: 'Windows fetch превысил лимит времени',
  WINDOWS_FETCHED_MAIN_CHANGED: 'GitHub main изменился во время Windows fetch — повторите проверку',
};
function pullBlockers(row) {
  if (row?.pullSafety?.blocked) return PULL_BLOCKER_TEXT.PREVIOUS_PULL_UNRESOLVED;
  if (!row || !row.actions || !row.actions.pull_main_browser) return 'Сначала выполните проверку выбранного репозитория';
  const action = row.actions.pull_main_browser;
  if (action.available === true) return 'Можно запросить Preview; перед выполнением условия и доступность записи проверяются повторно';
  if (!Array.isArray(action.reasons) || !action.reasons.length) return 'Причина не подтверждена, повторите проверку';
  return action.reasons.map(reason => PULL_BLOCKER_TEXT[reason] || ('Проверка не пройдена: ' + reason)).join('; ');
}
function dockerGithub(row) {
  const d = primary(row), gh = row.github || {};
  if (!d) return 'НЕТ PRIMARY';
  if (!ghChecked(row)) return githubFailure(row) || 'НЕ ПРОВЕРЕН';
  const sha = gh.branches && gh.branches[d.branch];
  if (!sha) return gh.truncated && gh.truncated.branches || gh.readErrors && gh.readErrors.branches ?
    'ВЕТКА НЕ ПОДТВЕРЖДЕНА' : 'НЕТ В GITHUB';
  if (sha === d.head) return 'РАВЕН GITHUB';
  switch (d.remoteRelation) {
    case 'behind': return 'ОТСТАЁТ ОТ GITHUB';
    case 'ahead': return 'ВПЕРЕДИ GITHUB';
    case 'diverged': return 'ИСТОРИИ РАЗОШЛИСЬ';
    default: return 'SHA ≠ GH · НАПРАВЛЕНИЕ НЕИЗВЕСТНО';
  }
}
function windowsGithub(row) {
  const w = row.windows || {};
  if (!w.exists) return w.error === 'WINDOWS_CLONE_NOT_FOUND' ? 'НЕТ КЛОНА' : 'НЕ ПРОВЕРЕН';
  if (w.error || !w.originValid) return 'ОШИБКА ORIGIN / GIT';
  if (w.fetchError) return 'ОШИБКА FETCH · ' + w.fetchError;
  if (!ghChecked(row)) return githubFailure(row) || 'НЕ ПРОВЕРЕН';
  if (w.branch !== 'main') return 'WINDOWS: НЕ MAIN';
  switch (w.relation) {
    case 'equal': return 'АКТУАЛЕН К GITHUB';
    case 'ahead': return 'ВПЕРЕДИ GITHUB';
    case 'behind': return 'ОТСТАЁТ ОТ GITHUB';
    case 'diverged': return 'ИСТОРИИ РАЗОШЛИСЬ';
    default: return 'SHA ≠ GH · НАПРАВЛЕНИЕ НЕИЗВЕСТНО';
  }
}
function overall(row) {
  const d = primary(row), w = row.windows || {}, gh = row.github || {};
  if (row.pullSafety?.blocked) return ['PULL: НЕТ ПОДТВЕРЖДЕНИЯ',
    'Обновить состояние обычной проверкой; при сохранении блокировки нужен разбор квитанции Windows'];
  if (!d) return ['НЕТ DOCKER PRIMARY', 'Проверить Git worktree inventory'];
  if (d.branch !== 'main') return ['DOCKER: НЕ MAIN', 'Проверить primary; не переключать автоматически'];
  if (d.clean === false) return ['DOCKER: ИЗМЕНЕНИЯ', 'Разобрать изменения в primary; не использовать reset/stash'];
  if (d.clean !== true) return ['DOCKER: НЕ ПРОВЕРЕН', 'Проверить состояние Docker primary'];
  if (!w.exists) return w.error === 'WINDOWS_CLONE_NOT_FOUND' ?
    ['НЕТ КЛОНА WINDOWS', 'Windows-клон отсутствует'] : ['WINDOWS: НЕ ПРОВЕРЕН', show(w.error)];
  if (w.error || !w.originValid) return ['ОШИБКА WINDOWS', show(w.error || 'ORIGIN НЕ СОВПАДАЕТ')];
  if (w.fetchError) return ['ОШИБКА FETCH WINDOWS', w.fetchError + ' · Команда Pull не выполнялась'];
  if (w.branch !== 'main') return ['WINDOWS: НЕ MAIN', 'Проверить активную ветку Windows'];
  if (w.clean === false) return ['WINDOWS: ИЗМЕНЕНИЯ', 'Разобрать Windows-файлы; Pull запрещён'];
  if (w.clean !== true) return ['WINDOWS: НЕ ПРОВЕРЕН', 'Проверить git status Windows'];
  if (row.remoteCheckError) return ['ОШИБКА ПРОВЕРКИ', 'GitHub не проверен: ' + row.remoteCheckError];
  if (!ghChecked(row)) {
    const failure = githubFailure(row);
    return failure ? ['GITHUB: ' + failure, 'Чтение GitHub main не подтверждено: ' +
      show(gh.error || row.remoteCheckError)] :
      ['НЕ ПРОВЕРЕНО', 'Нажать «Проверить выбранный/все»'];
  }
  if (w.relation === 'diverged') return ['WINDOWS: ИСТОРИИ РАЗОШЛИСЬ', 'Не выполнять Pull'];
  if (w.relation === 'ahead') return ['WINDOWS ВПЕРЕДИ', 'Проверить неожиданные Windows-коммиты'];
  if (dockerGithub(row) !== 'РАВЕН GITHUB') {
    if (d.remoteRelation === 'behind') return ['DOCKER ОТСТАЁТ',
      'Сначала обновить Docker main через Gateway; Windows Pull пока заблокирован'];
    if (d.remoteRelation === 'ahead') return ['DOCKER ВПЕРЕДИ',
      'Не выполнять Windows Pull; проверить неопубликованные Docker-коммиты'];
    if (d.remoteRelation === 'diverged') return ['DOCKER: ИСТОРИИ РАЗОШЛИСЬ',
      'Не выполнять Windows Pull; требуется разбор истории'];
    return ['DOCKER ≠ GITHUB',
      'Направление Docker не доказано; Windows Pull заблокирован до синхронизации Docker'];
  }
  if (w.relation === 'behind') return ['WINDOWS ОТСТАЁТ', 'Нужен guarded Windows main Pull'];
  if (w.relation === 'equal' && w.head === gh.head) return ['АКТУАЛЕН', 'Ничего не требуется'];
  return ['ТРЕБУЕТ ПРОВЕРКИ', 'Направление Git-истории не подтверждено'];
}
function rowFields(row, full = false) {
  const d = primary(row) || {}, w = row.windows || {}, state = overall(row);
  // Both VIEW columns intentionally reflect the primary's actual branch:
  // task branches are not the main-table target and no branch switch exists.
  const dockerBranch = show(d.branch), windowsBranch = w.exists ? show(w.branch) : '—';
  return [
    row.repoId, dockerBranch, dockerBranch,
    dockerGithub(row), changedLabel(d.changed), full ? show(d.head) : short(d.head),
    windowsBranch, w.exists ? windowsBranch : 'НЕТ КЛОНА',
    windowsGithub(row), w.exists ? changedLabel(w.changedCount) : 'Нет клона',
    full ? show(w.head) : short(w.head), state[0], state[1]
  ];
}
function tone(value) {
  if (value === 'АКТУАЛЕН' || value === 'РАВЕН GITHUB' || value === 'АКТУАЛЕН К GITHUB') return 'good';
  if (/ОШИБКА|ИЗМЕНЕНИЯ|РАЗОШЛИСЬ|ВПЕРЕДИ|НЕТ PRIMARY|HTTP 403|HTTP 404|ORIGIN НЕ РАЗРЕШЁН/.test(value)) return 'bad';
  if (/НЕ ПРОВЕРЕН|НЕТ КЛОНА|НЕИЗВЕСТНО/.test(value)) return 'muted';
  return 'warn';
}
function linesOfFiles(files, truncated) {
  if (!Array.isArray(files)) return '  НЕ ПРОВЕРЕНО';
  if (!files.length) return '  Нет';
  return files.map(file => '  ' + file).join('\n') + (truncated ? '\n  ... список ограничен' : '');
}
function rowDetails(row) {
  const values = rowFields(row, true), d = primary(row) || {}, w = row.windows || {}, gh = row.github || {};
  const text = MAIN_COLUMNS.map((header, index) => header + ': ' + values[index]);
  text.push('', 'ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ ПОСЛЕ КОЛОНОК',
    'Docker primary: ' + show(d.path), 'Windows primary: ' + show(w.path || ('%USERPROFILE%\\Repos\\' + row.repoId)),
    'Docker origin: ' + show(d.origin), 'Windows origin: ' + show(w.origin),
    'GitHub репозиторий из Docker origin: ' + show(gh.githubRepository),
    'Windows origin подтверждён: ' + (w.originValid === true ? 'Да' : 'Нет'),
    'Docker worktree status: ' + show(d.status),
    'Docker primary: ' + (d.clean === true ? 'CLEAN' : d.clean === false ? 'DIRTY' : 'НЕ ПРОВЕРЕНО'),
    'Windows состояние: ' + (w.clean === true ? 'CLEAN' : w.clean === false ? 'DIRTY' : 'НЕ ПРОВЕРЕНО'),
    '', 'MAIN: DOCKER / GITHUB / WINDOWS',
    'Docker primary main SHA: ' + (d.branch === 'main' ? show(d.head) : 'НЕ ПОДТВЕРЖДЕН'),
    'GitHub main SHA: ' + (ghChecked(row) ? show(gh.head) : 'НЕ ПРОВЕРЕН'),
    'Windows local main SHA: ' + (w.branch === 'main' ? show(w.head) : 'НЕ ПОДТВЕРЖДЕН'),
    'Windows main ↔ GitHub: ' + windowsGithub(row),
    'Docker main ↔ GitHub: ' + dockerGithub(row),
    'Доказательство Docker ↔ GitHub: ' + show(d.remoteRelationEvidence),
    'Обязательный CI: ' + show(gh.requiredCI),
    'Ручной Windows Pull (оценка): ' + (row.pullSafety?.blocked ? 'заблокирован предыдущим Pull' :
      pullEligible(row) ? 'можно запросить свежий Preview' :
      'Preview / проверки требуются') +
      ' · перед выполнением условия проверяются повторно',
    'Почему Windows Pull недоступен: ' + pullBlockers(row),
    'Незавершённых Pull: ' + (row.pullSafety ? show(row.pullSafety.pendingCount) : 'НЕ ПРОВЕРЕНО'),
    'Windows fetch: ' + show(w.fetchStatus) +
      (w.fetchError ? ' · ' + w.fetchError : ''),
    'Время чтения (мс): Docker ' + show(row.readTimingsMs && row.readTimingsMs.dockerInventory) +
      ', Windows Git ' + show(row.readTimingsMs && row.readTimingsMs.windowsGit) +
      ', Windows fetch ' + show(row.readTimingsMs && row.readTimingsMs.windowsFetch) +
      ', GitHub ' + show(row.readTimingsMs && row.readTimingsMs.githubGateway) +
      ', Git-история ' + show(row.readTimingsMs && row.readTimingsMs.gitAncestry),
    'GitHub этапы (мс): ' + ((gh.githubTimingsMs || gh.gatewayTimingsMs) ? JSON.stringify(gh.githubTimingsMs || gh.gatewayTimingsMs) : 'НЕ ПРОВЕРЕНЫ'),
    'Локальная проверка Windows: ' + show(w.checkedAt),
    'Проверка GitHub: ' + (row.remoteCheckError ? 'ОШИБКА: ' + row.remoteCheckError :
      ghChecked(row) ? show(row.remoteCheckedAt || gh.checkedAt) : githubFailure(row) || 'НЕ ПРОВЕРЕН'),
    'Код ошибки GitHub: ' + show(gh.error),
    'Gateway read errors: ' + (gh.readErrors && Object.keys(gh.readErrors).length ?
      Object.entries(gh.readErrors).map(([key, value]) => key + '=' + value).join('; ') : 'Нет / не получены'),
    '', 'ИЗМЕНЁННЫЕ ФАЙЛЫ DOCKER PRIMARY (git status)',
    linesOfFiles(d.changedFiles, d.changedTruncated),
    '', 'ИЗМЕНЁННЫЕ ФАЙЛЫ WINDOWS PRIMARY (git status)',
    linesOfFiles(w.changedFiles, w.changedTruncated),
    '', 'Рабочие деревья задач: ' + ((row.docker && row.docker.worktrees || []).filter(x => x.kind === 'worktree').length),
    'Для просмотра linked worktrees выберите вкладку «Worktrees».',
    'Просмотр вкладок не переключает Git-ветки. GitHub-действия — только через Fortis Gateway.');
  return text.join('\n');
}
function worktreeGithub(tree, row) {
  const gh = row.github || {};
  if (!ghChecked(row)) return 'НЕ ПРОВЕРЕН';
  if (tree.remoteSha === tree.head && known(tree.remoteSha)) return 'РАВЕН GITHUB';
  if (!tree.remoteSha) return gh.truncated && gh.truncated.branches || gh.readErrors && gh.readErrors.branches ?
    'НЕ ПОДТВЕРЖДЕНО' : 'НЕТ В GITHUB';
  return 'SHA ≠ GH · НЕИЗВЕСТНО';
}
function worktreeDetails(row, tree) {
  const pr = tree.pullRequest || {}, ci = tree.ci || {}, lines = [
    'Репозиторий: ' + row.repoId,
    'Тип: ' + show(tree.kind), 'Ветка: ' + show(tree.branch),
    'Путь: ' + show(tree.path), 'Git HEAD: ' + show(tree.head),
    'Origin: ' + show(tree.origin), 'GitHub SHA ветки: ' + show(tree.remoteSha),
    'Сопоставление GitHub: ' + worktreeGithub(tree, row),
    'Git registered: ' + show(tree.registered), 'Target eligible: ' + show(tree.targetEligible),
    'Состояние записи: ' + show(tree.status), 'Изменений: ' + show(tree.changed),
    'Чистое рабочее дерево: ' + (tree.clean === true ? 'Да' : tree.clean === false ? 'Нет' : 'НЕ ПРОВЕРЕНО'),
    '', 'ИЗМЕНЁННЫЕ ФАЙЛЫ', linesOfFiles(tree.changedFiles, tree.changedTruncated),
    '', 'PULL REQUEST / CI (ТОЧНЫЙ HEAD)',
    'PR статус: ' + show(pr.status), 'PR номер: ' + show(pr.data && pr.data.number),
    'PR название: ' + show(pr.data && pr.data.title),
    'PR URL: ' + show(pr.data && pr.data.url),
    'PR HEAD SHA: ' + show(pr.data && pr.data.head && pr.data.head.sha),
    'HEAD соответствует опубликованному: ' + show(pr.headMatchesPublished),
    'CI статус: ' + show(ci.status), 'CI head SHA: ' + show(ci.headSha),
    'Обязательный CI проверен: ' + (ci.requiredGateVerified === true ? 'Да' : 'Нет'),
    'Запуски Actions:'];
  for (const run of ci.workflowRuns || []) {
    lines.push('  ' + show(run.name) + ' · ' + show(run.status) + ' / ' + show(run.conclusion) +
      ' · SHA=' + show(run.headSha) + ' · ' + show(run.url));
  }
  if (!(ci.workflowRuns || []).length) lines.push('  Нет подтверждённых запусков');
  lines.push('Check runs:');
  for (const check of ci.checkRuns || []) lines.push('  ' + show(check.name) + ' · ' + show(check.status) + ' / ' + show(check.conclusion));
  if (!(ci.checkRuns || []).length) lines.push('  Нет подтверждённых check runs');
  lines.push('', 'Только просмотр; выбор worktree не выполняет Git switch, commit, push или pull.');
  return lines.join('\n');
}

function render() {
  const viewport = $('main-scroll'), left = viewport.scrollLeft, top = viewport.scrollTop;
  const tbody = $('primary-rows'); tbody.replaceChildren();
  const query = $('repo-filter').value.trim().toLocaleLowerCase();
  const attention = $('only-attention').checked;
  const visible = rows.filter(row =>
    (!query || row.repoId.toLocaleLowerCase().includes(query)) &&
    (!attention || overall(row)[0] !== 'АКТУАЛЕН'));
  if (!visible.some(row => row.repoId === active)) active = visible.length ? visible[0].repoId : null;
  for (const row of visible) {
    const tr = el('tr'); tr.tabIndex = 0; tr.dataset.repo = row.repoId;
    if (row.repoId === active) { tr.classList.add('selected'); tr.setAttribute('aria-selected', 'true'); }
    if (markedRepos.has(row.repoId)) tr.classList.add('marked');
    if (repoChecks.has(row.repoId)) tr.classList.add('checking');
    rowFields(row).forEach((field, idx) => {
      const td = el('td', field, idx === 0 ? 'repo-name' : idx === 3 || idx === 8 || idx === 11 ? tone(field) : '');
      td.title = String(field);
      td.hidden = !columnVisible(idx);
      if (idx === 0) {
        const checkbox = el('input', undefined, 'repo-mark');
        checkbox.type = 'checkbox';
        checkbox.checked = markedRepos.has(row.repoId);
        checkbox.title = 'Отметить для проверки нескольких репозиториев';
        checkbox.setAttribute('aria-label', 'Отметить ' + row.repoId);
        checkbox.onclick = event => {
          event.stopPropagation();
          if (checkbox.checked) markedRepos.add(row.repoId); else markedRepos.delete(row.repoId);
          render();
        };
        checkbox.onkeydown = event => event.stopPropagation();
        td.textContent = '';
        td.append(checkbox);
        td.append(el('span', field));
      }
      tr.append(td);
    });
    tr.onclick = () => { if (active !== row.repoId) { active = row.repoId; render(); } };
    tr.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tr.click(); } };
    tbody.append(tr);
  }
  if (!visible.length) {
    const tr = el('tr'), td = el('td', rows.length ? 'По фильтру ничего не найдено' : 'Репозитории не получены');
    td.colSpan = MAIN_COLUMNS.filter((_, index) => columnVisible(index)).length;
    tr.append(td); tbody.append(tr);
  }
  viewport.scrollLeft = left; viewport.scrollTop = top;
  $('count').textContent = visible.length === rows.length ? rows.length + ' репозиториев' :
    visible.length + ' из ' + rows.length + ' репозиториев';
  const chosen = rows.find(row => row.repoId === active);
  $('selection-title').textContent = chosen ? chosen.repoId : 'Выберите репозиторий';
  $('row-full').textContent = chosen ? rowDetails(chosen) : 'Выберите строку основной таблицы.';
  renderWorktrees(chosen);
  renderPullState();
  updateControls();
}
function renderWorktrees(row) {
  const body = $('worktree-rows'); body.replaceChildren();
  $('worktree-check-status').textContent = !row ? 'Выберите репозиторий' :
    row.worktreeCheckedAt ? 'Подробные PR/CI проверены: ' + row.worktreeCheckedAt :
      'PR/CI не проверены: нажмите «Проверить PR/CI» для точных сведений';
  if (!row) { $('worktree-full').textContent = 'Выберите основной репозиторий.'; return; }
  const tasks = (row.docker && row.docker.worktrees || []).filter(tree => tree.kind === 'worktree');
  let selected = selectedWorktrees.get(row.repoId);
  if (!tasks.some(tree => tree.path === selected)) selected = tasks[0] && tasks[0].path;
  if (selected) selectedWorktrees.set(row.repoId, selected);
  for (const tree of tasks) {
    const pr = tree.pullRequest || {}, ci = tree.ci || {};
    const values = [show(tree.branch), short(tree.head), short(tree.remoteSha),
      worktreeGithub(tree, row), changedLabel(tree.changed), show(pr.status), show(ci.status), show(tree.path)];
    const tr = el('tr'); tr.tabIndex = 0;
    if (tree.path === selected) tr.classList.add('selected');
    for (const [idx, value] of values.entries()) {
      const td = el('td', value, idx === 3 ? tone(value) : ''); td.title = value; tr.append(td);
    }
    tr.onclick = () => { selectedWorktrees.set(row.repoId, tree.path); renderWorktrees(row); };
    tr.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tr.click(); } };
    body.append(tr);
  }
  if (!tasks.length) {
    const tr = el('tr'), td = el('td', 'Linked worktrees для этого репозитория не зарегистрированы.');
    td.colSpan = 8; tr.append(td); body.append(tr);
  }
  const target = tasks.find(tree => tree.path === selected);
  $('worktree-full').textContent = target ? worktreeDetails(row, target) : 'Нет выбранного worktree.';
}
function activateTab(which) {
  detailTab = which;
  $('tab-row').classList.toggle('active', which === 'row');
  $('tab-worktrees').classList.toggle('active', which === 'worktrees');
  $('tab-row').setAttribute('aria-selected', String(which === 'row'));
  $('tab-worktrees').setAttribute('aria-selected', String(which === 'worktrees'));
  $('panel-row').hidden = which !== 'row';
  $('panel-worktrees').hidden = which !== 'worktrees';
}
async function copyDetails() {
  const value = detailTab === 'row' ? $('row-full').textContent : $('worktree-full').textContent;
  if (!value || !active) return;
  try {
    await navigator.clipboard.writeText(value);
    $('banner').textContent = 'Скопирован весь блок «' + (detailTab === 'row' ? 'Строка' : 'Worktrees') + '» для ' + active;
  } catch (error) {
    // No mutation of Git or page data. User can still select all content in pre.
    $('banner').textContent = 'Буфер обмена недоступен: выделите текст нижнего блока и нажмите Ctrl+C.';
  }
}
function selectedVisibleRepos() {
  const query = $('repo-filter').value.trim().toLocaleLowerCase();
  const attention = $('only-attention').checked;
  return rows.filter(row => (!query || row.repoId.toLocaleLowerCase().includes(query)) &&
    (!attention || overall(row)[0] !== 'АКТУАЛЕН')).map(row => row.repoId);
}
function updateControls() {
  const row = rows.find(item => item.repoId === active);
  const job = repoChecks.get(active);
  const checkingClone = Boolean(job && job.started);
  const pending = pendingPulls.has(active);
  const busy = pullBusyRepos.has(active) || readbackBusyRepos.has(active);
  $('local').disabled = localBusy;
  $('remote').disabled = !rows.length;
  $('selected-remote').disabled = !row || checkingClone || busy;
  $('check-worktree-details').disabled = !row || !!job || busy;
  $('marked-remote').disabled = ![...markedRepos].some(name => rows.some(row => row.repoId === name));
  $('mark-visible').disabled = !selectedVisibleRepos().length;
  $('clear-marked').disabled = !markedRepos.size;
  $('marked-count').textContent = 'Отмечено: ' + markedRepos.size;
  $('pull-windows').disabled = !row || checkingClone || busy || pending ||
    !browserPullEnabled || !pullEligible(row);
  $('pull-windows').title = !browserPullEnabled ? 'Локальный Windows Pull не настроен' :
    pending ? 'Сначала проверьте результат Pull этого репозитория' :
      checkingClone ? 'Пока выполняется Windows fetch для этого репозитория' : pullBlockers(row);
  $('cancel').disabled = !repoChecks.size;
}
function replaceRows(items, observedVersions = null) {
  if (!Array.isArray(items)) throw new Error('INVALID_REPOSITORY_LIST');
  // A local snapshot started before a targeted check must not erase the
  // newer verified evidence when the slower GET returns out of order.
  rows = items.map(item => {
    if (!observedVersions || (rowVersions.get(item.repoId) || 0) ===
        (observedVersions.get(item.repoId) || 0)) return item;
    return rows.find(current => current.repoId === item.repoId) || item;
  });
  for (const name of markedRepos) if (!rows.some(row => row.repoId === name)) markedRepos.delete(name);
  if (!rows.some(row => row.repoId === active)) active = rows[0] ? rows[0].repoId : null;
  render();
}
async function loadLocal() {
  if (localBusy) return;
  localBusy = true;
  const observedVersions = new Map(rowVersions);
  updateControls();
  $('banner').textContent = 'Чтение Docker primary и Windows…';
  try {
    const response = await fetch('/api/v1/repositories', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
    replaceRows(data.repositories, observedVersions);
    $('banner').textContent = 'Docker: ' + rows.length + ' репозиториев · ' + data.checkedAt +
      ' · GitHub проверяется отдельно' + ((data.errors || []).length ? ' · Ошибки: ' + data.errors.join(', ') : '');
  } catch (error) {
    $('banner').textContent = 'Не удалось обновить Docker/Windows: ' + error.message + ' · предыдущие результаты сохранены';
  } finally { localBusy = false; updateControls(); }
}
function finishCheckJob(job) {
  if (repoChecks.get(job.name) === job) repoChecks.delete(job.name);
  job.resolve();
  render();
}
function cancelQueuedCheck(name) {
  const job = repoChecks.get(name);
  if (!job || job.started) return false;
  const index = checkQueue.indexOf(job);
  if (index !== -1) checkQueue.splice(index, 1);
  job.controller.abort();
  finishCheckJob(job);
  return true;
}
function pumpCheckQueue() {
  while (runningChecks < MAX_CONCURRENT_CHECKS) {
    const pos = checkQueue.findIndex(job => !job.controller.signal.aborted &&
      (!job.details || runningDetailChecks < MAX_CONCURRENT_DETAILS));
    if (pos < 0) break;
    const [job] = checkQueue.splice(pos, 1);
    // Pull of this clone may have begun while the check was waiting.
    if (pullBusyRepos.has(job.name)) {
      finishCheckJob(job);
      continue;
    }
    job.started = true;
    runningChecks++;
    if (job.details) runningDetailChecks++;
    updateControls();
    (async () => {
      try {
        const endpoint = '/api/v1/repositories/' + encodeURIComponent(job.name);
        const signal = job.controller.signal;
        const saved = pendingPulls.get(job.name);
        // Recovery observes the original receipt and snapshot, never Git fetch.
        // Server-side fencing also covers an owner session restart.
        if (saved) {
          try { await reconcilePullResult(saved, {refresh: false}); }
          catch (error) {
            if (error.message === 'PULL_RECEIPT_NOT_OWNED') {
              clearPendingPull(job.name);
              showPullResult('Сессия предыдущего Pull завершилась. Обновляю состояние репозитория; безопасность нового Pull проверяется сервером.', job.name);
            } else showPullResult('Результат Pull недоступен: ' + error.message +
              '. Обновляю состояние репозитория.', job.name);
          }
        }
        if (signal.aborted) return;
        const response = saved ?
          await fetch(endpoint + (job.details ? '?remote=details' : '?remote=true'), {cache:'no-store', signal}) : job.details ?
          await fetch(endpoint + '?remote=details', {cache: 'no-store', signal}) :
          await fetch(endpoint + '/check-windows', {method:'POST', cache:'no-store', signal,
            headers:{'Content-Type':'application/json', 'X-CSRF-Token': await job.csrf}, body:'{}'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
        if (signal.aborted) return;
        if (!ghChecked(data.repository)) job.batch.githubUnverified++;
        if (!job.details && data.repository.windows && data.repository.windows.fetchError)
          job.batch.windowsFetchErrors++;
        const at = rows.findIndex(row => row.repoId === job.name);
        if (at >= 0) {
          data.repository.remoteCheckedAt = data.checkedAt;
          data.repository.worktreeCheckedAt = job.details ? data.checkedAt : null;
          data.repository.remoteCheckError = null;
          rows[at] = data.repository;
          rowVersions.set(job.name, (rowVersions.get(job.name) || 0) + 1);
          render();
        }
      } catch (error) {
        if (!job.controller.signal.aborted) {
          job.batch.requestErrors++;
          job.batch.githubUnverified++;
          const row = rows.find(item => item.repoId === job.name);
          if (row) {
            row.remoteCheckError = 'Проверка GitHub / Windows fetch не выполнена: ' + error.message;
            render();
          }
        }
      } finally {
        runningChecks--;
        if (job.details) runningDetailChecks--;
        if (!job.controller.signal.aborted) {
          job.batch.completed++;
          $('banner').textContent = 'Проверено ' + job.batch.completed + ' из ' + job.batch.total +
            ' · GitHub не подтверждён: ' + job.batch.githubUnverified +
            ', ошибок Windows fetch: ' + job.batch.windowsFetchErrors +
            ', ошибок запросов: ' + job.batch.requestErrors + ' · ' + job.name +
            ' · ' + ((Date.now() - job.batch.started) / 1000).toFixed(1) + ' с';
        }
        finishCheckJob(job);
        pumpCheckQueue();
      }
    })();
  }
}
function cancelChecks() {
  for (const job of [...repoChecks.values()]) {
    if (job.started) job.controller.abort();
    else cancelQueuedCheck(job.name);
  }
  $('banner').textContent = 'Отмена проверок запрошена. Pull других репозиториев не затронут.';
  updateControls();
}
async function checkRemote(mode = 'one', details = false) {
  const names = mode === 'all' ? rows.map(row => row.repoId) :
    mode === 'marked' ? [...markedRepos] : (active ? [active] : []);
  if (!names.length || (mode === 'all' && details)) return;
  const batch = {started:Date.now(),total:0,completed:0,skipped:0,promoted:false,
    githubUnverified:0,windowsFetchErrors:0,requestErrors:0};
  // GET /csrf is shared by one batch and never authorizes a write.
  const tasks = [], newJobs = [];
  for (const name of new Set(names)) {
    if (!rows.some(row => row.repoId === name)) continue;
    const existing = repoChecks.get(name);
    if (existing) {
      // One selected row can be promoted ahead of a slow bulk queue.
      if (mode === 'one' && !existing.started) {
        const pos = checkQueue.indexOf(existing);
        if (pos >= 0) {
          checkQueue.splice(pos,1);checkQueue.unshift(existing);
          batch.promoted = true;
        }
      }
      batch.skipped++;
      continue;
    }
    if (pullBusyRepos.has(name) || readbackBusyRepos.has(name)) {batch.skipped++;continue;}
    let resolve;
    const done = new Promise(end => {resolve = end;});
    const job = {name,details,csrf:null,batch,controller:new AbortController(),
      started:false,resolve};
    newJobs.push(job);
    repoChecks.set(name,job);
    checkQueue.push(job);
    tasks.push(done);
    batch.total++;
  }
  if (!tasks.length) {
    $('banner').textContent = batch.promoted ?
      'Выбранный репозиторий перемещён в начало очереди проверок.' :
      'Новые проверки не назначены: строки уже проверяются или имеют незавершённый Pull.';
    pumpCheckQueue();
    updateControls();
    return;
  }
  if (!details) {
    try {
      const csrf = jsonResponse(await fetch('/api/v1/csrf', {cache:'no-store'}))
        .then(data => data.csrf);
      for (const job of newJobs) job.csrf = csrf;
    } catch (error) {
      for (const job of newJobs) cancelQueuedCheck(job.name);
      $('banner').textContent = 'Не удалось начать проверку (CSRF): ' + error.message;
      return;
    }
  }
  $('banner').textContent = 'Проверяю ' + (details ? 'PR/CI' : 'GitHub main + Windows fetch') +
    ': 0 из ' + batch.total + ' (очередь общая, максимум 4 запроса)';
  render();
  pumpCheckQueue();
  await Promise.all(tasks);
  $('banner').textContent = 'Проверка завершена: ' + batch.completed + ' из ' + batch.total +
    ' · GitHub не подтверждён: ' + batch.githubUnverified +
    ', ошибок Windows fetch: ' + batch.windowsFetchErrors +
    ', ошибок запросов: ' + batch.requestErrors +
    (batch.skipped ? ', пропущено занятых: ' + batch.skipped : '') +
    ', время ' + ((Date.now()-batch.started)/1000).toFixed(1) + ' с.';
  updateControls();
}
function showPullResult(message, repo = active) {
  if (repo) pullMessages.set(repo, message);
  if (repo === active) renderPullState();
}
async function jsonResponse(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
  return data;
}
// Read-only reconciliation of the original operation. Never dispatch Git again.
const READBACK_LABELS = {
  GIT_READ_FAILED: 'Не удалось прочитать Git', HEAD_MISMATCH: 'SHA ещё не совпал',
  BRANCH_MISMATCH: 'Не та ветка Windows', WORKTREE_NOT_CLEAN: 'Git видит локальные изменения',
  ORIGIN_MISMATCH: 'Origin не подтверждён'
};
const FETCH_CAUSES = {
  GIT_AUTH_REQUIRED: 'Git требует авторизацию', GIT_TLS_FAILED: 'Ошибка сертификата Git',
  GIT_NETWORK_FAILED: 'Git не смог подключиться к серверу', GIT_LOCKED: 'Git заблокирован другим процессом',
  GIT_REMOTE_UNAVAILABLE: 'Git remote недоступен', GIT_LOCAL_ACCESS_DENIED: 'Git не может прочитать или записать локальные файлы',
  GIT_FETCH_FAILURE_UNCLASSIFIED: 'Причина fetch не распознана',
  FETCH_REASON_NOT_RECORDED: 'Старый worker не сохранил причину fetch'
};
function receiptMessage(receipt) {
  const issues = Array.isArray(receipt.readbackIssues) ? receipt.readbackIssues : [];
  const detail = issues.map(key => READBACK_LABELS[key] || key).join('; ');
  const observed = receipt.observation;
  const diagnostic = receipt.diagnostic;
  const facts = observed ? '. Windows SHA: ' + show(observed.head) +
    '; ожидался: ' + show(receipt.expectedHead) + '; цель: ' + show(receipt.targetSha) +
    '; проверено: ' + show(observed.checkedAt) +
    (observed.error ? '; ' + observed.error : '') : '';
  const cause = diagnostic ? '. ' + (FETCH_CAUSES[diagnostic.cause] || diagnostic.cause) +
    (diagnostic.exitCode === null ? '' : ' (exit ' + diagnostic.exitCode + ')') : '';
  return (receipt.state === 'FAILED' ? 'Pull завершился ошибкой до merge' : 'Результат Pull пока не определён') +
    ' (' + show(receipt.state) + ', ' + show(receipt.error) + ')' +
    (receipt.phase ? '. Этап: ' + receipt.phase : '') + cause + facts +
    (detail ? '. Проверка: ' + detail : '') + '. Request ID: ' + show(receipt.requestId) +
    '. «Проверить выбранный» обновляет состояние; Pull автоматически не повторяется.';
}
async function readPullReceipt(repo, requestId) {
  return jsonResponse(await fetch('/api/v1/repositories/' + encodeURIComponent(repo) +
    '/pull-results/' + encodeURIComponent(requestId), {cache: 'no-store'}));
}
async function refreshRepository(repo) {
  const version = rowVersions.get(repo) || 0;
  const result = await jsonResponse(await fetch('/api/v1/repositories/' +
    encodeURIComponent(repo) + '?remote=true', {cache: 'no-store'}));
  const at = rows.findIndex(row => row.repoId === repo);
  if (at >= 0 && version === (rowVersions.get(repo) || 0)) {
    result.repository.remoteCheckedAt = result.checkedAt;
    result.repository.remoteCheckError = null;
    rows[at] = result.repository;
    rowVersions.set(repo, version + 1);
    render();
  }
}
// Timings come from the local server's monotonic stage clocks, not Git output.
// Treat them as diagnostics; never use them to approve or repeat a Git effect.
const PULL_TIMING_LABELS = [
  ['previewTotal', 'Preview сервер'], ['previewDocker', 'Preview Docker'],
  ['previewWindows', 'Preview Windows'], ['previewGateway', 'Preview GitHub'],
  ['dockerInventory', 'Execute Docker'], ['windowsPreflight', 'Windows до fetch'],
  ['gatewayMain', 'Execute GitHub main'], ['windowsFetch', 'Windows fetch'],
  ['fetchHeadRead', 'Проверка FETCH_HEAD'], ['windowsAfterFetch', 'Windows после fetch'],
  ['gitAncestry', 'Проверка fast-forward'], ['fastForward', 'Git merge --ff-only'],
  ['windowsReadback', 'Итоговая проверка Windows'], ['executeTotal', 'Execute сервер']
];
function pullTimingSummary(previewTimings, receiptTimings, elapsed) {
  const values = {...(previewTimings || {}), ...(receiptTimings || {})};
  const parts = [];
  for (const [name, label] of PULL_TIMING_LABELS) {
    const ms = values[name];
    if (typeof ms === 'number' && Number.isFinite(ms) && ms >= 0 && ms <= 600000)
      parts.push(label + ': ' + ms + ' мс');
  }
  if (typeof elapsed === 'number' && Number.isFinite(elapsed) && elapsed >= 0)
    parts.unshift('От нажатия до подтверждения: ' + elapsed + ' мс');
  return parts.length ? '\nВремя Pull (этапы перекрываются с общим): ' + parts.join(' · ') : '';
}
async function reconcilePullResult(saved, {previewTimings = null, startedAt = null, refresh = true} = {}) {
  const receipt = await readPullReceipt(saved.repo, saved.requestId);
  const observed = receipt.observation;
  const included = receipt.evidence === 'TARGET_INCLUDED_READBACK' && receipt.targetIncluded === true &&
    receipt.requestId === saved.requestId && receipt.targetSha === saved.targetSha && isSha(receipt.head) &&
    observed?.head === receipt.head && observed.branch === 'main' && observed.clean === true &&
    observed.originValid === true && !observed.error;
  if (receipt.state === 'EXECUTED' && (receipt.head === saved.targetSha || included)) {
    clearPendingPull(saved.repo);
    const confirmed = included ? 'Прежняя цель Pull содержится в текущей истории: ' + saved.repo +
      '. Цель: ' + saved.targetSha + '; текущий Windows SHA: ' + receipt.head +
      '. Подтверждено проверкой истории; исходный Git Pull не повторялся. Request ID: ' + saved.requestId + '.' : (receipt.evidence === 'TARGET_READBACK' ? 'Windows находится на целевом SHA: ' : 'Windows Pull подтверждён: ') + saved.repo + ' → ' + saved.targetSha +
      '. Receipt EXECUTED; Request ID: ' + saved.requestId + '.' +
      pullTimingSummary(previewTimings, receipt.timingsMs,
        startedAt === null ? null : Date.now() - startedAt);
    showPullResult(confirmed, saved.repo);
    // The receipt has already proven success. A fresh, read-only table refresh
    // may take seconds through Gateway; it must not keep Pull "in progress".
    if (refresh) refreshRepository(saved.repo).catch(() => {
      showPullResult(confirmed + ' Обновление таблицы не удалось; нажмите «Проверить выбранный».', saved.repo);
    });
    return true;
  }
  if (receipt.state === 'FAILED') clearPendingPull(saved.repo);
  showPullResult(receiptMessage(receipt), saved.repo);
  if (refresh) refreshRepository(saved.repo).catch(error => {
    showPullResult(receiptMessage(receipt) + ' Обновление таблицы недоступно: ' + error.message, saved.repo);
  });
  return false;
}
async function pullWindows() {
  const repo = active;
  const chosen = rows.find(row => row.repoId === repo);
  if (!browserPullEnabled || !repo || !pullEligible(chosen) ||
      pullBusyRepos.has(repo) || readbackBusyRepos.has(repo) || pendingPulls.has(repo) ||
      repoChecks.get(repo)?.started) return;
  // A bulk check queued for the same clone can be cancelled in favor of the
  // owner's explicit Pull. A *running* check cannot be interrupted safely.
  cancelQueuedCheck(repo);
  if (repoChecks.has(repo)) return;
  const startedAt = Date.now();
  let previewTimings = null, requestId = null;
  pullBusyRepos.add(repo);
  render();
  showPullResult('Проверка Windows main и GitHub main для ' + repo + '…', repo);
  try {
    const csrf = await jsonResponse(await fetch('/api/v1/csrf', {cache: 'no-store'}));
    const headers = {'Content-Type': 'application/json', 'X-CSRF-Token': csrf.csrf};
    const prefix = '/api/v1/repositories/' + encodeURIComponent(repo);
    const preview = await jsonResponse(await fetch(prefix + '/pull-preview', {
      method: 'POST', headers, body: '{}', cache: 'no-store'
    }));
    previewTimings = preview.timingsMs || null;
    if (!preview.available) {
      showPullResult('Pull Windows main недоступен для ' + repo + ': ' +
        (preview.reasons || []).map(key => PULL_BLOCKER_TEXT[key] || key).join('; ') +
        '. Проверка Preview: Docker ' + show(preview.dockerHead) + '; GitHub ' + show(preview.targetSha) +
        '; Windows ' + show(preview.windowsHead) + '. Git не изменён.', repo);
      refreshRepository(repo).catch(() => {});
      return;
    }
    requestId = preview.requestId;
    rememberPendingPull(repo, requestId, preview.targetSha);
    showPullResult('Выполняется проверенный fetch + fast-forward для ' + repo +
      ' до ' + preview.targetSha + '. Не повторяйте Pull до результата.', repo);
    const receipt = await jsonResponse(await fetch(prefix + '/pull-execute', {
      method: 'POST', headers,
      body: JSON.stringify({previewId: preview.previewId, confirm: 'PULL_WINDOWS_MAIN'}),
      cache: 'no-store'
    }));
    if (receipt.state === 'EXECUTED' && receipt.head === preview.targetSha) {
      await reconcilePullResult({repo, requestId, targetSha: preview.targetSha},
        {previewTimings, startedAt});
    } else {
      showPullResult(receiptMessage(receipt), repo);
      try { await reconcilePullResult({repo, requestId, targetSha: preview.targetSha},
        {previewTimings, startedAt}); }
      catch (error) {
        showPullResult(receiptMessage(receipt) + ' Проверка receipt недоступна: ' + error.message, repo);
      }
    }
  } catch (error) {
    let observed = '';
    const pending = pendingPulls.get(repo);
    if (requestId && pending) {
      try {
        const result = await readPullReceipt(repo, requestId);
        if (result.state === 'EXECUTED' && result.head === pending.targetSha) {
          await reconcilePullResult(pending, {previewTimings, startedAt});
          return;
        }
        observed = '. ' + receiptMessage(result);
      } catch (ignored) { observed = '. Receipt пока недоступен'; }
    }
    showPullResult('Pull Windows для ' + repo + ': ' + error.message + observed +
      (requestId ? '. Нажмите «Проверить выбранный»; повторный Pull не запускайте. Request ID: ' + requestId :
        '. Git не запускался через этот запрос.'), repo);
  } finally {
    pullBusyRepos.delete(repo);
    render();
  }
}
$('tab-row').onclick = () => activateTab('row');
$('tab-worktrees').onclick = () => activateTab('worktrees');
$('copy-details').onclick = copyDetails;
$('toggle-docker').onclick = () => {layout.docker = !layout.docker; applyLayout(); saveLayout();};
$('toggle-windows').onclick = () => {layout.windows = !layout.windows; applyLayout(); saveLayout();};
$('reset-columns').onclick = () => {layout.widths = [...DEFAULT_WIDTHS]; applyLayout(); saveLayout();};
$('repo-filter').oninput = render;
$('only-attention').onchange = render;
$('local').onclick = loadLocal;
$('remote').onclick = () => checkRemote('all');
$('marked-remote').onclick = () => checkRemote('marked');
$('selected-remote').onclick = () => checkRemote('one');
$('check-worktree-details').onclick = () => checkRemote('one', true);
$('mark-visible').onclick = () => { for (const name of selectedVisibleRepos()) markedRepos.add(name); render(); };
$('clear-marked').onclick = () => { markedRepos.clear(); render(); };
$('cancel').onclick = cancelChecks;
$('pull-windows').onclick = pullWindows;
async function initialize() {
  $('copy-details').disabled = true;
  $('selected-remote').disabled = true;
  $('check-worktree-details').disabled = true;
  $('pull-windows').disabled = true;
  activateTab('row');
  setupColumns();
  restorePendingPull();
  // Pending receipts appear when their own row becomes active.
  try {
    const response = await fetch('/api/v1/capabilities', {cache: 'no-store'});
    const caps = await response.json();
    if (!response.ok || !caps.browserPull || caps.browserPull.mode !== 'local_owner_confirm')
      throw new Error('BROWSER_CAPABILITY_NOT_VERIFIED');
    browserPullEnabled = caps.browserPull.configured === true;
  } catch (error) {
    $('banner').textContent = 'API read-only capability не подтверждена: ' + error.message;
    return;
  }
  await loadLocal();
}
initialize();
