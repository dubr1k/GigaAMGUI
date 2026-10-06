# GigaAM Transcriber 1.4.4

## Русский

### Исправлено

- Кнопка «Очистить» (и полный сброс настроек) забывала список файлов, но не
  саму папку источника — «Папка источника» и «Папка транскриптов» (LLM)
  оставались запомненными. После перезапуска приложение пересканировало ту же
  папку (новая логика из 1.4.3) и файлы возвращались в очередь, как будто
  очистка не сработала.
- Теперь очистка списка аудиофайлов и очистка списка транскриптов для
  LLM-обработки также сбрасывают запомненную папку — на следующем запуске
  сканировать нечего, очередь остаётся пустой.

## English

### Fixed

- Clicking "Clear" (or the full settings reset) emptied the file list but not
  the remembered source folder — both the audio "Source folder" and the LLM
  "Transcript folder" stayed remembered. After a restart the app rescanned
  that same folder (the new logic introduced in 1.4.3) and the files came
  back into the queue, making "Clear" look like it did nothing.
- Clearing the audio file list and clearing the LLM transcript list now also
  forget the remembered folder, so the next launch has nothing to rescan and
  the queue stays empty.
