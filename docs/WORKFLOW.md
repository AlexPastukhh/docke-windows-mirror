# Mirror Workflow: поведение и безопасность

## Актуальная схема: Docker task → Gateway → GitHub → Windows main

Для обычных проектов Docker primary находится в `/workspace/repo/Repos/<repo>`
и остаётся на `main`. Каждая независимая задача выполняется в **linked Git
worktree** `/workspace/repo/Worktrees/<repo>/<task>`, где ветка относится к задаче.
Windows содержит отдельный **primary clone** `%USERPROFILE%\Repos\<repo>` на
`main`: не переключать его на task-ветку и не создавать там рабочие task worktrees.
`/workspace/repo/Workfiles` предназначен для рабочих/передаточных материалов,
но не заменяет Git-источники.

Git worktree — зарегистрированная Git рабочая директория, а не просто папка.
Для инвентаризации использовать `git worktree list --porcelain -z` от Docker
primary, проверять ветку, HEAD, общий Git directory и реальный путь. Неизвестные
или неканонические записи показывать как требующие проверки; не направлять в них
запись без подтверждения. У каждой задачи свой worktree/branch: не делать второй
checkout той же ветки, не переключать primary ради задачи и не очищать чужие
worktrees автоматически. Взаимосвязанные worktrees сохраняются до проверки
owner deployment records и условий cleanup.

**Доставка коммитов:**

1. Установить точные repo, primary `main`, linked task worktree и Git-состояние.
   Не менять dirty primary чужой задачи с помощью reset/stash. Для native
   Gateway workflow регистрация/adoption task выполняется через typed
   `workspace.create_task` и требует соответствующих preconditions, в том числе
   проверенного base SHA и чистого primary; наличие локального worktree не
   означает, что он уже зарегистрирован в Gateway.
2. Изменить и проверить файлы в task worktree, сделать stage/commit выбранных
   файлов; зафиксировать SHA и доказательства проверок. Для принятого native
   scope использовать typed операции `fortis_approvals.workflow.client.default_client()`;
   локальные Git read-only команды допустимы, но **сетевые GitHub-эффекты
   через прямой `git fetch/push`, credential-helper или GitHub connector запрещены**.
3. Опубликовать именно существующие Git-коммиты через Gateway `publish`
   (`github.push_commits`), с сохранённым requestId и проверенным remote SHA.
   Не считать успешный GitHub read достаточным разрешением на запись. Live
   capabilities и реальный token scope проверяются отдельно для каждого repo;
   при DENY/403/unknown остановиться, а не переходить на альтернативный клиент.
4. Создать PR через typed Gateway, проверить обязательные GitHub Actions/statuses
   **для точного PR HEAD**. `ASK` можно отправлять без отдельного вопроса в
   чате, но исполнение требует подтверждения владельца через Owner Portal
   (WebAuthn). Не выполнять merge по просто последнему зелёному запуску.
   В случае `UNCERTAIN` сначала status/reconcile по прежнему requestId.
5. После подтверждённого merge обновить чистый Docker `main` до одобренного
   SHA через Gateway `workspace.update_main`. Затем выполнить охраняемый
   Windows `main` pull: проверить точный origin, HEAD, ветку, чистоту,
   fast-forward ancestry, pin SHA и readback; никаких reset/stash/force.
   Изменение исходников **не** означает сборку image, deploy или перезапуск
   работающих сервисов: это отдельный owner-controlled процесс.

**Нормативные владельцы правил:** актуальный реестр контейнера из
`/workspace/repo/README.md` (UC-CONT-002/003/005/018) задаёт общий маршрут;
[native Gateway contract][gateway-native] — операции,
политики и границы доступа. Текущее API-first и Browser UI, его запуск,
ограничения и тесты описаны в [API-FIRST](API-FIRST.md), без дублирования
их интерфейсного контракта здесь.

**Состояние реализации (2026-11-07):** Browser UI/API объединены в
`main` после PR #1; новые изменения готовятся в
`task/mirror-gateway-ci-20261107`. Это всё ещё **source candidate**,
не установленная Windows-версия. SHA веток и PR/CI теперь читаются через
Gateway (при отсутствии scope — unknown), а Browser UI не содержит доступных
Git write routes. Legacy Docker Git helper не выполняет сетевые операции
к GitHub. Обязательный CI и разрешение merge остаются у Gateway;
Windows listener/ASK Pull всё ещё MANUAL-PENDING. Следующая проверка —
GitHub Actions workflow, затем ручной запуск на реальной Windows.

## Предыдущий WinForms Mirror: описание существующего кода, не инструкция для Gateway-only публикации

Ниже сохранено описание WinForms-реализации и её проверок, чтобы можно было
сопоставлять поведение при миграции. Оно не переопределяет актуальный маршрут
выше. В частности, прежние `Push Docker`, прямые GitHub-запросы и переключение
активной ветки — **исторические механизмы**, а не разрешённые способы
опубликовать новую task-ветку при действующем Gateway-only режиме.

