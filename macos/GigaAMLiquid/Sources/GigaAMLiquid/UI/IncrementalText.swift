import AppKit

/// Keeps the Live transcript view in step with its finals and drafts by editing
/// only what changed. Re-setting the whole string on every draft (several times
/// a second, two sources) re-laid out an hour-long transcript each time.
final class LiveTranscriptRenderer {
    private var renderedFinals = 0
    private var renderedRevision = -1
    /// UTF-16 length of the finals part at the start of the storage.
    private var finalsLength = 0
    private var showingPlaceholder = false

    /// `revision` changes whenever a final already shown is replaced or the list
    /// is reset; otherwise finals only grow and are appended.
    func render(finals: [String], revision: Int, drafts: [String], placeholder: String,
                into storage: NSTextStorage, attributes: [NSAttributedString.Key: Any]) {
        storage.beginEditing()
        defer { storage.endEditing() }
        if finals.isEmpty && drafts.isEmpty {
            storage.setAttributedString(NSAttributedString(string: placeholder, attributes: attributes))
            reset(placeholder: true)
            return
        }
        if showingPlaceholder || revision != renderedRevision || finals.count < renderedFinals {
            let text = finals.joined(separator: "\n")
            storage.setAttributedString(NSAttributedString(string: text, attributes: attributes))
            finalsLength = (text as NSString).length
            renderedFinals = finals.count
            renderedRevision = revision
            showingPlaceholder = false
        } else if finals.count > renderedFinals {
            let added = (renderedFinals == 0 ? "" : "\n") + finals[renderedFinals...].joined(separator: "\n")
            storage.replaceCharacters(in: NSRange(location: finalsLength, length: storage.length - finalsLength),
                                      with: NSAttributedString(string: added, attributes: attributes))
            finalsLength += (added as NSString).length
            renderedFinals = finals.count
        }
        let tail = drafts.isEmpty ? "" : (finals.isEmpty ? "" : "\n") + drafts.joined(separator: "\n")
        storage.replaceCharacters(in: NSRange(location: finalsLength, length: storage.length - finalsLength),
                                  with: NSAttributedString(string: tail, attributes: attributes))
    }

    /// The view was rebuilt (page shown again): render everything on the next call.
    func reset(placeholder: Bool = false) {
        renderedFinals = 0
        renderedRevision = -1
        finalsLength = 0
        showingPlaceholder = placeholder
    }
}

extension NSTextView {
    /// Appends streamed text without re-setting (and re-laying out) the whole view,
    /// in the view's own font and colour.
    func appendStreamed(_ text: String) {
        guard let storage = textStorage else { string += text; return }
        storage.append(NSAttributedString(string: text, attributes: typingAttributes))
    }
}
