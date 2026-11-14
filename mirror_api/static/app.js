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
let rows = [], active = null, detailTab = 'row', checkController = null;
let pullBusy = false, browserPullEnabled = false;
let lastPullReceipt = null;
const LAYOUT_KEY = 'fortis.mirror.columns.v1';
const PENDING_PULL_KEY = 'fortis.mirror.pendingPull.v1';
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
function restorePendingPull() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(PENDING_PULL_KEY));
    if (saved && isRepoId(saved.repo) && isRequestId(saved.requestId) && isSha(saved.targetSha))
      lastPullReceipt = saved;
  } catch (ignored) { /* New session: no automatic Git effects. */ }
  $('receipt-controls').hidden = !lastPullReceipt;
}
function rememberPendingPull(repo, requestId, targetSha) {
  lastPullReceipt = {repo, requestId, targetSha};
  try { sessionStorage.setItem(PENDING_PULL_KEY, JSON.stringify(lastPullReceipt)); }
  catch (ignored) { /* In-page reconciliation is still possible. */ }
  $('receipt-controls').hidden = false;
}
function clearPendingPull() {
  lastPullReceipt = null;
  try { sessionStorage.removeItem(PENDING_PULL_KEY); } catch (ignored) { /* Session ended. */ }
  $('receipt-controls').hidden = true;
}
function pullEligible(row) {
  return !!(row && row.actions && row.actions.pull_main_browser &&
    row.actions.pull_main_browser.available === true && !row.remoteCheckError);
}


