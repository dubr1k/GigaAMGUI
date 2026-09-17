import AVFoundation
import CoreAudio
import CoreGraphics
import Foundation
import ScreenCaptureKit

enum LiveSource: String {
    case mic
    case system
}

/// 100 ms of 16 kHz mono int16 PCM plus the metadata the worker's PushCaptureAdapter needs.
struct LiveAudioChunk {
    let source: LiveSource
    let seq: Int
    let sampleOffset: Int
    let timestampNs: Int64
    let pcm: Data
    let rms: Float
}

enum LiveCaptureEvent {
    case chunk(LiveAudioChunk)
    case permissionDenied(LiveSource, String)
    case deviceRemoved(LiveSource, String)
    case overflow(LiveSource, String)
    case level(LiveSource, Float)
}

struct LiveInputDevice {
    let id: String
    let name: String
    let isDefault: Bool
}

protocol LiveCaptureSource: AnyObject {
    var source: LiveSource { get }
    func start() throws
    func pause()
    func resume()
    func stop()
}

/// Resamples arbitrary input buffers to 16 kHz mono int16 and emits fixed-size chunks
/// with a monotonic sample offset. Not thread-safe by itself: each capture source
/// feeds its chunker from a single audio callback queue.
final class PcmChunker {
    private let source: LiveSource
    private let targetFormat: AVAudioFormat
    private let chunkFrames: Int
    private let onChunk: (LiveAudioChunk) -> Void
    private var converter: AVAudioConverter?
    private var inputFormat: AVAudioFormat?
    private var pending = [Int16]()
    private var seq = 0
    private var sampleOffset = 0
    var isPaused = false

    init(source: LiveSource, targetRate: Double = 16_000, chunkFrames: Int = 1600, onChunk: @escaping (LiveAudioChunk) -> Void) {
        self.source = source
        self.chunkFrames = chunkFrames
        self.onChunk = onChunk
        self.targetFormat = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: targetRate, channels: 1, interleaved: true)!
    }

    func append(buffer: AVAudioPCMBuffer, hostTime: UInt64) throws {
        guard !isPaused, buffer.frameLength > 0 else { return }
        if inputFormat != buffer.format {
            inputFormat = buffer.format
            converter = AVAudioConverter(from: buffer.format, to: targetFormat)
        }
        guard let converter else { throw WorkerFailure("Unsupported audio format for live capture.") }
        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: capacity) else { return }
        var consumed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if consumed { status.pointee = .noDataNow; return nil }
            consumed = true
            status.pointee = .haveData
            return buffer
        }
        if let error { throw error }
        let frames = Int(output.frameLength)
        guard frames > 0, let samples = output.int16ChannelData?[0] else { return }
        pending.append(contentsOf: UnsafeBufferPointer(start: samples, count: frames))
        let timestampNs = Int64(AVAudioTime.seconds(forHostTime: hostTime) * 1_000_000_000)
        while pending.count >= chunkFrames {
            let chunk = Array(pending[0..<chunkFrames])
            pending.removeFirst(chunkFrames)
            emit(chunk, timestampNs: timestampNs)
        }
    }

    func flush() {
        guard !pending.isEmpty else { return }
        emit(pending, timestampNs: Int64(Date().timeIntervalSince1970 * 1_000_000_000))
        pending.removeAll()
    }

    private func emit(_ samples: [Int16], timestampNs: Int64) {
        var sumSquares = 0.0
        for sample in samples {
            let value = Double(sample) / 32768.0
            sumSquares += value * value
        }
        let rms = Float((sumSquares / Double(max(samples.count, 1))).squareRoot())
        let data = samples.withUnsafeBufferPointer { Data(buffer: $0) }
        onChunk(LiveAudioChunk(source: source, seq: seq, sampleOffset: sampleOffset, timestampNs: timestampNs, pcm: data, rms: rms))
        seq += 1
        sampleOffset += samples.count
    }
}

/// Microphone input through AVAudioEngine; the selected CoreAudio device is routed into the input node.
final class MicrophoneCapture: LiveCaptureSource {
    let source = LiveSource.mic
    private let engine = AVAudioEngine()
    private let chunker: PcmChunker
    private let onEvent: (LiveCaptureEvent) -> Void
    private let deviceID: String?
    private var observer: NSObjectProtocol?

    init(deviceID: String?, onEvent: @escaping (LiveCaptureEvent) -> Void) {
        self.deviceID = deviceID
        self.onEvent = onEvent
        self.chunker = PcmChunker(source: .mic) { chunk in
            onEvent(.chunk(chunk))
            onEvent(.level(.mic, chunk.rms))
        }
    }

