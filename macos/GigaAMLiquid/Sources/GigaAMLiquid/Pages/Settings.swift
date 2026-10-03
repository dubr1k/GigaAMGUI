import AppKit

/// The Настройки page: the category rail and each category's panel.
extension AppController {
    func buildSettings(into content: NSStackView) {
        settingsCategoryButtons.removeAll()
        let names = ["Общие", "Модели", "Обработка", "Диаризация", "Аудио", "LLM", "API", "Пути", "О приложении"]
        let stored = defaults.string(forKey: "settings.category") ?? "Общие"
        let selected = names.contains(stored) ? stored : "Общие"
        let categories = GlassView(radius: 18)
        let rail = vertical([], spacing: 8)
        for name in names {
            rail.addArrangedSubview(settingsCategoryButton(name, selected: name == selected))
        }
        embed(rail, in: categories.contentView, inset: 18, top: 30)
        categories.widthAnchor.constraint(equalToConstant: 260).isActive = true
        let detail = NSView()
        settingsDetail = detail
        content.addArrangedSubview(stretchy(fillRow([categories, detail], spacing: 16)))
        applySettingsCategorySelection(selected)
    }

    func settingsPage(_ category: String) -> NSView {
        let surface = GlassView(radius: 28)
        let body = vertical([label(category, size: 24, weight: .medium, color: Palette.ink)], spacing: 24)
        switch category {
        case "Общие":
            body.addArrangedSubview(wrappedLabel("Язык и оформление приложения. Изменения сохраняются автоматически.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Язык интерфейса", control: popup(["Русский", "English"], key: "settings.language")))
            body.addArrangedSubview(settingsField("Тема", control: popup(["Системная", "Светлая", "Тёмная"], key: "settings.theme")))
        case "Модели":
            body.addArrangedSubview(wrappedLabel("Модель распознавания и устройство вычислений.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.spacing = 16
            body.addArrangedSubview(settingsField("ASR backend", control: popup(["auto", "mlx", "onnx", "pytorch"], key: "settings.backend")))
            body.addArrangedSubview(settingsField("Модель", control: popup(["v3_e2e_rnnt", "multilingual_ctc", "multilingual_large_ctc"], key: "settings.model")))
            body.addArrangedSubview(settingsField("ONNX provider", control: popup(["auto", "cpu", "cuda", "tensorrt", "coreml", "directml"], key: "settings.onnxProvider")))
            body.addArrangedSubview(settingsField("Устройство", control: inactive(popup(["Auto / GPU", "CPU", "GPU"], key: "settings.device"))))
            body.addArrangedSubview(inactive(toggleRow("Fallback на CPU", key: "settings.cpuFallback", defaultValue: false)))
            body.addArrangedSubview(wrappedLabel("Устройство и fallback выбирает backend. ONNX provider применяется только к ONNX. Язык определяется моделью.", size: 12, color: Palette.muted))
        case "Обработка":
            body.addArrangedSubview(wrappedLabel("Разбиение транскрипции и оформление субтитров.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(toggleRow("Разбивать по предложениям", key: "subtitle.sentences", defaultValue: true))
            body.addArrangedSubview(equalColumns([
                settingsField("Строк в блоке", control: popup(["2", "1", "3", "4"], key: "subtitle.lines")),
                settingsField("Символов в строке", control: popup(["64", "42", "80"], key: "subtitle.characters"))
            ], spacing: 20))
            body.addArrangedSubview(inactive(toggleRow("Показывать спикера", key: "settings.showSpeaker", defaultValue: true)))
            body.addArrangedSubview(wrappedLabel("Метки спикеров добавляются автоматически при включённой диаризации.", size: 12, color: Palette.muted))
        case "Диаризация":
            body.addArrangedSubview(wrappedLabel("Разделение речи по спикерам и доступ к моделям Hugging Face.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(toggleRow("Диаризация", key: "settings.diarization", defaultValue: false))
            body.addArrangedSubview(settingsField("Движок диаризации", control: popup(["pyannote", "onnx", "sortformer"], key: "settings.diarizationEngine")))
            body.addArrangedSubview(settingsField("Кол-во спикеров", control: speakerCountPopup()))
            body.addArrangedSubview(wrappedLabel("Sortformer определяет спикеров автоматически (до 4). Pyannote и ONNX принимают известное число спикеров.", size: 12, color: Palette.muted))
            let value = SecureStore.string(for: "hfToken") ?? ""
            let token = RoundedSecureTextField(string: value)
            token.cell = CenteredSecureTextCell(textCell: value)
            token.isEditable = true
            token.isSelectable = true
            token.isBezeled = false
            token.drawsBackground = false
            token.wantsLayer = true
            token.layer?.cornerRadius = 18
            token.layer?.masksToBounds = true
            token.placeholderString = L10n.text("Не настроен")
            token.identifier = NSUserInterfaceItemIdentifier("settings.hfToken")
            token.target = self
            token.delegate = self
            token.action = #selector(textChanged(_:))
            body.addArrangedSubview(settingsField("HF Token", control: token))
            body.addArrangedSubview(wrappedLabel("Pyannote требует токен и принятые лицензии моделей Hugging Face. Пустое поле использует HF_TOKEN из окружения.", size: 12, color: Palette.muted))
        case "Аудио":
            body.addArrangedSubview(wrappedLabel("Параметры аудиосигнала для обработки.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Подготовка аудио", control: popup(["auto", "off", "light", "denoise"], key: "processing.preprocessing")))
            body.addArrangedSubview(settingsField("Частота дискретизации", control: inactive(popup(["16000 Hz"], key: "settings.sampleRate"))))
            body.addArrangedSubview(wrappedLabel("Частоту 16000 Hz задаёт конвертер. auto — автоматическая подготовка, off — без неё, light — лёгкая обработка, denoise — шумоподавление.", size: 12, color: Palette.muted))
        case "LLM":
            body.addArrangedSubview(wrappedLabel("Провайдер постобработки и вопросов ассистенту в Live. API — OpenAI-совместимый или Anthropic адрес; остальные — локальные CLI.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.spacing = 16
            body.addArrangedSubview(settingsField("Провайдер", control: popup(Self.llmProviders, key: "llm.provider")))
            body.addArrangedSubview(settingsField("API URL", control: editableText(defaults.string(forKey: "llm.apiUrl") ?? "", key: "llm.apiUrl", placeholder: "https://api.openai.com/v1")))
            body.addArrangedSubview(settingsField("API Key", control: secureField(account: "llmApiKey", key: "llm.apiKey")))
            body.addArrangedSubview(equalColumns([
                settingsField("Модель", control: editableText(defaults.string(forKey: "llm.model") ?? "", key: "llm.model", placeholder: "gpt-4.1-mini")),
                settingsField("Temperature", control: editableText(defaults.string(forKey: "llm.temperature") ?? "", key: "llm.temperature", placeholder: "0.2"))
            ], spacing: 20))
            body.addArrangedSubview(divider())
            let rescan = button("Пересканировать", action: #selector(rescanLLMTools(_:)), height: 30)
            rescan.setAccessibilityIdentifier("llm.rescan")
            llmRescanButton = rescan
            body.addArrangedSubview(horizontal([label("Инструменты", size: 17, weight: .medium, color: Palette.ink), flexibleSpace(), rescan], spacing: 12))
            body.addArrangedSubview(wrappedLabel("Пустой путь — автопоиск по PATH и типичным каталогам (homebrew, npm, bun, nvm). Приложение из Finder не видит PATH терминала — поиск ведёт Python-сервис.", size: 12, color: Palette.muted))
            llmToolRows = [:]
            for tool in Self.llmCliProviders {
                body.addArrangedSubview(llmToolRow(tool))
            }
            body.addArrangedSubview(divider())
            body.addArrangedSubview(label("Дополнительные параметры", size: 17, weight: .medium, color: Palette.ink))
            body.addArrangedSubview(wrappedLabel("Обычно не нужны — всё работает с пустыми полями. «Аргументы» — флаги командной строки, которые добавляются к запуску CLI как есть (как в терминале): например, уровень рассуждений или профиль. «Provider» у Pi и oh-my-pi — внутренний поставщик модели (anthropic, openai, google…), если нужно переопределить настроенный в самом CLI. Модель берётся из общего поля «Модель» выше.", size: 12, color: Palette.muted))
            for tool in Self.llmCliProviders {
                let argsField = editableText(defaults.string(forKey: "llm.\(tool.prefix)Args") ?? "", key: "llm.\(tool.prefix)Args", placeholder: Self.llmArgsExamples[tool.prefix] ?? "")
                argsField.toolTip = L10n.text("Флаги командной строки, добавляются к запуску как есть. Пример: ") + (Self.llmArgsExamples[tool.prefix] ?? "")
                var fields: [NSView] = [settingsField("\(tool.name) — аргументы", control: argsField)]
                if tool.hasProvider {
                    let providerField = editableText(defaults.string(forKey: "llm.\(tool.prefix)Provider") ?? "", key: "llm.\(tool.prefix)Provider", placeholder: "по умолчанию из конфига CLI")
                    providerField.toolTip = L10n.text("Внутренний поставщик модели: anthropic, openai, google, openrouter… Пусто — как настроено в ") + tool.name
                    fields.append(settingsField("\(tool.name) — поставщик модели", control: providerField))
                }
                body.addArrangedSubview(fields.count == 1 ? fields[0] : equalColumns(fields, spacing: 20))
            }
            body.addArrangedSubview(divider())
            body.addArrangedSubview(wrappedLabel("«Другое» — любая своя команда. Промпт передаётся последним аргументом и одновременно в stdin; напишите {stdin} в аргументах, чтобы передавать только через stdin. Ответ читается из stdout.", size: 12, color: Palette.muted))
            body.addArrangedSubview(equalColumns([
                settingsField("Другое — команда", control: editableText(defaults.string(forKey: "llm.otherPath") ?? "", key: "llm.otherPath", placeholder: "/usr/local/bin/my-llm")),
                settingsField("Другое — аргументы", control: editableText(defaults.string(forKey: "llm.otherArgs") ?? "", key: "llm.otherArgs", placeholder: "--model x {stdin}"))
            ], spacing: 20))
            let allowTools = toggleRow("Разрешить инструменты и сессии агента", key: "llm.allowTools", defaultValue: false)
            allowTools.toolTip = L10n.text("Выключено: claude/codex/opencode/pi/omp запускаются как чистый запрос к модели — без доступа к файлам и без записи в историю сессий агента.")
            body.addArrangedSubview(allowTools)
            body.addArrangedSubview(wrappedLabel("Выключено — CLI работает как чистый запрос к модели: агент не читает файлы и не сохраняет сессию. Включайте, только если хотите, чтобы он мог пользоваться своими инструментами. Ключ API хранится в Связке ключей.", size: 12, color: Palette.muted))
            refreshLLMToolRows()
            refreshLLMTools(fresh: false)
        case "API":
            body.addArrangedSubview(wrappedLabel("Отдельный REST API запускается из api.py. Нативный клиент не запускает сервер и не проверяет его доступность.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(label("Не подключён", size: 17, weight: .medium, color: Palette.body))
            body.addArrangedSubview(settingsField("Адрес в примерах · не настройка подключения", control: label("http://127.0.0.1:8000", size: 15, color: Palette.ink)))
            body.addArrangedSubview(horizontal([button("Скопировать URL", action: #selector(copyAPIURL(_:))), button("Открыть документацию", action: #selector(openDocumentation(_:)))], spacing: 12))
            body.addArrangedSubview(wrappedLabel("Для запросов требуется заголовок Authorization: Bearer <ключ> (X-API-Key принимается как устаревший вариант). Примеры Python, cURL и JavaScript доступны в разделе API основного меню.", size: 13, color: Palette.body))
        case "Пути":
            body.addArrangedSubview(wrappedLabel("Хранение результатов и визуальные эффекты приложения.", size: 13, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Папка результатов", control: editableText(outputPathText, key: "output.path", placeholder: "Рядом с исходным файлом")))
            body.addArrangedSubview(toggleRow("Liquid Glass", key: "settings.liquidGlass", defaultValue: true))
        case "О приложении":
            body.spacing = 18
            let releaseVersion = (Bundle.main.object(forInfoDictionaryKey: "GigaAMReleaseVersion") as? String)
                ?? (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String)
                ?? "—"
            body.addArrangedSubview(label("GigaAM v3 Transcriber", size: 22, weight: .medium, color: Palette.ink))
            body.addArrangedSubview(wrappedLabel("Транскрибация русской речи из аудио и видео на базе GigaAM-v3.", size: 14, color: Palette.body))
            body.addArrangedSubview(divider())
            body.addArrangedSubview(settingsField("Версия приложения", control: label(releaseVersion, size: 15, color: Palette.ink)))
            body.addArrangedSubview(wrappedLabel("Импорт медиа, распознавание, live-захват и LLM используют встроенные Python-сервисы проекта; звук захватывает само приложение.", size: 13, color: Palette.body))
            body.addArrangedSubview(label("Разработчики приложения", size: 12, color: Palette.muted))
            let developers = ["dubr1k", "Baggrisha"].map { name in
                let link = button(name, action: #selector(openDeveloper(_:)))
                link.identifier = NSUserInterfaceItemIdentifier(name)
                link.toolTip = "https://github.com/\(name)"
                return link
            }
            body.addArrangedSubview(horizontal(developers + [flexibleSpace()], spacing: 12))
            body.addArrangedSubview(settingsField("Модель распознавания", control: label("SaluteDevices / GigaAM", size: 15, color: Palette.ink)))
            body.addArrangedSubview(centered(button("Проект на GitHub", action: #selector(openProject(_:)))))
        default: break
        }
        // The panel is as tall as the window allows; a body taller than that (the LLM
        // tools table, or any page in a short window) scrolls inside the panel
        // instead of stretching the page. 24 + 6 pt body inset = 30 pt.
        embed(scrollable(body), in: surface.contentView, inset: 24, fillHeight: true)
        return surface
    }

    func settingsCategoryButton(_ title: String, selected: Bool) -> NSButton {
        let button = NavigationRowButton(title: L10n.text(title), target: self, action: #selector(selectSettingsCategory(_:)))
        button.identifier = NSUserInterfaceItemIdentifier("settings.category.\(title)")
        button.isBordered = false
        button.bezelStyle = .regularSquare
        button.alignment = .left
        button.font = NSFont.systemFont(ofSize: 14, weight: .medium)
        button.wantsLayer = true
        button.layer?.cornerRadius = 9
        button.widthAnchor.constraint(equalToConstant: 224).isActive = true
        button.heightAnchor.constraint(equalToConstant: 38).isActive = true
        settingsCategoryButtons[title] = button
        button.contentTintColor = selected ? Palette.ink : Palette.body
        button.layer?.backgroundColor = (selected ? Palette.selection : .clear).cgColor
        return button
    }

    func applySettingsCategorySelection(_ selectedTitle: String) {
        guard let settingsDetail, settingsCategoryButtons[selectedTitle] != nil else { return }
        window.makeFirstResponder(nil)
        defaults.set(selectedTitle, forKey: "settings.category")
        for (title, button) in settingsCategoryButtons {
            let isSelected = title == selectedTitle
            button.contentTintColor = isSelected ? Palette.blue : Palette.body
            button.layer?.backgroundColor = (isSelected ? Palette.selection : .clear).cgColor
        }
        settingsDetail.subviews.forEach { $0.removeFromSuperview() }
        embed(settingsPage(selectedTitle), in: settingsDetail, inset: 0, fillHeight: true)
    }

    @objc func openProject(_ sender: Any?) {
        guard let url = URL(string: "https://github.com/dubr1k/GigaAMGUI") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc func openDeveloper(_ sender: NSButton) {
        guard let name = sender.identifier?.rawValue,
              ["Baggrisha", "dubr1k"].contains(name),
              let url = URL(string: "https://github.com/\(name)") else { return }
        NSWorkspace.shared.open(url)
    }

    @objc func selectSettingsCategory(_ sender: NSButton) {
        guard let rawValue = sender.identifier?.rawValue,
              rawValue.hasPrefix("settings.category.") else { return }
        applySettingsCategorySelection(String(rawValue.dropFirst("settings.category.".count)))
        refreshProcessingControls()
    }
}
