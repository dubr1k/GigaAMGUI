# TUI 2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Русский TUI с переключателем языка, вкладками «Обработка / LLM / Настройки / Журнал», панелью параметров, подсказкой «Далее», справкой `?` и поддержкой мыши — на ratatui 0.30 / crossterm 0.29, с `main.rs`, разбитым на модули.

**Architecture:** Состояние в `App` (`app.rs`), все действия пользователя — клавиши и клики — сводятся к `Action` и проходят через одну функцию `dispatch(&mut App, Action, &mut WorkerLink)`. Отрисовка (`ui/`) регистрирует интерактивные прямоугольники в `HitMap` за кадр; событие мыши превращается в `Action` через `HitMap::hit`. Тексты — через `i18n::t(lang, key)`. Воркер и headless-режим не меняются.

**Tech Stack:** Rust 2021, ratatui 0.30.2, crossterm 0.29, ratatui-image 11.1, serde_json (preserve_order). Тесты: `cargo test --release --manifest-path tui/Cargo.toml`; pty-прогон `scratchpad/cfg9/drive2.py`.

**Spec:** `docs/superpowers/specs/2026-09-21-tui-redesign-design.md`

## Global Constraints

- Версии: `ratatui = "0.30.2"`, `crossterm = "0.29"`, `ratatui-image = { version = "11.1", default-features = false, features = ["crossterm", "image-defaults"] }`; `ratatui-image 12.x` (rc) не брать.
- Модули: `main.rs` (≤ 300 строк), `app.rs`, `i18n.rs`, `settings.rs`, `commands.rs`, `worker.rs`, `headless.rs`, `pets.rs`, `ui/{mod,processing,llm,settings,log,help,menu}.rs`.
- Ключи i18n — идентификаторы вида `tab.processing`, `hint.add_files`, `stage.preprocessing`; таблица `&[(&str, &str, &str)]` (key, ru, en); `t(lang, key) -> &'static str` паникует в debug/возвращает ключ в release, если ключа нет.
- Язык по умолчанию `ru`; приоритет `--lang` → `TuiSettings.language` → `ru`; синхронизация с десктопом ключом `language` в `user_settings.json` по правилам `settings.rs` (десктоп установлен → читаем/пишем туда).
- Headless-режим (`gigaam transcribe/llm`, `HEADLESS_USAGE`) остаётся английским и неизменным по поведению.
- `/команды` и хоткеи из README сохраняются: `s`, `L`/`l`, `d`, `f`, `r`, `Esc`, `q`, `Ctrl+C×2`, `Tab`-автодополнение путей, `Delete`/`Backspace`, `Ctrl+↑↓`, цифры и `0` в меню.
- Каждый тест, который вызывает `run_command`/`dispatch`/`save_settings`/`load_settings`, начинается с `let _config = isolated_config_dir();`.
- Никакой AI-атрибуции в коммитах; коммит на задачу; пуш — только по просьбе пользователя.
- После каждой задачи: `cargo test --release --manifest-path tui/Cargo.toml` зелёный, `cargo build --release` без предупреждений, `cargo fmt`, `.venv/bin/python -m pytest tests/test_tui_requirements.py tests/test_install_tui_launcher.py -q` зелёный.

---

## Файлы

| Файл | Ответственность |
|---|---|
| `tui/Cargo.toml`, `Cargo.lock` | версии (Task 1) |
| `tui/src/main.rs` | аргументы, headless-ветка, терминал (raw mode, alt screen, mouse capture), цикл событий |
| `tui/src/app.rs` | `App`, `Page`, `Focus`, `handle_message`, `next_step`, `dispatch` |
| `tui/src/i18n.rs` | `Lang`, `STRINGS`, `t`, `tf` |
| `tui/src/settings.rs` | `TuiSettings`, пути, `load_settings`/`save_settings`, десктоп-синк, `.env`, `isolated_config_dir` (test) |
| `tui/src/commands.rs` | `COMMANDS`, `command_suggestions`, `run_command`, `open_command_menu`, `command_menu_options`, `apply_command_menu`, `queue_paths`, `normalize_path`, `complete_path` |
| `tui/src/worker.rs` | `spawn_worker[_with]`, `send`, `LlmTool`, `llm_settings_from/payload`, `start_payload` |
| `tui/src/headless.rs` | всё headless |
| `tui/src/pets.rs` | кадры, `refresh_pet_image`, `clear_pet_layer` |
| `tui/src/ui/mod.rs` | `draw`, `HitMap`, `Action`, `AreaId`, `ButtonId`, общие стили, шапка/вкладки/футер/подсказка/строка ввода |
| `tui/src/ui/processing.rs`, `llm.rs`, `settings.rs`, `log.rs`, `help.rs`, `menu.rs` | по вкладке |