Standalone `Fortis-Windows-Mirror-Status.ps1` GUI loads
`Mirror-Workflow.UI.cs`. Original main-only `Mirror-Status.UI.cs` retained as
historical reference; `-Cli` still runs `Mirror-Status.Report.ps1`. This GUI is
PowerShell 5.1 / WinForms, not the new Browser UI. It reads the Docker-primary
`tools/Export-Docker-Branch-Workflow.py` snapshot; its source behavior is
not evidence of live Windows deployment or Gateway authorization.

**Source of truth and screen:** the table enumerates Git repositories under
Docker `/workspace/repo/Repos/` (not Windows folders), one row per repository.
If Docker is unavailable, it shows an error instead of a misleading empty list.
Windows clone is expected under `%USERPROFILE%\Repos\<docker-folder-name>`;
missing clones remain visible as `НЕТ КЛОНА WINDOWS`. Windows-only folders are
not added. The UI shows a compact toolbar on top, a full-width ListView with
13 columns separated by `Docker · ...`, `Windows · ...`, and `Workflow · ...`,
then a multiline scrollable full-detail panel **below** the table. Every table
column is represented as an untruncated detail line in the identical order.
Both `Другие ветки Docker` and `Другие ветки Windows` show the same 13 full
fields for each other branch after the selected branch's primary details.

**Two independent branch selectors per environment:** `Ветка для просмотра`
changes only the branch whose SHA/status is displayed; **no Git checkout**.
The Docker view initially shows Docker's active branch. The Windows view is
**not inferred from Docker**: until Windows is checked it is unknown; on Check
it defaults to the actual Windows active branch (often `main`). Only a view
branch explicitly selected by the user persists across further checks. In the
details pane, a dedicated read-only `MAIN` block always shows known GitHub,
Docker-local and Windows-local `main` SHAs separately from the active Docker
branch. Dirty Docker feature branches must never be presented as a reason to
push their changes into GitHub `main`. When clean Windows `main` is verified
behind GitHub `main`, the workflow offers `Pull Windows main` even if Docker
is editing an unrelated feature branch; the guarded Windows fast-forward rules
are unchanged.

`Активная ветка` always reports actual `symbolic-ref HEAD` from that environment;
clicking it offers local existing branches for an explicit Git switch (with
confirmation and fresh preflight). Switching does **not** change the branch
selected for viewing. The overall workflow state is always derived from the
actual active Docker and Windows branches, never from their view selectors.
All-`main` clean equality is green `АКТУАЛЕН`; equality on a non-main branch is
`АКТУАЛЕН · НЕ MAIN` (warning rather than error). It also distinguishes
Docker unstaged/uncommitted files, Docker commits ahead of GitHub, Windows
behind, unexpected Windows commits ahead (error), divergent history, missing
Windows clones and unknown/unverified state. A different SHA alone is **not**
called ahead or behind. On user-triggered Check, GitHub branches are obtained
via verified `ls-remote --heads`; Docker ancestry is checked in its Git object
database with exact GitHub SHA and Windows ancestry in the Windows clone. If
required Git objects are missing, direction remains `НЕИЗВЕСТНО` instead of
guessed. Windows may fetch **Git metadata only** for its active branch to get
exact counts, without changing worktree files or its checked-out branch.

**Operating behavior:** opening the window reads the Docker inventory only;
remote GitHub/Windows checks require `Проверить выбранные` or `Проверить все`.
Checks execute off the UI thread and can be cancelled. Status and row
contents refresh **after each repository completes** (GitHub, Windows and the
Docker ancestry check), showing `Проверяется N из M` / `Проверено N из M`
during the run instead of waiting for the final repository. On cancellation,
already completed rows remain visible; unprocessed rows retain their previous
state. Choosing the already-active branch in either `Активная ветка` selector
is a no-op with no Git call. The branch-menu cleanup is deferred until after
its WinForms close/click handlers return. Docker read-only export never
fetches, pushes or changes a worktree. The owner-only actions are:

- `Stage Docker` opens a checklist of current **active Docker** working-tree
  changes (nothing selected by default). Only checked Git-reported paths are
  staged with literal Git pathspecs. The preview includes an SHA-256 digest of
  the complete NUL-delimited porcelain status; Stage rechecks active branch,
  HEAD and that exact digest before writing. No blanket `git add -A` and no
  Windows Git write. For safety, more than 500 changed entries require direct
  owner-controlled Docker Git instead of using the GUI.
- `Commit Docker` previews **all currently staged/indexed files**, asks for an
  explicit message (UTF-8; may contain newlines), and requires confirmation.
  The helper checks the SHA-256 of the raw staged index diff plus active
  Docker branch and HEAD before running normal `git commit` with hooks and
  signing disabled. A Commit does not stage remaining unstaged files. Changes
  appearing between preview and commit invalidate the digest.
