# GigaAM Transcriber 2.5.2

Патч-релиз: папка результатов в Liquid и читаемый TXT с абзацами.

## Исправлено

- **Liquid сохраняет результаты в выбранную папку.** Если поле «Папка сохранения результатов» было в фокусе, а папка выбиралась кнопкой «Изменить», поле показывало выбранную папку, но в настройках оставался пустой путь — и результаты сохранялись рядом с исходным файлом. Причина: при перестройке страницы старое, всё ещё редактируемое поле удалялось из окна, AppKit завершал его редактирование, и оно записывало своё пустое значение поверх выбранной папки. Повторная запись при завершении редактирования убрана — каждое изменение поля и так сохраняется сразу. Обычный GUI (PyQt) этой ошибки не имел.

## Изменено

- **Обычный TXT разбит на абзацы.** Раньше весь транскрипт был одной строкой. Теперь абзац закрывается только на конце предложения: после паузы от 2 секунд (если абзац уже не короткий) или когда он длиннее ~700 символов. Сокращения вроде «т.е.» разрывом не считаются. Текст без пунктуации (модели CTC/RNNT) делится на стыках сегментов примерно каждые 1400 символов. На двухчасовой лекции получается ~125 абзацев по 3–5 предложений, текст совпадает слово в слово.
- **`_diarize.txt` без обрывов фраз.** Внутри блока `[Спикер №N]` те же абзацы вместо строки на каждый сегмент распознавания, который резал фразы посередине.
- Файлы с таймкодами, Markdown и субтитры SRT/VTT не изменились.

## Что проверить

- Liquid: щёлкнуть в поле папки, нажать «Изменить», выбрать папку, запустить обработку — файлы появляются в выбранной папке.
- Открыть `.txt` и `_diarize.txt` новой расшифровки — текст разбит на абзацы, каждый начинается с начала предложения.
- Уже созданные TXT не меняются: для абзацев запись нужно распознать заново.

---

## English

Patch release: the Liquid output folder and readable paragraphs in TXT.

### Fixed

- **Liquid saves results to the chosen folder.** With the output-folder field focused, a folder picked with «Изменить» showed in the field while the stored setting stayed empty, so results were written next to the source file. Rebuilding the page removed the old, still-editing field from the window; AppKit ended its editing and the field wrote its empty text over the chosen folder. The end-of-editing save is removed — every edit is already saved as it happens. The PyQt GUI was not affected.

### Changed

- **Plain TXT is split into paragraphs.** The transcript used to be a single line. A paragraph now ends only at a sentence end: after a pause of 2 s or more (once it is not too short) or when it exceeds ~700 characters. Abbreviations such as «т.е.» do not split. Text without punctuation (CTC/RNNT models) splits at segment boundaries roughly every 1400 characters. A two-hour lecture yields ~125 paragraphs of 3–5 sentences with the text unchanged word for word.
- **`_diarize.txt` no longer breaks phrases.** Each `[Спикер №N]` block uses the same paragraphs instead of one line per recognition segment, which cut phrases mid-sentence.
- Timecoded files, Markdown and SRT/VTT subtitles are unchanged.

### What to check

- Liquid: click into the folder field, press «Изменить», pick a folder, start processing — results appear in the chosen folder.
- Open the `.txt` and `_diarize.txt` of a new transcription — the text is split into paragraphs, each starting a sentence.
- Existing TXT files are not rewritten; re-transcribe a recording to get paragraphs.