---

### Task 1: Обновить ratatui / crossterm / ratatui-image

**Files:**
- Modify: `tui/Cargo.toml`, `tui/Cargo.lock`, `tui/src/main.rs` (только места, которые перестанут компилироваться).

- [ ] **Step 1: Версии**

```toml
crossterm = "0.29"
ratatui = "0.30.2"
ratatui-image = { version = "11.1", default-features = false, features = ["crossterm", "image-defaults"] }
```

- [ ] **Step 2: `cargo build --release --manifest-path tui/Cargo.toml`** — читать ошибки компилятора и править по одному: ожидаемые точки — `Picker::from_query_stdio()` (в 11.x возвращает `Result<Picker>` — как сейчас), `picker.new_resize_protocol(image)` (проверить имя; в 11.x метод называется `new_resize_protocol`), `Picker::protocol_type()`; `crossterm` 0.29: `KeyEventKind`, `Event::Paste`, `EnterAlternateScreen` — без изменений. Никаких функциональных правок.

- [ ] **Step 3: Тесты и pty-прогон** — `cargo test` (39), затем `python3 scratchpad/cfg9/drive2.py <copy-of-config> $'\x1b[?62;c' $'/backend onnx\r'` (скрипт из прошлой сессии: `/private/tmp/claude-501/…/scratchpad/cfg9/drive2.py`; если недоступен — контроллер повторит проверку) и убедиться, что TUI рисуется (лог `tui.log` содержит «terminal transcriber») и `tui_settings.json` обновился.

- [ ] **Step 4: Commit** — `"TUI: ratatui 0.30, crossterm 0.29, ratatui-image 11"`; в теле — что именно менялось в API.

---

### Task 2: Разбить `main.rs` на модули (без изменения поведения)

**Files:**
- Create: `tui/src/{app,i18n,settings,commands,worker,headless,pets}.rs`, `tui/src/ui/mod.rs` (пока только `draw` целиком).
- Modify: `tui/src/main.rs`.

**Interfaces (что где оказывается):**
- `settings.rs`: `pub struct TuiSettings`, `pub fn load_settings() -> TuiSettings`, `pub fn save_settings(&TuiSettings) -> Result<(), String>`, `pub fn save_app_settings(app: &mut App)`, `pub(crate) fn config_dir/settings_path/main_app_settings_path/env_file_path`, `impl From<&App> for TuiSettings`, `#[cfg(test)] pub(crate) fn isolated_config_dir() -> ConfigGuard`.
- `worker.rs`: `pub struct LlmTool`, `pub fn spawn_worker()`, `pub fn spawn_worker_with(stderr: Stdio)`, `pub fn send(...)`, `pub fn llm_settings_from(...)`, `pub fn llm_settings_payload(app: &App) -> Value`, `pub fn start_payload(app: &App) -> Value`, `FALLBACK_PROVIDERS`, `provider_prefix`, `llm_tool_for`, `provider_menu_options`, `provider_from_menu_option`.
- `app.rs`: `pub struct App` (все поля `pub(crate)`), `impl Default`, `impl App { pub fn log, pub fn handle_message }`, `llm_input_files`, `llm_can_run`, `request_llm`, `esc_should_soft_cancel`, `reset_after_worker_restart`.
- `commands.rs`: `pub const COMMANDS`, `BACK_MENU_OPTION`, `MODEL_OPTIONS`, `FORMAT_KEYS`, `selectable_backends`, `backend_is_supported`, `backend_usage`, `command_suggestions`, `is_command`, `accept_command_suggestion`, `command_menu_options`, `open_command_menu`, `apply_command_menu`, `run_command`, `remove_selected_file`, `queue_paths`, `normalize_path`, `split_shell_paths`, `complete_path`.
- `headless.rs`: `HeadlessCommand`, `TranscribeArgs`, `LlmArgs`, `parse_headless_args`, `strip_data_dir`, `resolve_headless_paths`, `headless_start_payload`, `headless_llm_payload`, `format_headless_line`, `run_headless`, `HEADLESS_USAGE`, `data_dir_from_args`, `apply_data_dir_argument`.
- `pets.rs`: `PET_IDLE_FRAMES`, `PET_RUN_FRAMES`, `impl App { clear_pet_layer, refresh_pet_image }`.
- `ui/mod.rs`: `pub fn draw(frame, app)` — текущая функция как есть; `timecode`.
- `main.rs`: `fn main()` с циклом событий как сейчас (в Task 4 он изменится).

