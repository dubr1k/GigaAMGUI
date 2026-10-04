# GigaAM Transcriber 2.6.2

Патч-релиз: загрузка по ссылке с YouTube снова работает во всех версиях.

## Исправлено

- **Загрузка с YouTube падала с ошибкой HTTP 403.** Во всех интерфейсах — Liquid, классическом приложении, веб-версии, REST API и MCP — ссылка на обычное видео YouTube заканчивалась ошибкой «unable to download video data: HTTP Error 403: Forbidden». Страница видео открывалась, но сами аудио и видео YouTube отдавать отказывался: встроенный загрузчик yt-dlp обращался к нему способом, который YouTube перестал принимать. yt-dlp обновлён до 2026.08.19, где это исправлено. Короткие ролики при этом могли продолжать качаться, поэтому ошибка проявлялась не на каждой ссылке.
- **Сообщение об ошибке загрузки в Liquid было нечитаемым.** Вместо понятной причины Liquid показывал несколько экранов технического трейсбека и советовал проверить Python-окружение и `requirements.txt`, которых у установленного приложения нет. Теперь ошибка — одна строка с причиной, а совет про Python-окружение показывается только при запуске из исходников.
- **Понятное объяснение ошибки 403 везде.** Если сайт всё же откажет в доступе, все интерфейсы покажут одну строку: сервер отказал, для YouTube это обычно значит, что встроенный yt-dlp устарел (с номером версии), и нужно обновить GigaAM.

## Что проверить

- Вставьте в Liquid или в классическое приложение ссылку на обычное видео YouTube (не короткий ролик) и запустите загрузку по ссылке: файл должен скачаться и появиться в списке.
- То же через веб-версию или инструмент `transcribe` с параметром `url` в MCP.
- Пользователи TUI получат обновлённый yt-dlp командой `gigaam --update` (TUI 2.2.1).

Модели, распознавание, диаризация и Live не менялись.

---

## English

Patch release: downloading from YouTube by URL works again in every edition.

### Fixed

- **YouTube downloads failed with HTTP 403.** In every interface — Liquid, the classic app, the web version, the REST API and MCP — a link to a regular YouTube video ended with "unable to download video data: HTTP Error 403: Forbidden". The video page opened, but YouTube refused to serve the audio and video themselves: the bundled yt-dlp downloader requested them in a way YouTube no longer accepts. yt-dlp is updated to 2026.08.19, which fixes this. Short clips could still download, so the error did not show up on every link.
- **The download error in Liquid was unreadable.** Instead of a clear reason, Liquid showed several screens of technical traceback and advised checking the Python environment and `requirements.txt`, which the installed app does not have. The error is now a single line with the reason, and the Python-environment hint appears only when running from source.
- **A clear explanation of HTTP 403 everywhere.** If a site still refuses access, every interface shows one line: the server refused, for YouTube this usually means the bundled yt-dlp is out of date (with its version number), and GigaAM should be updated.

### What to check

- Paste a link to a regular YouTube video (not a short clip) into Liquid or the classic app and start the URL download: the file should download and appear in the list.
- The same through the web version, or the MCP `transcribe` tool with the `url` parameter.
- TUI users get the updated yt-dlp with `gigaam --update` (TUI 2.2.1).

Models, recognition, diarization and Live are unchanged.
