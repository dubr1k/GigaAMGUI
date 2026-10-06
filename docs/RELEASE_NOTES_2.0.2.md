# GigaAM Transcriber 2.0.2

Исправляющий релиз упаковки нативного macOS-клиента GigaAMLiquid.

## Исправления

- В `GigaAMLiquid.app` добавлена фирменная иконка приложения в формате ICNS.
- `Info.plist` теперь содержит `CFBundleIconFile`, поэтому Finder, Dock и окно
  выбора приложений отображают иконку вместо стандартной заглушки macOS.
- Release job проверяет наличие непустого файла иконки и соответствующего ключа
  в `Info.plist` до публикации online- и offline-архивов Liquid.
- Версии PyQt, AppKit, Tauri, npm и Cargo синхронизированы на `2.0.2`.

## Packaging

Иконка включается внутрь `GigaAMLiquid.app/Contents/Resources/` до ad-hoc
подписи приложения. Оба Liquid-архива используют один и тот же проверенный
bundle, поэтому исправление применяется к online- и offline-вариантам.

---

This patch release adds the missing application icon to the native macOS
GigaAMLiquid bundle, configures `CFBundleIconFile`, verifies the icon before
publishing, and synchronizes desktop version metadata to 2.0.2.
