import Foundation

/// The client's pages: navigation, titles and symbols. Titles are Russian
/// source strings translated through L10n.
enum Page: String, CaseIterable {
    case processing, result, live, llm, api, history, settings

    var title: String {
        switch self {
        case .processing: return "Обработка"
        case .result: return "Результат обработки"
        case .live: return "Live"
        case .llm: return "LLM"
        case .api: return "API"
        case .history: return "Журнал"
        case .settings: return "Настройки"
        }
    }

    var subtitle: String {
        switch self {
        case .processing: return "Загрузите аудио или видео и настройте параметры."
        case .result: return "Транскрипция и сохранённые файлы обработки."
        case .live: return "Захват в реальном времени и мгновенная транскрипция."
        case .llm: return "Постобработка транскрипций и работа с пользовательскими промптами."
        case .api: return "Рабочая документация и примеры запросов внутри приложения."
        case .history: return "История обработок, статусов и готовых результатов."
        case .settings: return "Параметры приложения, обработки, моделей и путей хранения."
        }
    }

    var navigationTitle: String {
        switch self {
        case .processing: return "Обработка"
        case .result: return "Результат"
        case .live: return "Live"
        case .llm: return "LLM"
        case .api: return "API"
        case .history: return "Журнал"
        case .settings: return "Настройки"
        }
    }

    var symbol: String {
        switch self {
        case .processing: return "waveform"
        case .result: return "doc.text"
        case .live: return "mic"
        case .llm: return "sparkles"
        case .api: return "chevron.left.forwardslash.chevron.right"
        case .history: return "clock"
        case .settings: return "gearshape"
        }
    }
}
