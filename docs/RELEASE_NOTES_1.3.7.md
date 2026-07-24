# GigaAM Transcriber 1.3.7

## Русский

Тестовый релиз 1.3.7 реализует [issue #37](https://github.com/dubr1k/GigaAMGUI/issues/37): SRT и VTT теперь формируются короткими пофразными блоками вместо длинных технических ASR-сегментов.

### Что изменилось

- SRT и VTT используют единый cue planner и получают одинаковую сегментацию.
- При наличии word timestamps начало и конец каждой фразы соответствуют реальным границам слов.
- Границы предложений определяются по пунктуации; длинные фразы дополнительно делятся с ограничением числа строк и длины строки.
- Значения по умолчанию: разбиение по предложениям включено, не более двух строк и 64 символов в строке.
- При отсутствующих, неполных, нечисловых или немонотонных word timestamps применяется безопасный детерминированный fallback внутри исходного ASR-сегмента.
- После диаризации speaker turns сохраняют исходные слова и timestamps.
- TXT и Markdown не используют новые настройки и сохраняют прежнюю структуру.

### Интерфейсы

- Desktop GUI и Web GUI показывают настройки рядом с SRT/VTT.
- Python CLI поддерживает `--subtitle-sentence-split`, `--subtitle-max-lines` и `--subtitle-max-width`.
- Rust TUI поддерживает `/subtitle-split`, `/subtitle-lines` и `/subtitle-width`; значения сохраняются между запусками.

### Проверка

- Добавлены регрессионные тесты для sentence boundaries, line limits, длинных токенов, точных и повреждённых timestamps, общего SRT/VTT planner и word preservation после diarization.
- Проверены forwarding, persistence, locking и layout для Desktop GUI, Python CLI, Web GUI и Rust TUI.
- Полные Python и Rust test suites, Ruff, `compileall`, JavaScript syntax check и `git diff --check` проходят.

---

## English

Test release 1.3.7 implements [issue #37](https://github.com/dubr1k/GigaAMGUI/issues/37): SRT and VTT output now uses short phrase-level cues instead of long technical ASR segments.

### What changed

- SRT and VTT share one cue planner and therefore produce identical segmentation.
- When word timestamps are available, each cue starts and ends on the actual word boundaries.
- Sentence punctuation creates semantic boundaries; long phrases are additionally constrained by configurable line count and line width.
- Defaults are sentence splitting enabled, at most two lines, and 64 characters per line.
- Missing, incomplete, non-numeric, or non-monotonic word timestamps use a safe deterministic fallback inside the original ASR segment.
- Speaker turns preserve their words and timestamps after diarization.
- TXT and Markdown do not use the new options and retain their existing structure.

### Interfaces

- Desktop GUI and Web GUI expose the controls next to SRT/VTT.
- Python CLI supports `--subtitle-sentence-split`, `--subtitle-max-lines`, and `--subtitle-max-width`.
- Rust TUI supports `/subtitle-split`, `/subtitle-lines`, and `/subtitle-width`, persisted between runs.

### Validation

- Regression coverage includes sentence boundaries, line limits, long tokens, precise and malformed timestamps, shared SRT/VTT planning, and diarization word preservation.
- Forwarding, persistence, locking, and layout were verified for Desktop GUI, Python CLI, Web GUI, and Rust TUI.
- Full Python and Rust test suites, Ruff, `compileall`, JavaScript syntax validation, and `git diff --check` pass.
