import AppKit

let application = NSApplication.shared
private let delegate = AppController()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