- `Push Docker` previews Docker HEAD and the exact current GitHub branch SHA
  via `ls-remote`, then requests confirmation. It rechecks active branch,
  HEAD and GitHub SHA; pushes **only** `HEAD:refs/heads/<active-branch>`
  using standard non-forcing `git push`. No force, automatic Windows Pull,
  branch switch, tagging or service restart. Uncommitted working-tree files
  are never included in a Push. A not-yet-published branch requires its own
  confirmation as part of the same Push dialog. Authentication errors stop
  the operation rather than prompting through the UI.
- These three buttons call `tools/Docker-Mirror-Git-Actions.py` **inside**
  `fortis-repo-test` as uid 1000; the Windows process is a UI/controller only.
  The operation target is always the **actual active Docker branch**, not
  the branch selected for viewing. No repository is blocked solely because
  of its name: `rd-graphs`, `tunnel`, `security` and other projects
  follow the same verified Git flow. Git writes still require the owner's
  explicit selection, previews and confirmations; no service is restarted.
- `Pull Windows` only for one verified clean Windows clone, the currently
  active **and viewed** Windows branch, approved Docker/Windows origin, and
  confirmed `WindowsAhead == 0`, `WindowsBehind > 0`. It confirms the exact
  displayed GitHub SHA, repeats GitHub/Windows preflight, fetches the selected
  branch, checks `FETCH_HEAD` still equals that SHA, checks HEAD/branch/clean
  worktree again, and performs **`git merge --ff-only FETCH_HEAD`**. The button
  is called Pull but uses exact-SHA fetch + fast-forward merge to prevent
  accidentally importing a newer unreviewed GitHub commit. No forced reset,
  stash, force-push, auto-restart or deploy. Configured Git file filters may
  still run on Git writes.
- Windows branch switch requires clean tree and an existing **local** target
  branch, approved matching remote, user confirmation, and a fresh branch,
  HEAD, dirty-status and target SHA check; command is `git switch --no-guess`.
- Docker branch switch is **explicit owner action**, never automatic, and
  requires a clean Docker Git tree, existing local target branch, owner
  confirmation and reverified active branch/HEAD via exporter `--switch-*`.
  There is no name-based repository exclusion for switches. Switching a
  working tree can affect files used by other running programs; check
  operational impact separately. Confirmation does not restart services.

**Windows is a receiver, not an authoring environment:** no Windows commit-all
or push button in the new default screen. Unexpected local edits or Windows
commits ahead of GitHub stop the normal workflow. Source updates do not
restart any running Fortis/RDG services. The historical TSV import and
old main-only Windows commit-all remain in the unused legacy UI source; the
new GUI does not import old `FORTIS_DOCKER_STATUS_V1` TSV files. The new
Docker branch snapshot is `FORTIS_DOCKER_BRANCH_V2` (name, active branch,
HEAD, dirty count, local branches, approved origin) and is not stored in Git.
In addition to the unchanged 13 column-detail lines, the details area lists
changed files **separately for the active Docker and Windows worktrees**, below
`ДОПОЛНИТЕЛЬНЫЕ СВЕДЕНИЯ`. Each item includes the Git porcelain status
(` M` = modified, `A ` = staged addition, ` D` = deletion, `??` = untracked,
etc.) and its path. The Docker exporter carries each status record as a
base64-encoded `F` line, so tabs, spaces and Git-quoted filenames do not
corrupt the snapshot. Windows reads `git status --porcelain=v1` locally,
without changing files. Both lists describe the **currently active branches**
regardless of which branches are selected for viewing. File lists are
limited to the first 500 entries per environment/repository; if there are
more entries or a status read fails, the UI explicitly marks the list as
partial/unavailable instead of reporting a clean worktree.

**Verification:** offline Linux source/fixture tests and Docker exporter can be
checked without touching live state:

```bash
python3 -B -m unittest -q test_mirror_docker_actions test_mirror_workflow_source test_mirror_status_source test_mirror_git_history
python3 -B tools/Export-Docker-Branch-Workflow.py --stdout --repo visual-workspace
```

For **real Windows WinForms compilation**, run from the reviewed Windows Git
clone only after owner-approved source propagation:

```powershell
powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File .\Test-MirrorWorkflowCompile.ps1
```

The compiler test does not open the UI, check GitHub, run Docker, switch
branches or pull. Windows UI compilation and end-to-end owner acceptance
remain **not verified from the Docker environment**. Treat the newly
implemented UI as a source candidate until those tests pass.

[gateway-native]: https://github.com/AlexPastukhh/fortis-approvals/blob/main/docs/NATIVE-WORKTREE-GATEWAY-2026-11-06.md
