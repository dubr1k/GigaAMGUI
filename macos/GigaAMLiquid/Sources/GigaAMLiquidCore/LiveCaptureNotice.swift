import Foundation

/// What a `live_capture_event` shows in the Live status line; every event also
/// goes to the log as is. The status line is where the recording state lives,
/// so only events the user can act on, or should know about, replace it.
public enum LiveCaptureNotice: Equatable {
    /// Log line only: the worker's own status messages and timeline
    /// discontinuities — an "idle gap" when a silent system-audio loopback
    /// resumes (routine, not a fault), a sequence gap the client's overflow
    /// notice already covered.
    case logOnly
    /// The event's detail as is: permission denied, device removed, disk full,
    /// the client's own (localized) overflow notice.
    case detail
    /// A worker overflow ("capture queue full; dropped_frames=N", at most one per
    /// 5 s per source) as the length of audio dropped.
    case droppedAudio(seconds: Double)

    /// `sampleRate` is the rate the client streams to the worker.
    public static func classify(kind: String, detail: String, sampleRate: Int = 16_000) -> LiveCaptureNotice {
        switch kind {
        case "status", "discontinuity":
            return .logOnly
        case "overflow":
            guard let frames = droppedFrames(in: detail), sampleRate > 0 else { return .detail }
            return .droppedAudio(seconds: Double(frames) / Double(sampleRate))
        default:
            return .detail
        }
    }

    private static func droppedFrames(in detail: String) -> Int? {
        guard let marker = detail.range(of: "dropped_frames=") else { return nil }
        return Int(detail[marker.upperBound...].prefix { $0.isASCII && $0.isNumber })
    }
}
