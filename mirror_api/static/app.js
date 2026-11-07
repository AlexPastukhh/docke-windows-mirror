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
const ghChecked = row => Boolean(row.github && row.github.head && !row.github.error);
const primary = row => row.docker && row.docker.primary || null;
const changedLabel = count => count === null || count === undefined ? 'НЕ ПРОВЕРЕНО' :
  count === 0 ? 'Нет' : 'Есть: ' + count;

function dockerGithub(row) {
  const d = primary(row), gh = row.github || {};
  if (!d) return 'НЕТ PRIMARY';
  if (!ghChecked(row)) return 'НЕ ПРОВЕРЕН';
  const sha = gh.branches && gh.branches[d.branch];
  if (!sha) return gh.truncated && gh.truncated.branches || gh.readErrors && gh.readErrors.branches ?
    'ВЕТКА НЕ ПОДТВЕРЖДЕНА' : 'НЕТ В GITHUB';
  if (sha === d.head) return 'РАВЕН GITHUB';
  // Two different SHAs are NOT evidence of ahead/behind without Git ancestry.
  return 'SHA ≠ GH · НАПРАВЛЕНИЕ НЕИЗВЕСТНО';
}
function windowsGithub(row) {
  const w = row.windows || {};
  if (!w.exists) return w.error === 'WINDOWS_CLONE_NOT_FOUND' ? 'НЕТ КЛОНА' : 'НЕ ПРОВЕРЕН';
  if (w.error || !w.originValid) return 'ОШИБКА ORIGIN / GIT';
  if (!ghChecked(row)) return 'НЕ ПРОВЕРЕН';
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
  if (w.branch !== 'main') return ['WINDOWS: НЕ MAIN', 'Проверить активную ветку Windows'];
  if (w.clean === false) return ['WINDOWS: ИЗМЕНЕНИЯ', 'Разобрать Windows-файлы; Pull запрещён'];
  if (w.clean !== true) return ['WINDOWS: НЕ ПРОВЕРЕН', 'Проверить git status Windows'];
  if (!ghChecked(row)) return ['НЕ ПРОВЕРЕНО', 'Нажать «Проверить выбранный/все»'];
  if (w.relation === 'diverged') return ['WINDOWS: ИСТОРИИ РАЗОШЛИСЬ', 'Не выполнять Pull'];
  if (w.relation === 'ahead') return ['WINDOWS ВПЕРЕДИ', 'Проверить неожиданные Windows-коммиты'];
  if (w.relation === 'behind') return ['WINDOWS ОТСТАЁТ', 'Нужен guarded Windows main Pull'];
  if (dockerGithub(row) !== 'РАВЕН GITHUB') return ['DOCKER ≠ GITHUB', 'Сверить Docker main с GitHub main'];
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
  if (/ОШИБКА|ИЗМЕНЕНИЯ|РАЗОШЛИСЬ|ВПЕРЕДИ|НЕТ PRIMARY/.test(value)) return 'bad';
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
    'Обязательный CI: ' + show(gh.requiredCI),
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
  for (const row of rows) {
    const tr = el('tr'); tr.tabIndex = 0; tr.dataset.repo = row.repoId;
    if (row.repoId === active) { tr.classList.add('selected'); tr.setAttribute('aria-selected', 'true'); }
    rowFields(row).forEach((field, idx) => {
      const td = el('td', field, idx === 0 ? 'repo-name' : idx === 3 || idx === 8 || idx === 11 ? tone(field) : '');
      td.title = String(field); tr.append(td);
    });
    tr.onclick = () => { if (active !== row.repoId) { active = row.repoId; render(); } };
    tr.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); tr.click(); } };
    tbody.append(tr);
  }
  if (!rows.length) {
    const tr = el('tr'), td = el('td', 'Репозитории не получены');
    td.colSpan = MAIN_COLUMNS.length; tr.append(td); tbody.append(tr);
  }
  viewport.scrollLeft = left; viewport.scrollTop = top;
  $('count').textContent = rows.length + ' репозиториев';
  const chosen = rows.find(row => row.repoId === active);
  $('selection-title').textContent = chosen ? chosen.repoId : 'Выберите репозиторий';
  $('row-full').textContent = chosen ? rowDetails(chosen) : 'Выберите строку основной таблицы.';
  renderWorktrees(chosen);
  $('copy-details').disabled = !chosen;
  $('selected-remote').disabled = !chosen || !!checkController;
}
function renderWorktrees(row) {
  const body = $('worktree-rows'); body.replaceChildren();
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
  $('cancel').disabled = !busy;
}
function replaceRows(items) {
  if (!Array.isArray(items)) throw new Error('INVALID_REPOSITORY_LIST');
  rows = items;
  if (!rows.some(row => row.repoId === active)) active = rows[0] ? rows[0].repoId : null;
  render();
}
async function loadLocal() {
  if (checkController) return;
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
async function checkRemote(all) {
  if (checkController || !rows.length || (!all && !active)) return;
  const names = all ? rows.map(row => row.repoId) : [active];
  checkController = new AbortController();
  const signal = checkController.signal;
  setBusy(true);
  let index = 0, completed = 0, errors = 0;
  $('banner').textContent = 'Проверяю GitHub/Gateway: 0 из ' + names.length;
  const worker = async () => {
    while (index < names.length && !signal.aborted) {
      const name = names[index++];
      try {
        const response = await fetch('/api/v1/repositories/' + encodeURIComponent(name) + '?remote=true',
          {cache: 'no-store', signal});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'HTTP ' + response.status);
        const at = rows.findIndex(row => row.repoId === name);
        if (at >= 0) { rows[at] = data.repository; render(); }
      } catch (error) {
        if (!signal.aborted) errors++;
      } finally {
        if (!signal.aborted) {
          completed++;
          $('banner').textContent = 'Проверено ' + completed + ' из ' + names.length +
            ', ошибок запросов ' + errors + ' · ' + name;
        }
      }
    }
  };
  try {
    await Promise.all(Array.from({length: Math.min(4, names.length)}, worker));
    $('banner').textContent = signal.aborted ?
      'Проверка отменена; обработанные строки и сведения сохранены.' :
      'Проверка завершена: ' + completed + ' из ' + names.length + ', ошибок запросов ' + errors +
        '. Отдельные ошибки Gateway смотрите в нижнем блоке.';
  } finally { checkController = null; setBusy(false); }
}
$('tab-row').onclick = () => activateTab('row');
$('tab-worktrees').onclick = () => activateTab('worktrees');
$('copy-details').onclick = copyDetails;
$('local').onclick = loadLocal;
$('remote').onclick = () => checkRemote(true);
$('selected-remote').onclick = () => checkRemote(false);
$('cancel').onclick = () => { if (checkController) checkController.abort(); };
async function initialize() {
  $('copy-details').disabled = true;
  $('selected-remote').disabled = true;
  activateTab('row');
  try {
    const response = await fetch('/api/v1/capabilities', {cache: 'no-store'});
    const caps = await response.json();
    if (!response.ok || caps.readOnly !== true) throw new Error('READONLY_CAPABILITY_NOT_VERIFIED');
  } catch (error) {
    $('banner').textContent = 'API read-only capability не подтверждена: ' + error.message;
    return;
  }
  await loadLocal();
}
initialize();