- [ ] **Step 1:** Переместить код по модулям (`mod …;` в `main.rs`, `use crate::…`). Тесты каждого блока — в `#[cfg(test)] mod tests` соответствующего модуля; тест-хелпер `isolated_config_dir` — в `settings.rs`, остальные тесты импортируют `crate::settings::isolated_config_dir`.
- [ ] **Step 2:** `cargo build --release` без предупреждений (`#[allow(dead_code)]` не добавлять — если что-то стало неиспользуемым, значит перемещено неверно). `cargo test` — те же 39 тестов, все зелёные. `wc -l tui/src/main.rs` ≤ 300.
- [ ] **Step 3: Commit** — `"TUI: split main.rs into modules"` (в теле: список модулей, «поведение не менялось, тесты те же»).

---

### Task 3: i18n, настройка языка, `/lang`, синхронизация с десктопом

**Files:**
- Create: `tui/src/i18n.rs`
- Modify: `tui/src/settings.rs` (поле `language`, синк), `tui/src/app.rs` (`lang: Lang`), `tui/src/commands.rs` (`/lang`, описания команд через ключи), `tui/src/main.rs` (`--lang`).

**Interfaces:**
```rust
// i18n.rs
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Lang { Ru, En }
impl Lang {
    pub fn parse(s: &str) -> Option<Lang>   // "ru"/"en", регистронезависимо
    pub fn code(self) -> &'static str        // "ru"/"en"
    pub fn toggle(self) -> Lang
}
pub const STRINGS: &[(&str, &str, &str)] = &[ /* key, ru, en */ ];
pub fn t(lang: Lang, key: &str) -> &'static str;
pub fn tf(lang: Lang, key: &str, args: &[(&str, &str)]) -> String; // подстановка {name}
```
Ключи, которые обязаны быть в таблице к концу Task 3 (остальные добавляются в задачах 5–7): `app.title`, `status.ready`, `status.running`, `status.idle`, `tab.processing`, `tab.llm`, `tab.settings`, `tab.log`, `cmd.<name>` для каждой команды из `COMMANDS` (описание), `lang.name` («Русский»/«English»), `stage.preparing`, `stage.conversion`, `stage.preprocessing`, `stage.transcription`, `stage.diarization`, `stage.export`, `stage.finalizing` (тексты как в `src/gui/processing_mixin.py:_STAGE_NAMES`), `settings.language`, `settings.language_changed` («Язык: Русский» / «Language: English»).

- [ ] **Step 1: Тесты** (`i18n.rs`):

```rust
#[test]
fn every_string_has_both_languages_and_unique_keys() {
    let mut seen = std::collections::HashSet::new();
    for (key, ru, en) in STRINGS {
        assert!(!ru.is_empty() && !en.is_empty(), "{key}");
        assert!(seen.insert(*key), "duplicate key {key}");
    }
}
#[test]
fn every_command_has_a_description_key() {
    for (name, _) in crate::commands::COMMANDS {
        let key = format!("cmd.{}", name.trim_start_matches('/'));
        assert!(STRINGS.iter().any(|(k, _, _)| *k == key), "{key}");
    }
}
#[test]
fn keys_used_in_sources_exist() {
    let re = regex_lite::Regex::new(r#"\bt[f]?\(\s*[a-z_.]+\s*,\s*"([a-z0-9_.]+)""#).unwrap();
    for file in ["app.rs", "commands.rs", "main.rs", "ui/mod.rs", "ui/processing.rs", "ui/llm.rs", "ui/settings.rs", "ui/log.rs", "ui/help.rs", "ui/menu.rs"] {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src").join(file);
        let Ok(src) = std::fs::read_to_string(&path) else { continue };
        for cap in re.captures_iter(&src) {
            let key = &cap[1];
            assert!(STRINGS.iter().any(|(k, _, _)| *k == key), "{file}: missing key {key}");
        }
    }
}
#[test]
fn tf_substitutes_named_arguments() {
    assert_eq!(tf(Lang::Ru, "queue.count", &[("n", "3")]), "Очередь (3)");
}
```
(добавить `regex-lite = "0.1"` в `[dev-dependencies]`.)

