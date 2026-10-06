import time

import numpy as np
import pytest

from src.live.types import CaptureEventKind, CaptureSource


class DeniedMacApi:
    def devices(self, source):
        return []

    def start(self, source, device_id, callback):
        raise PermissionError("Screen Recording denied")

    def pause(self):
        pass

    def stop(self):
        pass


class TccFailureMacApi(DeniedMacApi):
    def start(self, source, device_id, callback):
        raise RuntimeError("Screen Recording permission was denied by TCC")


class CallbackMacApi(DeniedMacApi):
    def start(self, source, device_id, callback):
        callback(np.ones((2, 2), dtype=np.float32), 123, 48_000)


def wait_until(predicate):
    end = time.monotonic() + 1
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_macos_permission_denial_emits_event_without_chunk():
    from src.live.capture.macos import MacSystemAudioAdapter

    events = []
    adapter = MacSystemAudioAdapter(api=DeniedMacApi())
    adapter.start(lambda chunk: pytest.fail("denied capture emitted a chunk"), events.append)
    wait_until(lambda: events)
    adapter.stop()

    assert events[-1].kind is CaptureEventKind.PERMISSION_DENIED
    assert events[-1].source is CaptureSource.SYSTEM
    assert "Screen Recording" in events[-1].detail


def test_macos_unavailable_screencapturekit_explains_virtual_device_fallback():
    from src.live.capture.factory import CaptureUnavailable
    from src.live.capture.macos import MacSystemAudioAdapter

    with pytest.raises(CaptureUnavailable, match="macOS 13|virtual audio device"):
        MacSystemAudioAdapter(api_loader=lambda: (_ for _ in ()).throw(ImportError("missing"))).devices()


def test_macos_tcc_error_is_mapped_to_permission_event():
    from src.live.capture.macos import MacSystemAudioAdapter

    events = []
    adapter = MacSystemAudioAdapter(api=TccFailureMacApi())
    adapter.start(lambda chunk: pytest.fail("denied capture emitted a chunk"), events.append)
    wait_until(lambda: events)
    adapter.stop()

    assert events[-1].kind is CaptureEventKind.PERMISSION_DENIED
    assert "Screen Recording" in events[-1].detail


def test_macos_callback_is_delivered_from_worker_with_native_timestamp():
    from src.live.capture.macos import MacSystemAudioAdapter

    chunks = []
    adapter = MacSystemAudioAdapter(api=CallbackMacApi())
    adapter.start(chunks.append, lambda event: pytest.fail(event.detail))
    wait_until(lambda: chunks)
    adapter.stop()

    assert chunks[0].timestamp_ns == 123
    assert chunks[0].sample_rate == 48_000
    assert chunks[0].frames.flags["WRITEABLE"] is False


def test_macos_shareable_content_completion_returns_none():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    class ShareableContent:
        @staticmethod
        def getShareableContentWithCompletionHandler_(handler):
            assert handler("content", None) is None

    class ScreenCaptureKit:
        SCShareableContent = ShareableContent

    capture = _ScreenCaptureKitCapture(None, None, None, ScreenCaptureKit)

    assert capture._shareable_content() == "content"


def test_macos_system_audio_copies_cmblockbuffer_bytes_before_numpy_conversion():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    pcm = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    delivered = []

    class AVFoundation:
        @staticmethod
        def CMSampleBufferGetDataBuffer(_sample_buffer):
            return "block"

    class CoreMedia:
        @staticmethod
        def CMBlockBufferGetDataLength(block):
            assert block == "block"
            return pcm.nbytes

        @staticmethod
        def CMBlockBufferCopyDataBytes(block, offset, length, destination):
            assert (block, offset, length) == ("block", 0, pcm.nbytes)
            assert destination is None
            return 0, pcm.tobytes()

    capture = _ScreenCaptureKitCapture(AVFoundation, CoreMedia, None, None)
    capture._deliver_audio(object(), lambda frames, timestamp, rate: delivered.append((frames, timestamp, rate)))

    np.testing.assert_array_equal(delivered[0][0], pcm)
    assert delivered[0][1:] == (None, 48_000)


def test_float_pcm_bytes_become_frames_for_both_layouts():
    from src.live.capture.macos import float_pcm_frames

    interleaved = np.array([1, -1, 2, -2], dtype=np.float32).tobytes()
    planar = np.array([1, 2, -1, -2], dtype=np.float32).tobytes()

    expected = [[1.0, -1.0], [2.0, -2.0]]
    assert float_pcm_frames(interleaved, 2, non_interleaved=False).tolist() == expected
    assert float_pcm_frames(planar, 2, non_interleaved=True).tolist() == expected
    assert float_pcm_frames(planar, 1, non_interleaved=True).tolist() == [[1.0], [2.0], [-1.0], [-2.0]]
    with pytest.raises(OSError, match="frame size"):
        float_pcm_frames(b"\x00" * 12, 2, non_interleaved=True)


