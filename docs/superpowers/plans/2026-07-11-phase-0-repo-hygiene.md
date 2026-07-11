# Фаза 0: Гигиена репозитория — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать из рабочего дерева артефакты синхронизации/бэкапов и зафиксировать зелёный baseline тестов перед рефакторингом.

**Architecture:** Ничего в `src/` не меняется. Только `.gitignore`, удаление невыслеживаемого мусора, фиксация baseline.

**Tech Stack:** git, ruff, pytest.

## Global Constraints

- Поведение поверхностей (GUI / CLI / API / Web) — строго 1:1. В этой фазе код не трогаем вообще.
- Не удалять ничего из-под контроля версий без явной проверки, что оно невыслеживаемое и мусорное.
- Ветка работы: `refactor/monolith-decomposition`.

---

### Task 1: Baseline — зафиксировать текущее состояние тестов

**Files:**
- Modify: нет (только фиксация вывода)

- [ ] **Step 1: Прогнать линтер**

Run: `ruff check .`
Expected: записать текущий результат (может быть не пустым — это baseline, не чиним).

- [ ] **Step 2: Прогнать тесты**

Run: `pytest -q`
Expected: записать число passed/failed. Это эталон — после каждой последующей фазы должно быть не хуже.

- [ ] **Step 3: Сохранить baseline в файл**

Записать вывод в `docs/superpowers/plans/phase-0-baseline.txt` (кратко: команда + итоговая строка pytest).

```bash
git add -f docs/superpowers/plans/phase-0-baseline.txt
git commit -m "chore: record test baseline before refactor"
```

---

### Task 2: Игнорировать артефакты синхронизации и бэкапов

**Files:**
- Modify: `.gitignore`

**Interfaces:**
- Produces: обновлённый `.gitignore`, покрывающий Syncthing-конфликты и служебные бэкап-папки.

- [ ] **Step 1: Проверить, что эти пути невыслеживаемы**

Run: `git ls-files | grep -E 'sync-conflict|\.deploy-backups|\.opencode-backups|\.omo/|\.mimocode/|\.hermes/'`
Expected: пусто (файлы не под контролем версий). Если что-то выводится — НЕ удалять, вынести вопрос пользователю.

- [ ] **Step 2: Добавить правила в `.gitignore`**

Дописать в конец `.gitignore`:

```gitignore
# Syncthing conflict copies
*.sync-conflict-*
# Tooling backup/scratch dirs
.deploy-backups/
.opencode-backups/
.omo/
.mimocode/
.hermes/
```

- [ ] **Step 3: Проверить, что дерево чистое от шума**

Run: `git status --porcelain | grep -E 'sync-conflict|backups'`
Expected: пусто.

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore sync-conflict and tooling backup artifacts"
```

---

### Task 3: Удалить невыслеживаемые sync-conflict копии исходников

**Files:**
- Delete (невыслеживаемые): `src/gui/app_qt.sync-conflict-*.py`, `tests/test_gui_download_integration.sync-conflict-*.py`, `.sync-conflict-*.gitignore`

**Interfaces:**
- Produces: рабочее дерево без дублирующих sync-conflict `.py`, искажающих поиск и подсчёт монолитов.

- [ ] **Step 1: Показать, что будет удалено (dry-run)**

Run: `git clean -ndX -- '*.sync-conflict-*'` затем отдельно перечислить `.py`-конфликты:
`find . -name '*.sync-conflict-*' -not -path './.venv/*'`
Expected: список только sync-conflict файлов, никаких настоящих исходников.

- [ ] **Step 2: Удалить sync-conflict файлы**

```bash
find . -name '*.sync-conflict-*' -not -path './.venv/*' -not -path './.git/*' -delete
```

- [ ] **Step 3: Убедиться, что тесты не сломались**

Run: `pytest -q`
Expected: не хуже baseline из Task 1 (удаляем только дубликаты, не участвующие в сборе тестов помимо своего файла).

- [ ] **Step 4: Commit (если что-то из удалённого было под git — иначе только рабочее дерево)**

Run: `git status --porcelain`
Если удалённые файлы были отслеживаемыми — `git add -A && git commit -m "chore: remove sync-conflict duplicate files"`. Если все были невыслеживаемыми — коммитить нечего, зафиксировать факт в выводе.

---

## Self-Review

- **Spec coverage:** Реализует раздел «Фаза 0 — Гигиена репозитория» спеки: игнор `*.sync-conflict-*`, `.deploy-backups/`, `.opencode-backups/`, `.omo/`, `.mimocode/`, `.hermes/`; зелёный baseline. ✅
- **Placeholder scan:** конкретные пути и команды, без TBD. ✅
- **Безопасность:** каждый шаг удаления предваряется dry-run проверкой, что цели невыслеживаемы/мусорны. ✅