`settings.rs`:
```rust
#[test]
fn language_round_trips_and_syncs_with_the_desktop_app() {
    let directory = isolated_config_dir();
    std::fs::write(directory.join("user_settings.json"), r#"{"language":"en"}"#).unwrap();
    let settings = load_settings();
    assert_eq!(settings.language, "en");
    let mut updated = settings; updated.language = "ru".into();
    save_settings(&updated).unwrap();
    let main: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(directory.join("user_settings.json")).unwrap()).unwrap();
    assert_eq!(main["language"], "ru");
}
```
`commands.rs`:
```rust
#[test]
fn lang_command_switches_and_persists() {
    let _config = isolated_config_dir();
    let mut app = App::default();
    app.input = "/lang en".into(); run_command(&mut app);
    assert_eq!(app.lang, Lang::En);
    assert_eq!(app.status, "Language: English");
    app.input = "/lang xx".into(); run_command(&mut app);
    assert_eq!(app.status, "Usage: /lang ru|en");
}
```

- [ ] **Step 2: RED**, затем реализация: `TuiSettings.language: String` (default `"ru"`), чтение/запись `language` в десктоп-синке (`shared_settings_from_main_app` / `_into_main_app`), `App.lang`, загрузка в `main()` (`--lang ru|en` перекрывает; аргумент вырезать из argv как `--data-dir`), `/lang` в `COMMANDS` + `run_command` (+`save_app_settings`), `COMMANDS` описания — оставить английские строки в константе (используются headless/тестами), но в UI подсказок брать `t(lang, "cmd.<name>")`. Заменить `app.status` строки, которые уже есть в коде, на `t(...)` **только** там, где это задевается (`/lang`); массовый перевод статусов — в Task 5–7 по вкладкам.

- [ ] **Step 3: GREEN**, `cargo fmt`, commit `"TUI: language setting with /lang, synced with the desktop app"`.

---

### Task 4: Мышь: `HitMap`, `Action`, `dispatch`, захват мыши, `/mouse`

**Files:**
- Modify: `tui/src/ui/mod.rs` (HitMap/Action, регистрация областей в текущем `draw`: пункты меню, подсказки команд, файлы очереди), `tui/src/app.rs` (`dispatch`), `tui/src/main.rs` (mouse capture, обработка `Event::Mouse`), `tui/src/settings.rs` (`mouse: bool`), `tui/src/commands.rs` (`/mouse on|off`).

**Interfaces:**
```rust
// ui/mod.rs
#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    Tab(Page), SelectFile(usize), RemoveFile(usize), OpenMenu(&'static str), MenuItem(usize),
    Suggestion(usize), ToggleMode(&'static str), Button(ButtonId), ToggleLang, Help,
    Scroll(AreaId, i32), FocusInput, SettingsRow(usize), LlmInput(usize),
}
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ButtonId { Start, Stop, RunLlm, CancelLlm, ClearQueue, ClearLog }
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum AreaId { Queue, LlmOutput, Log, Settings, Help }
#[derive(Default)]
pub struct HitMap { items: Vec<(Rect, Action)> }
impl HitMap {
    pub fn clear(&mut self)
    pub fn add(&mut self, rect: Rect, action: Action)
    pub fn hit(&self, x: u16, y: u16) -> Option<Action>   // последний добавленный выигрывает (рисуется поверх)
}
// app.rs
pub enum Page { Processing, Llm, Settings, Log }
pub struct WorkerLink<'a> { pub stdin: &'a mut ChildStdin }   // для действий, которым надо слать команды
pub fn dispatch(app: &mut App, action: Action, worker: Option<&mut ChildStdin>) -> Vec<Value>;
// dispatch возвращает JSON-команды, которые main() отправит воркеру (так тесты не нуждаются в процессе)
```
`App` получает `hits: HitMap` (перерисовка очищает), `page: Page`, `mouse_enabled: bool`, `scroll: HashMap<AreaId, u16>`.