def _core_media_with_format(pcm: bytes, flags: int, channels: int = 2):
    class Asbd:
        mFormatFlags = flags
        mChannelsPerFrame = channels
        mSampleRate = 48_000.0

    class CoreMedia:
        @staticmethod
        def CMSampleBufferGetFormatDescription(_sample_buffer):
            return "format"

        @staticmethod
        def CMAudioFormatDescriptionGetStreamBasicDescription(description):
            assert description == "format"
            return Asbd()

        @staticmethod
        def CMBlockBufferGetDataLength(_block):
            return len(pcm)

        @staticmethod
        def CMBlockBufferCopyDataBytes(_block, _offset, length, _destination):
            return 0, pcm[:length]

    return CoreMedia


class _BlockAVFoundation:
    @staticmethod
    def CMSampleBufferGetDataBuffer(_sample_buffer):
        return "block"


def test_macos_system_audio_reads_planar_buffers_channel_by_channel():
    """ScreenCaptureKit delivers non-interleaved float: all left samples, then
    all right ones. Reading that as interleaved paired neighbouring samples
    of one channel, so recognition heard each half of a buffer at double speed."""
    from src.live.capture.macos import _ScreenCaptureKitCapture

    planar = np.array([1, 2, 3, 4, -1, -2, -3, -4], dtype=np.float32).tobytes()
    capture = _ScreenCaptureKitCapture(_BlockAVFoundation, _core_media_with_format(planar, 0x29), None, None)
    delivered = []

    capture._deliver_audio(object(), lambda frames, timestamp, rate: delivered.append(np.array(frames)))

    assert delivered[0].tolist() == [[1.0, -1.0], [2.0, -2.0], [3.0, -3.0], [4.0, -4.0]]


def test_macos_system_audio_keeps_interleaved_buffers_as_they_are():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    interleaved = np.array([1, -1, 2, -2], dtype=np.float32).tobytes()
    capture = _ScreenCaptureKitCapture(_BlockAVFoundation, _core_media_with_format(interleaved, 0x09), None, None)
    delivered = []

    capture._deliver_audio(object(), lambda frames, timestamp, rate: delivered.append(np.array(frames)))

    assert delivered[0].tolist() == [[1.0, -1.0], [2.0, -2.0]]


def test_macos_system_audio_reads_a_real_planar_core_media_buffer():
    """The same layout through the real CoreMedia API (no capture hardware needed)."""
    AVFoundation = pytest.importorskip("AVFoundation")
    CoreAudio = pytest.importorskip("CoreAudio")
    CoreMedia = pytest.importorskip("CoreMedia")
    from src.live.capture.macos import _ScreenCaptureKitCapture

    planar = np.array([1, 2, 3, 4, -1, -2, -3, -4], dtype=np.float32).tobytes()
    asbd = CoreAudio.AudioStreamBasicDescription()
    asbd.mSampleRate = 48_000.0
    asbd.mFormatID = CoreAudio.kAudioFormatLinearPCM
    asbd.mFormatFlags = (
        CoreAudio.kAudioFormatFlagIsFloat
        | CoreAudio.kAudioFormatFlagIsNonInterleaved
        | CoreAudio.kAudioFormatFlagIsPacked
    )
    asbd.mBytesPerPacket = asbd.mBytesPerFrame = 4
    asbd.mFramesPerPacket = 1
    asbd.mChannelsPerFrame = 2
    asbd.mBitsPerChannel = 32
    status, description = CoreMedia.CMAudioFormatDescriptionCreate(None, asbd, 0, None, 0, None, None, None)
    assert status == 0
    status, block = CoreMedia.CMBlockBufferCreateWithMemoryBlock(
        None, None, len(planar), None, None, 0, len(planar), 0, None,
    )
    assert status == 0
    assert CoreMedia.CMBlockBufferReplaceDataBytes(planar, block, 0, len(planar)) == 0
    status, sample_buffer = CoreMedia.CMAudioSampleBufferCreateReadyWithPacketDescriptions(
        None, block, description, 4, CoreMedia.CMTimeMake(0, 48_000), None, None,
    )
    assert status == 0
    capture = _ScreenCaptureKitCapture(AVFoundation, CoreMedia, None, None)
    delivered = []

    capture._deliver_audio(sample_buffer, lambda frames, timestamp, rate: delivered.append(np.array(frames)))

    assert delivered[0].tolist() == [[1.0, -1.0], [2.0, -2.0], [3.0, -3.0], [4.0, -4.0]]


