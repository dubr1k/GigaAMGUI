# GigaAM Transcriber 2.0.4

Исправляющий релиз нативного macOS-клиента GigaAMLiquid: главное меню,
сочетания клавиш и drag & drop.

## Исправления

- **Главное меню и ⌘Q.** SwiftPM-сборка не несёт `MainMenu.nib`, а
  программно меню не строилось — в menu bar было только имя приложения, а
  ⌘Q, ⌘W, ⌘H и ⌘X/⌘C/⌘V/⌘A в текстовых полях не имели key equivalents.
  Добавлено меню приложения / Файл / Правка / Окно / Справка со стандартными
  сочетаниями, «Выбрать файлы…» ⌘O, «Ссылка на медиа…» ⇧⌘O и ссылкой на
  проект. Подписи следуют переключателю RU/EN.
- **Закрытие окна.** Закрытие единственного окна оставляло процесс без окна и
  без меню; теперь закрытие окна завершает приложение.

## Добавлено

- **Drag & drop.** Аудио и видео можно перетащить из Finder в любое место
  окна: фильтр (audio/movie) и дедупликация те же, что у панели «Выбрать
  файлы», при дропе с другой страницы открывается «Обработка». Во время
  обработки или загрузки медиа дроп игнорируется.

## Packaging

- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.0.4`.

---

This patch release gives the native GigaAMLiquid client a real main menu (the
SwiftPM build ships no MainMenu.nib, so ⌘Q/⌘W/⌘C/⌘V had no key equivalents),
quits the app when its only window closes, and adds Finder drag & drop of audio
and video files with the same filtering and de-duplication as the open panel.