    static func devices() -> [LiveInputDevice] {
        let defaultID = AVCaptureDevice.default(for: .audio)?.uniqueID
        let devices: [AVCaptureDevice]
        if #available(macOS 14.0, *) {
            devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external], mediaType: .audio, position: .unspecified).devices
        } else {
            devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInMicrophone, .externalUnknown], mediaType: .audio, position: .unspecified).devices
        }
        return devices.map { LiveInputDevice(id: $0.uniqueID, name: $0.localizedName, isDefault: $0.uniqueID == defaultID) }
    }

    static func requestAccess(_ completion: @escaping (Bool) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized:
            completion(true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .audio) { granted in DispatchQueue.main.async { completion(granted) } }
        default:
            completion(false)
        }
    }

    func start() throws {
        if let deviceID, let device = AVCaptureDevice(uniqueID: deviceID) {
            Self.select(device: device, on: engine.inputNode)
        }
        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { throw WorkerFailure(L10n.text("Микрофон недоступен.")) }
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, time in
            guard let self else { return }
            do { try self.chunker.append(buffer: buffer, hostTime: time.hostTime) }
            catch { self.onEvent(.overflow(.mic, error.localizedDescription)) }
        }
        engine.prepare()
        try engine.start()
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            guard let self, !self.engine.isRunning else { return }
            self.onEvent(.deviceRemoved(.mic, L10n.text("Микрофон отключён.")))
        }
    }

    func pause() { chunker.isPaused = true }
    func resume() { chunker.isPaused = false }

    func stop() {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        chunker.flush()
    }

    /// Route a specific CoreAudio device into the engine's input unit.
    private static func select(device: AVCaptureDevice, on node: AVAudioInputNode) {
        guard let unit = node.audioUnit else { return }
        var deviceID = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyTranslateUIDToDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var uid = device.uniqueID as CFString
        let status = withUnsafeMutablePointer(to: &uid) { pointer in
            AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, UInt32(MemoryLayout<CFString>.size), pointer, &size, &deviceID)
        }
        guard status == noErr, deviceID != 0 else { return }
        AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0, &deviceID, UInt32(MemoryLayout<AudioDeviceID>.size))
    }
}

/// System audio through ScreenCaptureKit: an audio-only stream over the main display with a 2×2 px video
/// frame at 1 fps, which is the smallest configuration SCStream accepts.
final class SystemAudioCapture: NSObject, LiveCaptureSource, SCStreamOutput, SCStreamDelegate {
    let source = LiveSource.system
    private let chunker: PcmChunker
    private let onEvent: (LiveCaptureEvent) -> Void
    private var stream: SCStream?
    private let queue = DispatchQueue(label: "GigaAMLiquid.systemAudio")

    init(onEvent: @escaping (LiveCaptureEvent) -> Void) {
        self.onEvent = onEvent
        self.chunker = PcmChunker(source: .system) { chunk in
            onEvent(.chunk(chunk))
            onEvent(.level(.system, chunk.rms))
        }
        super.init()
    }

    static func requestAccess() -> Bool { CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() }

    func start() throws {
        let semaphore = DispatchSemaphore(value: 0)
        var content: SCShareableContent?
        var failure: Error?
        SCShareableContent.getExcludingDesktopWindows(true, onScreenWindowsOnly: true) { result, error in
            content = result
            failure = error
            semaphore.signal()
        }
        semaphore.wait()
        if let failure { throw failure }
        guard let display = content?.displays.first else {
            throw WorkerFailure(L10n.text("Нет доступного дисплея для захвата системного звука."))
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.capturesAudio = true
        configuration.excludesCurrentProcessAudio = true
        configuration.sampleRate = 48_000
        configuration.channelCount = 1
        configuration.width = 2
        configuration.height = 2
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        let stream = SCStream(filter: filter, configuration: configuration, delegate: self)
        try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        let startSemaphore = DispatchSemaphore(value: 0)
        var startError: Error?
        stream.startCapture { error in
            startError = error
            startSemaphore.signal()
        }
        startSemaphore.wait()
        if let startError { throw startError }
        self.stream = stream
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description) else { return }
        let frames = CMSampleBufferGetNumSamples(sampleBuffer)
        guard frames > 0, let format = AVAudioFormat(streamDescription: asbd),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)) else { return }
        buffer.frameLength = AVAudioFrameCount(frames)
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(sampleBuffer, at: 0, frameCount: Int32(frames), into: buffer.mutableAudioBufferList)
        guard status == noErr else { return }
        // Presentation time is on the host clock, the same clock the microphone tap reports.
        let seconds = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
        do { try chunker.append(buffer: buffer, hostTime: AVAudioTime.hostTime(forSeconds: seconds)) }
        catch { onEvent(.overflow(.system, error.localizedDescription)) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        onEvent(.deviceRemoved(.system, error.localizedDescription))
    }

    func pause() { chunker.isPaused = true }
    func resume() { chunker.isPaused = false }

    func stop() {
        guard let stream else { return }
        let semaphore = DispatchSemaphore(value: 0)
        stream.stopCapture { _ in semaphore.signal() }
        _ = semaphore.wait(timeout: .now() + 3)
        self.stream = nil
        queue.sync { chunker.flush() }
    }
}
