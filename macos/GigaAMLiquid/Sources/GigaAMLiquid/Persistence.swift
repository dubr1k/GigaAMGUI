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

    /// Fields whose value lives in the Keychain: control identifier → (account, failure title).
    static let secretFields: [String: (account: String, failure: String)] = [
        "settings.hfToken": ("hfToken", "Не удалось сохранить HF Token"),
        "llm.apiKey": ("llmApiKey", "Не удалось сохранить API Key"),
    ]

    /// Return in a field: the edit is complete.
    @objc func textChanged(_ sender: NSTextField) {
        recordText(sender)
        commitSecret(sender)
    }

    /// Every keystroke. Plain settings go to UserDefaults at once; a secret only
    /// waits for the end of editing — a Keychain write per keystroke meant a
    /// SecItemUpdate per character and, if the Keychain refused, an alert per key.
    func recordText(_ sender: NSTextField) {
        guard let key = sender.identifier?.rawValue else { return }
        if Self.secretFields[key] != nil {
            pendingSecrets[key] = sender.stringValue
            return
        }
        defaults.set(sender.stringValue, forKey: key)
        if key == "output.path" { refreshProcessingControls() }
        if key == "live.sessionRoot", liveJob == nil {
            liveSessionDir = nil
            refreshLiveFolderLabel()
        }
        // An edited CLI path invalidates its badge: re-probe that tool once typing pauses.
        if key.hasPrefix("llm."), key.hasSuffix("Path"),
           let tool = Self.llmCliProviders.first(where: { "llm.\($0.prefix)Path" == key }) {
            scheduleLLMToolCheck(tool.name)
        }
    }

    /// Writes a secret the user typed in this field, once. Only an edit made in
    /// the field is written: ending the editing of an untouched field (it is
    /// removed when a page is rebuilt) never writes its possibly stale text.
    func commitSecret(_ sender: NSTextField) {
        guard let key = sender.identifier?.rawValue, let secret = Self.secretFields[key],
              let value = pendingSecrets.removeValue(forKey: key) else { return }
        do {
            try SecureStore.set(value.trimmingCharacters(in: .whitespacesAndNewlines), for: secret.account)
        } catch {
            showNotice(secret.failure, error.localizedDescription)
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
    /// Plain fields persist here, on every edit, and never again on end-editing:
    /// saving on end-editing let a stale field win — show(page:) removed the old,
    /// still-focused field only after the new page had read the stored value, and
    /// AppKit ends its editing on removal, so a folder picked with «Изменить» was
    /// overwritten by the old empty text while the new field still displayed it.
    func controlTextDidChange(_ notification: Notification) {
        guard let control = notification.object as? NSTextField else { return }
        if control.identifier != nil { recordText(control) }
        if let field = control as? NSSearchField, field === searchField { updateSearchResults(for: field) }
    }

    /// Only secrets are written here, and only what was typed in this field.
    func controlTextDidEndEditing(_ notification: Notification) {
        guard let control = notification.object as? NSTextField else { return }
        commitSecret(control)
    }
}
