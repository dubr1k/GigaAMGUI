import AVFoundation
import Foundation
import Testing
@testable import GigaAMLiquid

@Suite struct PcmChunkerTests {
    private func buffer(frames: Int, rate: Double = 16_000) -> AVAudioPCMBuffer {
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames))!
        buffer.frameLength = AVAudioFrameCount(frames)
        for index in 0..<frames { buffer.floatChannelData![0][index] = Float(sin(Double(index) / 8)) * 0.3 }
        return buffer
    }

    private final class Sink {
        private let lock = NSLock()
        private var stored: [LiveAudioChunk] = []
        func add(_ chunk: LiveAudioChunk) { lock.lock(); stored.append(chunk); lock.unlock() }
        var chunks: [LiveAudioChunk] { lock.lock(); defer { lock.unlock() }; return stored }
    }

    /// The trailing chunk at stop was stamped with wall-clock (epoch) time while
    /// every other chunk carries host-clock time: a jump of ~56 years in the
    /// stream the worker uses to place sources in mix.flac.
    @Test func trailingChunkUsesTheSameClockAsTheOthers() throws {
        let sink = Sink()
        let chunker = PcmChunker(source: .mic) { sink.add($0) }
        try chunker.append(buffer: buffer(frames: 2000), hostTime: mach_absolute_time())
        chunker.finish()
        let chunks = sink.chunks
        #expect(chunks.count == 2)
        #expect(chunks.map(\.sampleOffset) == [0, 1600])
        guard chunks.count == 2 else { return }
        let gap = abs(chunks[1].timestampNs - chunks[0].timestampNs)
        #expect(gap < 60_000_000_000, "trailing chunk is \(gap) ns away from the previous one")
    }

    /// A tap block still running when capture stops must not add audio after the
    /// trailing chunk (it would reach the worker after live_stop).
    @Test func nothingIsEmittedAfterFinish() throws {
        let sink = Sink()
        let chunker = PcmChunker(source: .mic) { sink.add($0) }
        chunker.finish()
        try chunker.append(buffer: buffer(frames: 3200), hostTime: mach_absolute_time())
        #expect(sink.chunks.isEmpty)
    }

    @Test func pauseIsSafeFromAnotherThread() async throws {
        let sink = Sink()
        let chunker = PcmChunker(source: .mic) { sink.add($0) }
        let feeding = DispatchQueue(label: "feed")
        let group = DispatchGroup()
        for _ in 0..<50 {
            feeding.async(group: group) { try? chunker.append(buffer: self.buffer(frames: 1600), hostTime: mach_absolute_time()) }
            DispatchQueue.global().async(group: group) { chunker.isPaused.toggle() }
        }
        // Ждём без блокировки потока: синхронный group.wait занимал поток пула
        // тестов, и на 3-ядерном раннере CI весь прогон стоял 10 с — остальные
        // тесты не получали событий и падали по таймауту.
        let done = Flag()
        group.notify(queue: .global()) { done.set() }
        #expect(try await eventually { done.isSet })
        let offsets = sink.chunks.map(\.sampleOffset)
        #expect(offsets == offsets.sorted())
    }
}

private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    func set() { lock.lock(); value = true; lock.unlock() }
    var isSet: Bool { lock.lock(); defer { lock.unlock() }; return value }
}