- [ ] **Step 1: Тесты** (`ui/mod.rs`, `app.rs`):

```rust
#[test]
fn hit_map_returns_the_topmost_action() {
    let mut map = HitMap::default();
    map.add(Rect::new(0, 0, 10, 10), Action::Tab(Page::Llm));
    map.add(Rect::new(2, 2, 3, 3), Action::Help);
    assert_eq!(map.hit(3, 3), Some(Action::Help));
    assert_eq!(map.hit(9, 9), Some(Action::Tab(Page::Llm)));
    assert_eq!(map.hit(20, 20), None);
}
#[test]
fn dispatch_matches_the_keyboard_equivalents() {
    let _config = isolated_config_dir();
    let mut app = App::default();
    app.files = vec!["/tmp/a.wav".into(), "/tmp/b.wav".into()];
    dispatch(&mut app, Action::SelectFile(1), None);
    assert_eq!(app.selected_file, Some(1));
    dispatch(&mut app, Action::Tab(Page::Settings), None);
    assert_eq!(app.page, Page::Settings);
    dispatch(&mut app, Action::OpenMenu("/backend"), None);
    assert_eq!(app.command_menu.as_deref(), Some("/backend"));
    dispatch(&mut app, Action::MenuItem(1), None);
    assert_eq!(app.backend, selectable_backends()[1]);
    dispatch(&mut app, Action::ToggleLang, None);
    assert_eq!(app.lang, Lang::En);
    let commands = dispatch(&mut app, Action::Button(ButtonId::Start), None);
    assert_eq!(commands[0]["type"], "start");
}
#[test]
fn mouse_setting_persists_and_defaults_on() {
    let _config = isolated_config_dir();
    let mut app = App::default();
    assert!(app.mouse_enabled);
    app.input = "/mouse off".into(); run_command(&mut app);
    assert!(!app.mouse_enabled);
    assert!(!load_settings().mouse);
}
```

- [ ] **Step 2: RED → реализация.** `dispatch` — единственное место с логикой действий; существующие обработчики клавиш в `main.rs`, которые дублируют действие (выбор файла, меню, старт, LLM), переводятся на вызов `dispatch(...)` с соответствующим `Action`, а команды воркеру отправляются из возвращённого `Vec<Value>`. В `main.rs`: `execute!(stdout, EnterAlternateScreen, EnableMouseCapture)` если `app.mouse_enabled`; `Event::Mouse(m)` → `Down(Left)` → `app.hits.hit(m.column, m.row)` → `dispatch`; `ScrollUp/Down` → `Action::Scroll(area, ∓3)` для области под курсором (в `HitMap` области прокрутки регистрируются как `Action::Scroll(area, 0)`, `hit_scroll(x,y) -> Option<AreaId>`). `/mouse on|off` включает/выключает захват на лету (`execute!(stdout, EnableMouseCapture|DisableMouseCapture)`), `LeaveAlternateScreen` + `DisableMouseCapture` при выходе. В текущем `draw` зарегистрировать: пункты меню (`MenuItem(i)`), подсказки команд (`Suggestion(i)`), строки очереди (`SelectFile(i)`).

- [ ] **Step 3: GREEN**, `cargo fmt`, commit `"TUI: mouse support — hit map, one dispatch path for keys and clicks, /mouse"`.

---

### Task 5: Вкладки, шапка, футер, подсказка «Далее», вкладка «Обработка»

**Files:**
- Modify: `tui/src/ui/mod.rs` (каркас: шапка, вкладки, подсказка, ввод, футер; вызов страницы), `tui/src/app.rs` (`next_step`, `Focus`), `tui/src/i18n.rs` (ключи), `tui/src/main.rs` (`Tab`/`Shift+Tab`/`F1–F4`, `?`).
- Create: `tui/src/ui/processing.rs`, `tui/src/ui/menu.rs` (меню выбора и подсказки команд — вынести из старого `draw`).

