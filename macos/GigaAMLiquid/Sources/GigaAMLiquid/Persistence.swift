import AppKit
import GigaAMLiquidCore

/// Settings persistence: every control writes its value to UserDefaults under
/// its identifier (secrets go to the Keychain through SecureStore).
extension AppController {
    func option(_ key: String, values: [String]) -> String {
        SettingsSchema.choice(defaults.string(forKey: key), in: values)
    }

    func enabledOption(_ key: String, defaultValue: Bool) -> Bool {
        defaults.object(forKey: key) == nil ? defaultValue : defaults.bool(forKey: key)
    }

    @objc func popupChanged(_ sender: NSPopUpButton) {
        guard let key = sender.identifier?.rawValue, let value = sender.titleOfSelectedItem else { return }
        if key == "live.microphone" {
            // Titles are device names; persist the stable device id instead.
            defaults.set(liveDeviceIDs.indices.contains(sender.indexOfSelectedItem) ? liveDeviceIDs[sender.indexOfSelectedItem] : "default", forKey: key)
            return
        }
        defaults.set(value, forKey: key)
        if key == "settings.theme" || key == "settings.language" { rebuildInterface() }
        if key == "settings.diarizationEngine" { resetManualSpeakerCountIfUnavailable() }
        if key == "llm.provider" { refreshLLMProviderStatus() }
        refreshProcessingControls()
    }

    @objc func switchChanged(_ sender: NSButton) {
        guard let key = sender.identifier?.rawValue else { return }
        defaults.set(sender.state == .on, forKey: key)
        if key == "settings.liquidGlass" { rebuildInterface() }
        if key == "settings.diarization" { resetManualSpeakerCountIfUnavailable() }
        refreshProcessingControls()
    }

    @objc func textChanged(_ sender: NSTextField) {
        guard let key = sender.identifier?.rawValue else { return }
        if key == "settings.hfToken" {
            do {
                try SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "hfToken")
            } catch {
                showNotice("Не удалось сохранить HF Token", error.localizedDescription)
            }
        } else if key == "llm.apiKey" {
            do {
                try SecureStore.set(sender.stringValue.trimmingCharacters(in: .whitespacesAndNewlines), for: "llmApiKey")
            } catch {
                showNotice("Не удалось сохранить API Key", error.localizedDescription)
            }
        } else {
            defaults.set(sender.stringValue, forKey: key)
        }
        if key == "output.path" { refreshProcessingControls() }
        if key == "live.sessionRoot", liveJob == nil {
            liveSessionDir = nil
            refreshLiveFolderLabel()
        }
        // An edited CLI path invalidates its badge: re-probe just that tool.
        if key.hasPrefix("llm."), key.hasSuffix("Path"),
           let tool = Self.llmCliProviders.first(where: { "llm.\($0.prefix)Path" == key }) {
            checkLLMTool(tool.name)
        }
    }
}

/// Multi-line editors (LLM source and prompt) persist on every change.
extension AppController: NSTextViewDelegate {
    func textDidChange(_ notification: Notification) {
        guard let editor = notification.object as? NSTextView, let key = editor.identifier?.rawValue else { return }
        defaults.set(editor.string, forKey: key)
    }
}

/// Single-line fields (and the page search field) report every edit here.
extension AppController: NSSearchFieldDelegate {
    /// No controlTextDidEndEditing: every edit is already persisted here. Saving
    /// again on end-editing let a stale field win — show(page:) removes the old,
    /// still-focused field only after the new page has read the stored value, and
    /// AppKit ends its editing on removal, so a folder picked with «Изменить» was
    /// overwritten by the old empty text while the new field still displayed it.
    func controlTextDidChange(_ notification: Notification) {
        guard let control = notification.object as? NSTextField else { return }
        if control.identifier != nil { textChanged(control) }
        if let field = control as? NSSearchField, field === searchField { updateSearchResults(for: field) }
    }
}