const PULL_BLOCKER_TEXT = {
  DOCKER_MAIN_NOT_VERIFIED: 'Docker primary main не подтверждён как чистый и зарегистрированный',
  DOCKER_MAIN_NOT_AT_GITHUB_MAIN: 'Docker main не совпадает с GitHub main; сначала обновите Docker main штатным Gateway workflow',
  GITHUB_MAIN_UNVERIFIED: 'GitHub main не подтверждён Gateway',
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
  if (!row || !row.actions || !row.actions.pull_main_browser) return 'Сначала выполните проверку выбранного репозитория';
  const action = row.actions.pull_main_browser;
  if (action.available === true) return 'Предварительные условия выполнены; окончательное разрешение только после свежего Preview';
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
    'Ручной Windows Pull (оценка): ' + (row.actions && row.actions.pull_main_browser &&
      row.actions.pull_main_browser.available === true ? 'можно запросить свежий Preview' :
      'Preview / проверки требуются') +
      ' · окончательное решение принимает только свежий серверный Preview',
    'Почему Windows Pull недоступен: ' + pullBlockers(row),
    'Windows fetch: ' + show(w.fetchStatus) +
      (w.fetchError ? ' · ' + w.fetchError : ''),
    'Время чтения (мс): Docker ' + show(row.readTimingsMs && row.readTimingsMs.dockerInventory) +
      ', Windows Git ' + show(row.readTimingsMs && row.readTimingsMs.windowsGit) +
      ', Windows fetch ' + show(row.readTimingsMs && row.readTimingsMs.windowsFetch) +
      ', Gateway ' + show(row.readTimingsMs && row.readTimingsMs.githubGateway) +
      ', Git-история ' + show(row.readTimingsMs && row.readTimingsMs.gitAncestry),
    'Gateway этапы (мс): ' + (gh.gatewayTimingsMs ? JSON.stringify(gh.gatewayTimingsMs) : 'НЕ ПРОВЕРЕНЫ'),
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
    rowFields(row).forEach((field, idx) => {
      const td = el('td', field, idx === 0 ? 'repo-name' : idx === 3 || idx === 8 || idx === 11 ? tone(field) : '');
      td.title = String(field);
      td.hidden = !columnVisible(idx);
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
  $('copy-details').disabled = !chosen;
  $('selected-remote').disabled = !chosen || !!checkController || pullBusy;
  $('check-worktree-details').disabled = !chosen || !!checkController || pullBusy;
  $('pull-windows').disabled = !chosen || !!checkController || pullBusy ||
    !browserPullEnabled || !!lastPullReceipt || !pullEligible(chosen);
  $('pull-windows').title = !browserPullEnabled ? 'Локальный Windows Pull не настроен' :
    lastPullReceipt ? 'Сначала проверьте результат предыдущего Pull' : pullBlockers(chosen);
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
function setBusy(busy) {
  $('local').disabled = busy;
  $('remote').disabled = busy;
  $('selected-remote').disabled = busy || !active;
  $('check-worktree-details').disabled = busy || !active;
  const current = rows.find(row => row.repoId === active);
  $('pull-windows').disabled = busy || !active || !browserPullEnabled ||
    !!lastPullReceipt || !pullEligible(current);
  $('check-pull-result').disabled = busy || !lastPullReceipt;
  $('cancel').disabled = !checkController;
}
function replaceRows(items) {
  if (!Array.isArray(items)) throw new Error('INVALID_REPOSITORY_LIST');
  rows = items;
  if (!rows.some(row => row.repoId === active)) active = rows[0] ? rows[0].repoId : null;
  render();
}
async function loadLocal() {
  if (checkController || pullBusy) return;
  $('banner').textContent = 'Чтение Docker primary и Windows…';
  $('local').disabled = $('remote').disabled = $('selected-remote').disabled = true;
  try {
    const response = await fetch('/api/v1/repositories', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
    replaceRows(data.repositories);
    $('banner').textContent = 'Docker: ' + rows.length + ' репозиториев · ' + data.checkedAt +
      ' · GitHub проверяется отдельно' + ((data.errors || []).length ? ' · Ошибки: ' + data.errors.join(', ') : '');
  } catch (error) {
    $('banner').textContent = 'Не удалось обновить Docker/Windows: ' + error.message + ' · предыдущие результаты сохранены';
  } finally { setBusy(false); }
}
async function checkRemote(all, details = false) {
  if (checkController || pullBusy || !rows.length || (!all && !active) || (all && details)) return;
  const names = all ? rows.map(row => row.repoId) : [active];
  checkController = new AbortController();
  const signal = checkController.signal;
  setBusy(true);
  let index = 0, completed = 0, githubUnverified = 0, windowsFetchErrors = 0, requestErrors = 0;
  const checkSummary = () => 'GitHub не подтверждён: ' + githubUnverified +
    ', ошибок Windows fetch: ' + windowsFetchErrors + ', ошибок запросов: ' + requestErrors;
  const started = Date.now();
  $('banner').textContent = 'Проверяю ' + (details ? 'PR/CI' : 'GitHub main + Windows fetch') + ': 0 из ' + names.length;
  const worker = async (csrf) => {
    while (index < names.length && !signal.aborted) {
      const name = names[index++];
      try {
        const endpoint = '/api/v1/repositories/' + encodeURIComponent(name);
        const response = details ?
          await fetch(endpoint + '?remote=details', {cache: 'no-store', signal}) :
          await fetch(endpoint + '/check-windows', {
            method: 'POST', cache: 'no-store', signal,
            headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: '{}'
          });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
        const at = rows.findIndex(row => row.repoId === name);
        if (!ghChecked(data.repository)) githubUnverified++;
        if (!details && data.repository.windows && data.repository.windows.fetchError) windowsFetchErrors++;
        if (at >= 0) {
          data.repository.remoteCheckedAt = data.checkedAt;
          data.repository.worktreeCheckedAt = details ? data.checkedAt : null;
          data.repository.remoteCheckError = null;
          rows[at] = data.repository;
          render();
        }
      } catch (error) {
        if (!signal.aborted) {
          requestErrors++;
          githubUnverified++;
          const at = rows.findIndex(row => row.repoId === name);
          if (at >= 0) {
            rows[at].remoteCheckError = 'Проверка GitHub / Windows fetch не выполнена';
            render();
          }
        }
      } finally {
        if (!signal.aborted) {
          completed++;
          $('banner').textContent = 'Проверено ' + completed + ' из ' + names.length +
            ' · ' + checkSummary() + ' · ' + name +
            ' · ' + ((Date.now() - started) / 1000).toFixed(1) + ' с';
        }
      }
    }
  };
  try {
    // Owner check may write Windows Git metadata, so use a CSRF-gated POST.
    // Keep the ordinary GET inventory and detailed PR/CI GET strictly read-only.
    const csrf = details ? null : (await jsonResponse(await fetch('/api/v1/csrf',
      {cache: 'no-store', signal}))).csrf;
    await Promise.all(Array.from({length: Math.min(details ? 1 : 4, names.length)},
      () => worker(csrf)));
    $('banner').textContent = signal.aborted ?
      'Проверка отменена; обработанные строки и сведения сохранены.' :
      'Проверка завершена: ' + completed + ' из ' + names.length + ' · ' + checkSummary() +
        ', время ' + ((Date.now() - started) / 1000).toFixed(1) + ' с. Отдельные ошибки в нижнем блоке.';
  } catch (error) {
    if (!signal.aborted) {
      for (const name of names) {
        const row = rows.find(item => item.repoId === name);
        if (row) row.remoteCheckError = 'Проверка не запущена: ' + error.message;
      }
      render();
      $('banner').textContent = 'Не удалось начать проверку: ' + error.message;
    }
  } finally { checkController = null; setBusy(false); }
}
function showPullResult(message) {
  $('pull-result').textContent = message;
  $('pull-result').hidden = !message;
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
function receiptMessage(receipt) {
  const issues = Array.isArray(receipt.readbackIssues) ? receipt.readbackIssues : [];
  const detail = issues.map(key => READBACK_LABELS[key] || key).join('; ');
  return 'Результат предыдущего Pull пока не подтверждён (' + show(receipt.state) +
    ', ' + show(receipt.error) + ')' + (detail ? '. Проверка: ' + detail : '') +
    '. Request ID: ' + show(receipt.requestId) +
    '. Git не запускается повторно; нажмите «Проверить результат».';
}
async function readPullReceipt(repo, requestId) {
  return jsonResponse(await fetch('/api/v1/repositories/' + encodeURIComponent(repo) +
    '/pull-results/' + encodeURIComponent(requestId), {cache: 'no-store'}));
}
async function refreshRepository(repo) {
  const result = await jsonResponse(await fetch('/api/v1/repositories/' +
    encodeURIComponent(repo) + '?remote=true', {cache: 'no-store'}));
  const at = rows.findIndex(row => row.repoId === repo);
  if (at >= 0) {
    result.repository.remoteCheckedAt = result.checkedAt;
    result.repository.remoteCheckError = null;
    rows[at] = result.repository;
    render();
  }
}
// Timings come from the local server's monotonic stage clocks, not Git output.
// Treat them as diagnostics; never use them to approve or repeat a Git effect.
const PULL_TIMING_LABELS = [
  ['previewTotal', 'Preview сервер'], ['previewDocker', 'Preview Docker'],
  ['previewWindows', 'Preview Windows'], ['previewGateway', 'Preview Gateway'],
  ['dockerInventory', 'Execute Docker'], ['windowsPreflight', 'Windows до fetch'],
  ['gatewayMain', 'Execute Gateway main'], ['windowsFetch', 'Windows fetch'],
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
async function reconcilePullResult(saved, {previewTimings = null, startedAt = null} = {}) {
  const receipt = await readPullReceipt(saved.repo, saved.requestId);
  if (receipt.state === 'EXECUTED' && receipt.head === saved.targetSha) {
    clearPendingPull();
    const confirmed = 'Windows Pull подтверждён: ' + saved.repo + ' → ' + saved.targetSha +
      '. Receipt EXECUTED; Request ID: ' + saved.requestId + '.' +
      pullTimingSummary(previewTimings, receipt.timingsMs,
        startedAt === null ? null : Date.now() - startedAt);
    showPullResult(confirmed);
    // The receipt has already proven success. A fresh, read-only table refresh
    // may take seconds through Gateway; it must not keep Pull "in progress".
    refreshRepository(saved.repo).catch(() => {
      showPullResult(confirmed + ' Обновление таблицы не удалось; нажмите «Проверить выбранный».');
    });
    return true;
  }
  showPullResult(receiptMessage(receipt));
  return false;
}
async function verifyPullResult() {
  if (!lastPullReceipt || pullBusy || checkController) return;
  const saved = {...lastPullReceipt};
  pullBusy = true;
  setBusy(true);
  showPullResult('Читаю сохранённый результат Pull ' + saved.requestId + ' без нового Git…');
  try { await reconcilePullResult(saved); }
  catch (error) {
    showPullResult('Результат Pull пока недоступен: ' + error.message +
      '. Повторите только «Проверить результат» или проверьте Git вручную. Request ID: ' + saved.requestId);
  } finally { pullBusy = false; setBusy(false); render(); }
}
async function pullWindows() {
  if (!browserPullEnabled || !active || checkController || pullBusy || lastPullReceipt) return;
  const repo = active;
  const startedAt = Date.now();
  let previewTimings = null;
  pullBusy = true;
  setBusy(true);
  showPullResult('Проверка Windows main и GitHub main для ' + repo + '…');
  let requestId = null;
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
        (preview.reasons || []).join(', ') + '. Git не изменён.');
      return;
    }
    requestId = preview.requestId;
    rememberPendingPull(repo, requestId, preview.targetSha);
    // The owner's click on Pull Windows is the confirmation. Do not show a
    // second browser modal. Preserve the one-use server-side Preview → Execute
    // protocol and its fresh SHA/origin/clean/fast-forward preconditions.
    showPullResult('Проверки пройдены: ' + repo + ' · Windows ' + preview.windowsHead +
      ' → GitHub ' + preview.targetSha + '. Начинаю guarded fast-forward…');
    showPullResult('Выполняется проверенный fetch + fast-forward для ' + repo +
      ' до ' + preview.targetSha + '. Не повторяйте Pull до результата.');
    const receipt = await jsonResponse(await fetch(prefix + '/pull-execute', {
      method: 'POST', headers,
      body: JSON.stringify({previewId: preview.previewId, confirm: 'PULL_WINDOWS_MAIN'}),
      cache: 'no-store'
    }));
    if (receipt.state === 'EXECUTED' && receipt.head === preview.targetSha) {
      await reconcilePullResult({repo, requestId, targetSha: preview.targetSha},
        {previewTimings, startedAt});
    } else {
      showPullResult(receiptMessage(receipt));
      // One immediate read-only reconciliation avoids the manual F12 workflow.
      // A still-uncertain result retains a visible button for the owner.
      try { await reconcilePullResult({repo, requestId, targetSha: preview.targetSha},
        {previewTimings, startedAt}); }
      catch (error) {
        showPullResult(receiptMessage(receipt) + ' Проверка receipt недоступна: ' + error.message);
      }
    }
  } catch (error) {
    let observed = '';
    if (requestId && lastPullReceipt) {
      try {
        const result = await readPullReceipt(repo, requestId);
        if (result.state === 'EXECUTED' && result.head === lastPullReceipt.targetSha) {
          await reconcilePullResult(lastPullReceipt, {previewTimings, startedAt});
          return;
        }
        observed = '. ' + receiptMessage(result);
      } catch (ignored) { observed = '. Receipt пока недоступен'; }
    }
    showPullResult('Pull Windows для ' + repo + ': ' + error.message + observed +
      (requestId ? '. Нажмите «Проверить результат»; повторный Pull не запускайте. Request ID: ' + requestId :
        '. Git не запускался через этот запрос.'));
  } finally {
    pullBusy = false;
    setBusy(false);
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
$('check-pull-result').onclick = verifyPullResult;
$('local').onclick = loadLocal;
$('remote').onclick = () => checkRemote(true);
$('selected-remote').onclick = () => checkRemote(false);
$('check-worktree-details').onclick = () => checkRemote(false, true);
$('cancel').onclick = () => { if (checkController) checkController.abort(); };
$('pull-windows').onclick = pullWindows;
async function initialize() {
  $('copy-details').disabled = true;
  $('selected-remote').disabled = true;
  $('check-worktree-details').disabled = true;
  $('pull-windows').disabled = true;
  activateTab('row');
  setupColumns();
  restorePendingPull();
  if (lastPullReceipt) showPullResult('Есть неподтверждённый результат Pull для ' +
    lastPullReceipt.repo + ' · Request ID: ' + lastPullReceipt.requestId +
    '. Используйте «Проверить результат», не запускайте новый Pull.');
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