**Interfaces:**
```rust
// app.rs
pub fn next_step(app: &App) -> &'static str;  // ключ i18n: hint.worker_down | hint.cancel_batch | hint.cancel_llm | hint.add_files | hint.start | hint.run_llm | hint.view_result
pub enum Focus { Input, Queue, Params }
// ui/processing.rs
pub fn draw(frame: &mut Frame, area: Rect, app: &mut App);
// регистрирует: SelectFile(i) на строках очереди, OpenMenu(cmd) на строках панели параметров
// (порядок и команды: /backend, /model, /formats, /diarize, /speakers, /audio-mode, /output),
// Button(Start|Stop), Button(ClearQueue), Scroll(AreaId::Queue, 0)
```
Раскладка страницы «Обработка»: `Layout::vertical([Min(6), Length(5)])` → верх `Layout::horizontal([Percentage(60), Percentage(40)])` (очередь | параметры), низ — блок «Прогресс» (gauge, стадия `stage.<id>` + `HH:MM:SS/HH:MM:SS`, до 3 последних сохранённых файлов). Очередь — `Table` с колонками `№`(4) `Файл`(Min) `Состояние`(14); состояние строки: `ожидает` (не начат), `в обработке` (`file_index == i && running`), `готово`/`ошибка` (из `file_completed`, хранить `App.file_states: HashMap<String, FileState>`), `отменён`. Панель — `Table` 2 колонки (название, значение), выделенная строка при `Focus::Params`; `Enter` → `Action::OpenMenu`. `→`/`←` переключают `Focus` между очередью и панелью, `Esc` из панели → `Focus::Input`.

Шапка: `" GigaAM  ● {status}   RU|EN   ? {help}"`; клик по `RU`/`EN` → `ToggleLang`, по `?` → `Help`. Вкладки — `Tabs` виджет, клик → `Tab(page)`. Подсказка: `"▶ {t(next_step)}"` (accent). Футер: `"s {start} · L {llm} · d {diar} · f {formats} · ? {help} · q {quit}"` локализованный.

- [ ] **Step 1: Тесты** (`app.rs`):
```rust
#[test]
fn next_step_follows_the_state_machine() {
    let mut app = App::default();
    assert_eq!(next_step(&app), "hint.add_files");
    app.files.push("/tmp/a.wav".into());
    assert_eq!(next_step(&app), "hint.start");
    app.running = true;
    assert_eq!(next_step(&app), "hint.cancel_batch");
    app.running = false; app.result_files.push("/tmp/a.txt".into());
    assert_eq!(next_step(&app), "hint.run_llm");
    app.llm_running = true; app.running = true;
    assert_eq!(next_step(&app), "hint.cancel_llm");
    app.llm_running = false; app.running = false; app.llm_results.push(("summary".into(), "…".into()));
    assert_eq!(next_step(&app), "hint.view_result");
    app.worker_down = true;
    assert_eq!(next_step(&app), "hint.worker_down");
}
#[test]
fn file_states_follow_worker_events() {
    let mut app = App::default();
    app.files = vec!["/tmp/a.wav".into()];
    app.handle_message(json!({"type":"file_started","file":"/tmp/a.wav","file_index":0,"total_files":1}));
    assert_eq!(app.file_state("/tmp/a.wav"), FileState::Processing);
    app.handle_message(json!({"type":"file_completed","file":"/tmp/a.wav","file_index":0,"result":{"success":false,"saved_files":[],"error":"x"}}));
    assert_eq!(app.file_state("/tmp/a.wav"), FileState::Failed);
}
```
`ui/processing.rs` — тест рендера через `ratatui::backend::TestBackend`:
```rust
#[test]
fn processing_page_renders_russian_headings_and_registers_param_rows() {
    let _config = isolated_config_dir();
    let mut app = App::default();
    app.files = vec!["/tmp/запись.wav".into()];
    let backend = ratatui::backend::TestBackend::new(100, 30);
    let mut terminal = ratatui::Terminal::new(backend).unwrap();
    terminal.draw(|f| crate::ui::draw(f, &mut app)).unwrap();
    let text = terminal.backend().to_string();
    for needle in ["Обработка", "LLM", "Настройки", "Журнал", "Очередь", "Параметры", "Движок", "Далее"] {
        assert!(text.contains(needle), "{needle}\n{text}");
    }
    assert!(app.hits.hit(2, 1).is_some(), "tab bar is clickable");
    assert!(matches!(app.hits.items().iter().find(|(_, a)| *a == Action::OpenMenu("/backend")), Some(_)));
}
```
(добавить `pub fn items(&self) -> &[(Rect, Action)]` в `HitMap`.)