def test_macos_system_audio_ignores_empty_cmblockbuffer_callback():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    class AVFoundation:
        @staticmethod
        def CMSampleBufferGetDataBuffer(_sample_buffer):
            return "block"

    class CoreMedia:
        @staticmethod
        def CMBlockBufferGetDataLength(_block):
            return 0

    capture = _ScreenCaptureKitCapture(AVFoundation, CoreMedia, None, None)
    errors = []
    capture.set_error_handler(errors.append)

    capture._deliver_audio(object(), lambda *_args: pytest.fail("empty callback delivered audio"))

    assert errors == []


def test_macos_system_audio_ignores_callback_without_a_data_buffer():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    class AVFoundation:
        @staticmethod
        def CMSampleBufferGetDataBuffer(_sample_buffer):
            return None

    capture = _ScreenCaptureKitCapture(AVFoundation, None, None, None)
    errors = []
    capture.set_error_handler(errors.append)

    capture._deliver_audio(object(), lambda *_args: pytest.fail("empty callback delivered audio"))

    assert errors == []


def test_macos_system_audio_reports_one_persistent_copy_failure():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    class AVFoundation:
        @staticmethod
        def CMSampleBufferGetDataBuffer(_sample_buffer):
            return "block"

    class CoreMedia:
        @staticmethod
        def CMBlockBufferGetDataLength(_block):
            return 8

        @staticmethod
        def CMBlockBufferCopyDataBytes(_block, _offset, _length, destination):
            assert destination is None
            return -12704, b""

    capture = _ScreenCaptureKitCapture(AVFoundation, CoreMedia, None, None)
    errors = []
    capture.set_error_handler(errors.append)

    for _ in range(3):
        capture._deliver_audio(object(), lambda *_args: pytest.fail("invalid buffer delivered audio"))

    assert len(errors) == 1
    assert "copy failed (OSStatus -12704)" in str(errors[0])


def test_macos_system_audio_reports_one_persistent_malformed_buffer():
    from src.live.capture.macos import _ScreenCaptureKitCapture

    class AVFoundation:
        @staticmethod
        def CMSampleBufferGetDataBuffer(_sample_buffer):
            return "block"

    class CoreMedia:
        @staticmethod
        def CMBlockBufferGetDataLength(_block):
            return 8

        @staticmethod
        def CMBlockBufferCopyDataBytes(_block, _offset, _length, _destination):
            return 0, b"\x00"

    capture = _ScreenCaptureKitCapture(AVFoundation, CoreMedia, None, None)
    errors = []
    capture.set_error_handler(errors.append)

    for _ in range(3):
        capture._deliver_audio(object(), lambda *_args: pytest.fail("malformed buffer delivered audio"))

    assert len(errors) == 1
    assert "returned 1 bytes; expected 8 bytes" in str(errors[0])


def test_macos_screen_capture_delegate_uses_objective_c_superclass_initializer():
    Foundation = pytest.importorskip("Foundation")

    from src.live.capture.macos import _ScreenCaptureKitCapture

    class Content:
        @staticmethod
        def displays():
            return [object()]

    class ShareableContent:
        @staticmethod
        def getShareableContentWithCompletionHandler_(handler):
            handler(Content(), None)

    class Configuration:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def setCapturesAudio_(self, _value):
            pass

        def setSampleRate_(self, _value):
            pass

        def setChannelCount_(self, _value):
            pass

    class ContentFilter:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithDisplay_excludingWindows_(self, _display, _windows):
            return self

    class Stream:
        @classmethod
        def alloc(cls):
            return cls()

        def initWithFilter_configuration_delegate_(self, _filter, _configuration, _delegate):
            return self

        def addStreamOutput_type_sampleHandlerQueue_error_(self, _output, _output_type, _queue, _error):
            return True

        def startCaptureWithCompletionHandler_(self, handler):
            handler(None)

        def stopCaptureWithCompletionHandler_(self, handler):
            handler(None)

    class ScreenCaptureKit:
        SCShareableContent = ShareableContent
        SCStreamConfiguration = Configuration
        SCContentFilter = ContentFilter
        SCStream = Stream
        SCStreamOutputTypeAudio = 1

    capture = _ScreenCaptureKitCapture(None, None, Foundation, ScreenCaptureKit)

    capture.start(CaptureSource.SYSTEM, None, lambda *_args: None)
    first_output_type = type(capture._output)
    capture.pause()
    capture.resume()

    assert type(capture._output) is first_output_type
