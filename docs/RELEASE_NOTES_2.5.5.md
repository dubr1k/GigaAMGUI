# GigaAM Transcriber 2.5.5

Патч-релиз: LLM через CLI-провайдера больше не зависает на длинных записях.

## Исправлено

- **Выжимка длинной записи через Claude Code, Codex и другие CLI зависала навсегда.** Если текст для LLM был больше буфера канала (около 64 КБ) — многочасовая встреча или несколько транскриптов сразу, — CLI бесконечно ждал конца ввода. После первого таймаута проверки отмены остаток промпта не дописывался, и вход не закрывался. Ошибка появилась в 2.5.4 вместе с остановкой всего дерева процессов провайдера. Теперь промпт записывает отдельный поток, а отмена работает как раньше. Проверено на `claude -p` с промптом 125 КБ: ответ за 8 секунд.

## Что проверить

- LLM с CLI-провайдером на записи длиннее получаса: ответ приходит, а не висит.
- Остановка LLM во время ответа по-прежнему снимает все процессы провайдера.

TUI обновляется отдельно: `gigaam --update` (TUI 2.1.2 — то же исправление LLM и вставка списка путей в Terminal.app).

---

## English

Patch release: LLM runs through CLI providers no longer hang on long recordings.

### Fixed

- **Summarising a long recording through Claude Code, Codex or another CLI hung forever.** When the LLM input exceeded the pipe buffer (about 64 KB) — a multi-hour meeting or several transcripts at once — the CLI waited for end of input indefinitely: after the first cancellation-poll timeout the rest of the prompt was never written and stdin was never closed. The bug came with 2.5.4 together with stopping the provider's whole process tree. The prompt is now written by a separate thread, and cancellation works as before. Verified with `claude -p` and a 125 KB prompt: answered in 8 seconds.

### What to check

- LLM with a CLI provider on a recording longer than half an hour: the answer arrives instead of hanging.
- Stopping an LLM run mid-answer still ends every provider process.

The TUI updates separately with `gigaam --update` (TUI 2.1.2 carries the same LLM fix and multi-line path pasting in Terminal.app).
