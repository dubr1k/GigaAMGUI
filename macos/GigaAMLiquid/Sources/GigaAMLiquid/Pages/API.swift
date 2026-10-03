import AppKit

/// The API page: request examples for the separate REST API (api.py), which
/// this client documents but does not run.
extension AppController {
    func buildAPI(into content: NSStackView) {
        let examples = card("Примеры запросов")
        let body = contentStack(examples)
        body.spacing = 10
        body.addArrangedSubview(label("Статус API", size: 12, color: Palette.muted))
        body.addArrangedSubview(label("○  Не подключён", size: 14, weight: .medium, color: Palette.body))
        body.addArrangedSubview(label("http://127.0.0.1:8000  •  адрес примера", size: 14, color: Palette.body))
        body.addArrangedSubview(horizontal([
            button("Скопировать URL", action: #selector(copyAPIURL(_:))),
            button("Открыть документацию", primary: true, action: #selector(openDocumentation(_:))),
            flexibleSpace()
        ], spacing: 12))
        let language = PillSelector(labels: ["Python", "cURL", "JavaScript"], target: self, action: #selector(apiLanguageChanged(_:)))
        language.selectedSegment = ["Python", "cURL", "JavaScript"].firstIndex(of: defaults.string(forKey: "api.exampleLanguage") ?? "Python") ?? 0
        language.heightAnchor.constraint(equalToConstant: 32).isActive = true
        body.addArrangedSubview(language)
        let code = codeView(apiExample(language.selectedSegment))
        apiCodeText = code.documentView as? NSTextView
        code.heightAnchor.constraint(greaterThanOrEqualToConstant: 180).isActive = true
        stretchy(code)
        body.addArrangedSubview(code)
        body.addArrangedSubview(horizontal([button("Копировать пример", action: #selector(copyCode(_:))), flexibleSpace()], spacing: 12))
        body.addArrangedSubview(wrappedLabel("Пример запроса, не ответ сервера. Этот клиент не запускает API.", size: 12, color: Palette.muted))
        body.bottomAnchor.constraint(equalTo: examples.bottomAnchor, constant: -16).isActive = true
        let docs = card("Документация")
        for title in ["Быстрый старт", "Эндпоинты", "Параметры", "Примеры", "Форматы ответов", "Скачать OpenAPI (JSON)"] {
            let row = documentationRow(title)
            if title == "Скачать OpenAPI (JSON)" {
                row.isEnabled = false
                row.toolTip = L10n.text("Схема доступна после подключения API.")
            }
            contentStack(docs).addArrangedSubview(row)
        }
        docs.widthAnchor.constraint(equalToConstant: 292).isActive = true
        content.addArrangedSubview(stretchy(fillRow([examples, docs], spacing: 16)))
    }

    func apiExample(_ language: Int) -> String {
        switch language {
        case 1:
            return """
            # Пример: задайте GIGAAM_API_KEY и путь к файлу
            curl http://127.0.0.1:8000/v1/audio/transcriptions \\
              -H "Authorization: Bearer $GIGAAM_API_KEY" \\
              -F file=@meeting.mp3 -F model=whisper-1 -F response_format=verbose_json
            """
        case 2:
            return """
            // Пример для Node.js; задайте GIGAAM_API_KEY
            import OpenAI from "openai";
            import fs from "node:fs";

            const client = new OpenAI({
              baseURL: "http://127.0.0.1:8000/v1",
              apiKey: process.env.GIGAAM_API_KEY,
            });
            const result = await client.audio.transcriptions.create({
              model: "whisper-1",
              file: fs.createReadStream("meeting.mp3"),
              response_format: "verbose_json",
            });
            console.log(result.text);
            """
        default:
            return """
            # Пример: задайте GIGAAM_API_KEY и путь к файлу
            import os
            from openai import OpenAI

            client = OpenAI(
                base_url="http://127.0.0.1:8000/v1",
                api_key=os.environ["GIGAAM_API_KEY"],
            )
            with open("meeting.mp3", "rb") as audio:
                result = client.audio.transcriptions.create(
                    model="whisper-1", file=audio,
                    response_format="verbose_json")
            print(result.text)
            """
        }
    }

    func documentationRow(_ title: String) -> NSButton {
        let button = button(title, action: #selector(openDocumentation(_:)), height: 46)
        button.identifier = NSUserInterfaceItemIdentifier(title)
        button.alignment = .left
        button.image = NSImage(systemSymbolName: "chevron.right", accessibilityDescription: nil)
        button.imagePosition = .imageTrailing
        button.font = NSFont.systemFont(ofSize: 14)
        return button
    }

    @objc func openDocumentation(_ sender: NSButton) {
        let title = sender.identifier?.rawValue ?? "Открыть документацию"
        let detail: String
        switch title {
        case "Параметры":
            detail = "POST /v1/audio/transcriptions принимает multipart-поле file. Основные поля: model (whisper-1 и другие алиасы), response_format (json/text/srt/vtt/verbose_json/diarized_json), stream, timestamp_granularities[]. Расширения GigaAM: diarize, diarization_backend, num_speakers, asr_backend, onnx_provider, audio_preprocessing. Требуется заголовок Authorization: Bearer <ключ>."
        case "Форматы ответов":
            detail = "Ответ возвращается синхронно, сразу в запросе. Формат задаётся response_format: json, text, srt, vtt, verbose_json или diarized_json. При stream=true json/verbose_json приходят по SSE: во время обработки — комментарии прогресса, дельты текста — после распознавания всего файла."
        case "Эндпоинты":
            detail = "POST /v1/audio/transcriptions — распознать файл.\nGET /v1/models — доступные модели.\nGET /health — состояние сервиса."
        case "Примеры":
            detail = "Выберите Python, cURL или JavaScript слева. Кнопка «Копировать пример» копирует показанный код. Задайте свой API-ключ и путь к аудиофайлу."
        default:
            detail = "Отдельный REST API запускается командой python api.py. REST API совместим с OpenAI Audio API: укажите base_url http://127.0.0.1:8000/v1 в любом клиенте OpenAI. Примеры на этой странице соответствуют POST /v1/audio/transcriptions.\n\nЭтот нативный клиент не запускает сервер и не проверял его доступность."
        }
        showNotice(title, detail)
    }

    @objc func copyAPIURL(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("http://127.0.0.1:8000", forType: .string)
    }

    @objc func copyCode(_ sender: Any?) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(apiCodeText?.string ?? "", forType: .string)
    }

    @objc func apiLanguageChanged(_ sender: PillSelector) {
        let names = ["Python", "cURL", "JavaScript"]
        defaults.set(names[sender.selectedSegment], forKey: "api.exampleLanguage")
        apiCodeText?.string = apiExample(sender.selectedSegment)
    }
}