- [ ] **Step 2: RED → реализация**, все строки страницы через `t(app.lang, …)`; добавить ключи. `App.worker_down: bool` выставляется в `main()`, если `spawn_worker` упал / канал отвалился.
- [ ] **Step 3: GREEN**, `cargo fmt`, commit `"TUI: tabs, header/footer, next-step hint, Processing page with queue and parameter panel"`.

---

### Task 6: Вкладка «LLM»

**Files:**
- Create: `tui/src/ui/llm.rs`. Modify: `app.rs` (`llm_scroll`), `i18n.rs`.

**Interfaces:** `pub fn draw(frame, area, app)`; регистрирует `LlmInput(i)` (выбор/удаление `Delete`), `ToggleMode("summary"|"tasks"|"terms"|"custom")`, `OpenMenu("/settings-provider")`, `Button(RunLlm|CancelLlm)`, `Scroll(AreaId::LlmOutput, 0)`. Раскладка: верх `horizontal([Percentage(50), Percentage(50)])` — «Транскрипты» (Table №/Файл) | «Что сделать» (4 чекбокса `[x]`, строка «Промпт: …» (клик → предзаполнить `/llm-prompt `), строка «Провайдер: {name} · {version|not installed}», кнопки), низ — «Ответ» (`Paragraph` с `scroll((app.scroll[LlmOutput],0))`, заголовок «Ответ · {mode} · сохранено: {path}` или «стрим…»).

- [ ] **Step 1: Тест** (TestBackend 100×30): после `app.page = Page::Llm`, `app.result_files = ["/tmp/a.txt"]`, `app.llm_results = [("summary", "Итог")]` — текст содержит «Транскрипты», «Что сделать», «Выжимка», «Провайдер», «Итог»; `hits` содержит `ToggleMode("tasks")` и `Button(ButtonId::RunLlm)`; `dispatch(ToggleMode("tasks"))` добавляет режим; `dispatch(Button(RunLlm))` возвращает команду `llm_start` с `files == ["/tmp/a.txt"]`.
- [ ] **Step 2: RED → реализация.** `r` теперь переключает вкладку на LLM и показывает ответ (вместо блока в старом draw). Esc-логика LLM (soft cancel → kill) не меняется.
- [ ] **Step 3: GREEN**, commit `"TUI: LLM page — inputs, modes, provider, streamed answer with scrolling"`.

---

### Task 7: Вкладки «Настройки» и «Журнал», справка `?`

**Files:**
- Create: `tui/src/ui/settings.rs`, `tui/src/ui/log.rs`, `tui/src/ui/help.rs`. Modify: `app.rs` (`settings_cursor`, `help_open`), `commands.rs` (`/settings` → `Tab(Settings)`, `/help`), `main.rs` (`?` только при пустом вводе), `i18n.rs`.

**Interfaces:**
```rust
// ui/settings.rs
pub struct SettingRow { pub key: &'static str /* i18n */, pub value: String, pub action: Action }
pub fn rows(app: &App) -> Vec<SettingRow>;   // порядок из спеки §3 «Настройки»
pub fn draw(frame, area, app);                // List с выделением app.settings_cursor; регистрирует SettingsRow(i) и Scroll(AreaId::Settings,0)
```
Действия строк: перечислимые → `OpenMenu("/backend" | "/onnx-provider" | "/model" | "/diarization-backend" | "/audio-mode" | "/settings-provider" | "/settings-model")`; булевы → `ToggleSetting("mouse"|"pets"|"subtitle_split"|"llm_tools")` (новый вариант `Action::ToggleSetting(&'static str)`); текстовые → `EditSetting("/llm-api-url" | "/llm-api-key" | "/llm-temperature" | "/subtitle-lines" | "/subtitle-width" | "/llm-path" | "/llm-provider-name" | "/llm-args" | "/output")` — предзаполняет `app.input` командой с пробелом и ставит `Focus::Input`; язык → `OpenMenu("/lang")` (меню `Русский | English`). Ключ API отображается как `••••` если непустой.

`ui/log.rs`: `Paragraph` всех `app.logs` со `scroll`, автоследование если `scroll` был внизу, `Ctrl+L` → `Button(ClearLog)`.

`ui/help.rs`: оверлей `Clear` + `Block` по центру (80%×80%), две колонки: клавиши / команды (`COMMANDS` с `t("cmd.<name>")`), закрытие `Esc`/`?`/клик вне; регистрирует `Action::Help` на всей площади оверлея (клик закрывает).

- [ ] **Step 1: Тесты:** `rows()` содержит строку языка со значением «Русский» и строку ключа со значением `••••` при непустом ключе; `dispatch(SettingsRow(i))` для строки «Мышь» переключает `mouse_enabled`; `dispatch(Help)` переключает `help_open`; рендер справки содержит «/backend» и описание из таблицы; `/help` и `?` открывают справку; `/settings` переключает `page`.
- [ ] **Step 2: RED → реализация.** Старое меню `/settings` (индексы 0–8) удалить — его заменяет вкладка; `/settings-provider` и `/settings-model` меню остаются.
- [ ] **Step 3: GREEN**, commit `"TUI: Settings and Log pages, ? help overlay"`.

---

### Task 8: Перевод остатков, pty-проверка, документация

**Files:**
- Modify: `tui/src/**` (все `app.status = "…"` и `app.log("…")` с английскими литералами → `t()`/`tf()`; исключение — headless и сообщения воркера), `README.md`, `README_EN.md`, `docs/CHANGELOG.md`, `skills/gigaam/SKILL.md` (только упоминание, что интерактивный TUI по-русски, `/lang en`).

- [ ] **Step 1:** Тест-«ловушка» в `i18n.rs`: пройти по `app.rs`, `commands.rs`, `ui/*.rs` регуляркой `app\.status = "([A-Za-z][^"]*)"` и `app\.log\("([A-Za-z][^"]*)"` — совпадений быть не должно (английские литералы в статусах запрещены); допускается список исключений `ALLOWED_LITERAL_STATUSES` (пустой на момент завершения).
- [ ] **Step 2:** Перевести всё оставшееся; `Usage: …` строки команд — тоже через ключи `usage.<name>`.
- [ ] **Step 3:** pty-прогон: `python3 scratchpad/cfg9/drive2.py <copy-config> $'\x1b[?62;c' $'/lang en\r'` → в `tui.log` до переключения есть «Обработка» и «Далее», после — «Processing» и «Next». Приложить фрагменты в отчёт.
- [ ] **Step 4:** Документация: README (раздел TUI: вкладки, мышь, `?`, `/lang`, `/mouse`, скриншот не требуется), CHANGELOG `[Unreleased]` → «TUI 2.0».
- [ ] **Step 5:** Commit `"TUI: translate the remaining messages, document TUI 2.0"`.

---

## Self-review

- Спека §1 → Task 1; §2 → Task 3 (+8); §3 → Tasks 5–7; §4 → Task 4; §5 → Task 5; §6 → Task 2; §7 → Tasks 3–4 (`serde(default)`), §8 → тесты в каждой задаче + Task 8 pty; §9 → Task 8.
- Имена согласованы: `Action`, `ButtonId`, `AreaId`, `HitMap{add,hit,items,clear}`, `dispatch(app, action, Option<&mut ChildStdin>) -> Vec<Value>`, `Page`, `Focus`, `FileState`, `next_step`, `Lang{parse,code,toggle}`, `t/tf`, `SettingRow/rows`, `Action::ToggleSetting/EditSetting` (введены в Task 7 — добавить в enum там же).
- Риск: ratatui 0.30 мог переименовать `Frame`/`Tabs` API — Task 1 закрывает до редизайна. Риск: `TestBackend::to_string` — в 0.30 есть `Display` для `TestBackend` (буфер как текст); если нет — использовать `terminal.backend().buffer()` и собирать строки.
