import AppKit
import AVFoundation
import ApplicationServices

final class Dictation: NSObject, NSApplicationDelegate {
    var status: NSStatusItem!
    var engine: AVAudioEngine?
    var worker: Process?
    var input: Pipe!
    var pending = Data()
    var ready = false
    var inFlight = false
    var recording = false
    var finishing = false
    var session = UUID().uuidString
    var target: NSRunningApplication?
    var recordingURL: URL?
    var audioFile: FileHandle?
    var sampleRate = 48000.0
    var timer: Timer?
    var preview = ""
    var markerShown = false
    var cancelledFiles: [URL] = []
    var eventTap: CFMachPort?
    let audioQueue = DispatchQueue(label: "dictation.audio")
    let marker = "🎙️  "
    let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share/whisper-dictation")

    func applicationDidFinishLaunching(_ notification: Notification) {
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        setStatus("Loading…", symbol: "mic")
        let menu = NSMenu()
        menu.addItem(withTitle: "Record / stop", action: #selector(toggle), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Quit", action: #selector(quit), keyEquivalent: "").target = self
        status.menu = menu
        let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        _ = AXIsProcessTrustedWithOptions(options)
        installEscapeHandler()
        startWorker()
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        toggle(); return false
    }
    func setStatus(_ title: String, symbol: String) {
        status.button?.title = title
        status.button?.image = NSImage(systemSymbolName: symbol, accessibilityDescription: title)
        status.button?.contentTintColor = recording ? .systemRed : nil
    }
    func installEscapeHandler() {
        let callback: CGEventTapCallBack = { _, type, event, context in
            guard let context = context else { return Unmanaged.passUnretained(event) }
            let owner = Unmanaged<Dictation>.fromOpaque(context).takeUnretainedValue()
            if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
                if let tap = owner.eventTap { CGEvent.tapEnable(tap: tap, enable: true) }
                return Unmanaged.passUnretained(event)
            }
            if type == .keyDown && event.getIntegerValueField(.keyboardEventKeycode) == 53 && (owner.recording || owner.finishing) {
                owner.cancel(); return nil
            }
            return Unmanaged.passUnretained(event)
        }
        eventTap = CGEvent.tapCreate(tap: .cgSessionEventTap, place: .headInsertEventTap,
                                    options: .defaultTap, eventsOfInterest: 1 << CGEventType.keyDown.rawValue,
                                    callback: callback, userInfo: Unmanaged.passUnretained(self).toOpaque())
        if let tap = eventTap, let source = CFMachPortCreateRunLoopSource(kCFAllocatorDefault, tap, 0) {
            CFRunLoopAddSource(CFRunLoopGetMain(), source, .commonModes)
            CGEvent.tapEnable(tap: tap, enable: true)
        }
    }
    func startWorker() {
        let p = Process()
        p.executableURL = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share/uv/tools/mlx-whisper/bin/python")
        p.arguments = [root.appendingPathComponent("worker.py").path, "mlx-community/whisper-large-v3-turbo"]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        env["HF_HUB_OFFLINE"] = "1"
        p.environment = env
        input = Pipe()
        let output = Pipe()
        p.standardInput = input; p.standardOutput = output
        let log = root.appendingPathComponent("worker.log")
        FileManager.default.createFile(atPath: log.path, contents: nil)
        p.standardError = try? FileHandle(forWritingTo: log)
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if !data.isEmpty { DispatchQueue.main.async { self?.consume(data) } }
        }
        p.terminationHandler = { [weak self] _ in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.cancel(); self.ready = false
                self.setStatus("Error", symbol: "exclamationmark.triangle")
            }
        }
        do { try p.run(); worker = p } catch { alert(error.localizedDescription) }
    }
    func consume(_ data: Data) {
        pending.append(data)
        while let newline = pending.firstIndex(of: 10) {
            let line = pending.prefix(upTo: newline)
            pending.removeSubrange(...newline)
            guard let result = try? JSONSerialization.jsonObject(with: line) as? [String: Any] else { continue }
            if result["ready"] as? Bool == true {
                ready = true; setStatus("", symbol: "mic"); continue
            }
            inFlight = false
            for url in cancelledFiles { try? FileManager.default.removeItem(at: url) }
            cancelledFiles.removeAll()
            guard result["session"] as? String == session else { continue }
            if let error = result["error"] as? String {
                cancel(); alert(error); continue
            }
            guard sameTarget() else {
                let text = result["text"] as? String ?? ""
                cancel()
                if !text.isEmpty { copy(text) }
                alert("Focus changed. Dictation stopped; the latest transcript is on the clipboard.")
                continue
            }
            replacePreview(result["text"] as? String ?? "")
            if result["final"] as? Bool == true {
                // Wait for the terminal to consume the paste before moving its cursor.
                let completedSession = session
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                    guard self.finishing, self.session == completedSession, self.sameTarget() else { return }
                    self.key(115) // Home: microphone and its following space are first.
                    for _ in self.marker { self.key(117) } // Remove the complete recording marker.
                    self.key(119) // End: send the completed single-line transcript.
                    if !self.preview.isEmpty { self.key(36) }
                    self.markerShown = false; self.preview = ""; self.finishing = false
                    self.cleanup(); self.setStatus("", symbol: "mic")
                }
            } else if finishing {
                request(final: true)
            }
        }
    }
    @objc func toggle() {
        guard ready else { NSSound.beep(); return }
        if recording { stop(); return }
        if finishing || inFlight { NSSound.beep(); return }
        guard AXIsProcessTrusted() else { alert("Enable Whisper Dictation in Privacy & Security → Device Control and Data Access."); return }
        if eventTap == nil { installEscapeHandler() }
        guard eventTap != nil else { alert("Restart Whisper Dictation after granting keyboard access so Escape can cancel recordings."); return }
        target = NSWorkspace.shared.frontmostApplication
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async {
                if granted { self.record() } else { self.alert("Allow microphone access for Whisper Dictation in System Settings.") }
            }
        }
    }
    func record() {
        guard sameTarget() else { return }
        session = UUID().uuidString; preview = ""
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("whisper-\(session).f32")
        do {
            FileManager.default.createFile(atPath: url.path, contents: nil)
            let file = try FileHandle(forWritingTo: url)
            let e = AVAudioEngine()
            let node = e.inputNode
            let format = node.outputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else { throw NSError(domain: "Audio", code: 1, userInfo: [NSLocalizedDescriptionKey: "No microphone input is available."]) }
            sampleRate = format.sampleRate
            node.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, _ in
                guard let self = self, let channels = buffer.floatChannelData else { return }
                let frames = Int(buffer.frameLength)
                var mono = [Float](repeating: 0, count: frames)
                for channel in 0..<Int(format.channelCount) {
                    for index in 0..<frames { mono[index] += channels[channel][index] / Float(format.channelCount) }
                }
                let data = mono.withUnsafeBytes { Data($0) }
                self.audioQueue.async { try? file.write(contentsOf: data) }
            }
            recordingURL = url; audioFile = file
            try e.start(); engine = e; recording = true
            key(115); typeLiteral(marker); markerShown = true
            setStatus("Recording", symbol: "mic.fill")
            timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in
                guard let self = self else { return }
                if !self.sameTarget() { self.cancel(); return }
                if !self.inFlight { self.request(final: false) }
            }
        } catch { cleanup(); alert(error.localizedDescription) }
    }
    func closeAudio() {
        timer?.invalidate(); timer = nil
        engine?.stop(); engine?.inputNode.removeTap(onBus: 0); engine = nil
        audioQueue.sync { try? audioFile?.close(); audioFile = nil }
    }
    func stop() {
        closeAudio(); recording = false; finishing = true
        setStatus("Finishing…", symbol: "waveform")
        if !inFlight { request(final: true) }
    }
    func request(final: Bool) {
        guard let url = recordingURL else { return }
        audioQueue.sync { try? audioFile?.synchronize() }
        let message: [String: Any] = ["path": url.path, "sample_rate": Int(sampleRate), "session": session, "final": final]
        if let data = try? JSONSerialization.data(withJSONObject: message) {
            inFlight = true; input.fileHandleForWriting.write(data + Data([10]))
        }
    }
    func sameTarget() -> Bool {
        guard let app = target, !app.isTerminated else { return false }
        return NSWorkspace.shared.frontmostApplication?.processIdentifier == app.processIdentifier
    }
    func replacePreview(_ text: String) {
        guard sameTarget() else { return }
        if text == preview { return }
        // The marker is typed directly so its trailing space is not normalized by a paste.
        for _ in marker + preview { key(51) }
        typeLiteral(marker)
        if !text.isEmpty { paste(text) }
        preview = text
    }
    func cancel() {
        guard recording || finishing else { return }
        closeAudio(); recording = false; finishing = false
        if markerShown && sameTarget() {
            for _ in preview { key(51) }
            for _ in marker { key(51) } // Remove the complete recording marker.
        }
        markerShown = false; preview = ""; session = UUID().uuidString
        if inFlight, let url = recordingURL { cancelledFiles.append(url); recordingURL = nil }
        cleanup(); setStatus("", symbol: "mic")
    }
    func cleanup() {
        if let url = recordingURL { try? FileManager.default.removeItem(at: url); recordingURL = nil }
    }
    func copy(_ text: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
    func typeLiteral(_ text: String) {
        let source = CGEventSource(stateID: .combinedSessionState)
        for character in text {
            let units = Array(String(character).utf16)
            for pressed in [true, false] {
                let event = CGEvent(keyboardEventSource: source, virtualKey: 0, keyDown: pressed)
                units.withUnsafeBufferPointer { buffer in
                    event?.keyboardSetUnicodeString(stringLength: buffer.count, unicodeString: buffer.baseAddress!)
                }
                event?.flags = []
                event?.post(tap: .cghidEventTap)
            }
        }
    }
    func paste(_ text: String) { copy(text); key(9, flags: .maskCommand) }
    func key(_ code: CGKeyCode, flags: CGEventFlags = []) {
        let source = CGEventSource(stateID: .combinedSessionState)
        for pressed in [true, false] {
            let event = CGEvent(keyboardEventSource: source, virtualKey: code, keyDown: pressed)
            event?.flags = flags; event?.post(tap: .cghidEventTap)
        }
    }
    func alert(_ message: String) {
        let a = NSAlert(); a.messageText = "Whisper Dictation"; a.informativeText = message; a.runModal()
    }
    @objc func quit() { cancel(); worker?.terminate(); NSApp.terminate(nil) }
}
let app = NSApplication.shared
let delegate = Dictation()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
