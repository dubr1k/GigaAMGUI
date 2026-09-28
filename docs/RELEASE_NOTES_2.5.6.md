# GigaAM Transcriber 2.5.6

Патч-релиз для macOS-приложения GigaAM Liquid: интерфейс подстраивается под размер окна и полный экран.

## Исправлено

- **Полноэкранный режим больше не оставляет пустоту справа.** Страницы Liquid были колонкой фиксированной ширины, прижатой к левому краю, поэтому при разворачивании окна элементы оставались на месте. Теперь страница всегда во всю ширину окна, а лишняя высота уходит туда, где она нужна: в список выбранных файлов, текст Live, редакторы LLM, пример кода API, таблицу журнала и панель настроек.
- **Обычное окно показывает интерфейс целиком, без прокрутки.** В окне по умолчанию «Обработка», Live и LLM не помещались по высоте, а в окне уже 1200 pt появлялась и горизонтальная прокрутка. Теперь окно открывается размером 1240×940, и даже при минимальном размере 1100×880 любая страница видна полностью. Если экран меньше, окно подстраивается под него.
- **Длинные строки в текстовых полях не обрезаются.** Поле текста было шире своей рамки, и конец строк ответа LLM и транскрипта Live уходил за край. Теперь текст переносится.

## Изменено

- «Обработка»: слева — загрузка, выбранные файлы, папка результатов и кнопки «Запустить» / «Остановить» в один ряд; справа — настройки обработки и форматы вывода. Длинный список файлов прокручивается внутри своей карточки.
- LLM: две колонки — исходный текст и результат слева, шаблоны, промпт и запуск справа.
- Настройки: длинные разделы прокручиваются внутри панели; путь к CLI-инструменту в «Настройки → LLM» вынесен на отдельную строку.

## Что проверить

- Разверните Liquid на весь экран (зелёная кнопка или ⌃⌘F): карточки растягиваются по ширине, список файлов и текстовые поля — по высоте.
- В обычном окне пройдите по всем разделам: прокрутки нет ни по горизонтали, ни по вертикали.
- Добавьте папку с десятками файлов: список прокручивается внутри карточки «Выбранные файлы», кнопки запуска остаются на месте.

Изменения касаются только GigaAM Liquid; PyQt-версия, веб-интерфейс, API и TUI не менялись.

---

## English

Patch release for the GigaAM Liquid macOS app: the interface now follows the window size and full screen.

### Fixed

- **Full screen no longer leaves the right side empty.** Liquid pages were a fixed-width column pinned to the left edge, so nothing moved when the window grew. Pages now always span the window, and the spare height goes where it is useful: the selected-files list, the Live transcript, the LLM editors, the API code sample, the history table and the settings panel.
- **A normal window shows the whole interface without scrolling.** In the default window Processing, Live and LLM did not fit vertically, and a window narrower than 1200 pt also scrolled sideways. The window now opens at 1240×940, and even at the 1100×880 minimum every page is fully visible. On a smaller screen the window shrinks to fit it.
- **Long lines in text fields are no longer cut off.** The text view was wider than its frame, so the end of LLM answers and Live transcript lines ran past the edge. Text now wraps.

### Changed

- Processing: upload, selected files, output folder and the Start / Stop buttons (now side by side) on the left; processing settings and output formats on the right. A long file list scrolls inside its own card.
- LLM: two columns — source text and result on the left, templates, prompt and run controls on the right.
- Settings: long sections scroll inside the panel; the CLI tool path in Settings → LLM moved to its own line.

### What to check

- Make Liquid full screen (green button or ⌃⌘F): cards stretch across, the file list and text fields grow in height.
- In a normal window go through every section: there is no horizontal or vertical scrolling.
- Add a folder with dozens of files: the list scrolls inside the Selected files card and the Start buttons stay in place.

Only GigaAM Liquid changed; the PyQt app, web UI, API and TUI are unchanged.
