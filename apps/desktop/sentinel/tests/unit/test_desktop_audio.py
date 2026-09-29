import ctypes
import ctypes.util
import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import unittest
from unittest.mock import Mock, patch

source = Path(__file__).resolve().parents[2] / "native/graphics/guest/desktop-audio.py"
spec = importlib.util.spec_from_file_location("desktop_audio_test", source)
audio = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio)
OPUS_LIBRARY = os.environ.get("SENTINEL_TEST_OPUS_LIBRARY") or ctypes.util.find_library("opus")


class DesktopAudioTests(unittest.TestCase):
    def test_input_devices_are_rejected_before_opening_pulse(self):
        with (
            patch.object(audio.C, "CDLL") as load,
            self.assertRaisesRegex(ValueError, "output monitor"),
        ):
            audio.Monitor("microphone")
        load.assert_not_called()

    def test_authorized_descriptor_is_used_without_reopening_root_control_socket(self):
        sender, receiver = socket.socketpair()
        monitor, encoder = Mock(), Mock()
        monitor.read.side_effect = [object(), EOFError("finished test")]
        encoder.encode.return_value = b"encoded-opus"
        descriptor = sender.detach()
        try:
            with (
                patch.object(audio, "connect") as connect,
                patch.object(audio, "Monitor", return_value=monitor),
                patch.object(audio, "Encoder", return_value=encoder),
                patch("builtins.print") as report,
                self.assertRaises(EOFError),
            ):
                audio.stream("/root/control.sock", "@DEFAULT_MONITOR@", descriptor)
            connect.assert_not_called()
            self.assertEqual(receiver.recv(1024), struct.pack("!H", 12) + b"encoded-opus")
            self.assertEqual(json.loads(report.call_args.args[0]), {"event": "ready"})
            monitor.close.assert_called_once()
            encoder.close.assert_called_once()
            self.assertEqual(receiver.recv(1), b"")
        finally:
            receiver.close()

    @unittest.skipUnless(OPUS_LIBRARY, "A native libopus is required for packet verification")
    def test_real_encoder_produces_stereo_ten_millisecond_opus_packets(self):
        library = ctypes.CDLL(OPUS_LIBRARY)
        library.opus_packet_get_nb_samples.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_int32,
        ]
        library.opus_packet_get_nb_samples.restype = ctypes.c_int
        library.opus_packet_get_nb_channels.argtypes = [ctypes.c_void_p]
        library.opus_packet_get_nb_channels.restype = ctypes.c_int
        with patch.object(audio.C, "CDLL", return_value=library):
            encoder = audio.Encoder()
        try:
            samples = (ctypes.c_int16 * (audio.SAMPLES * audio.CHANNELS))()
            for index in range(len(samples)):
                samples[index] = (index * 17) % 8000 - 4000
            for _ in range(3):
                packet = encoder.encode(samples)
                self.assertGreater(len(packet), 0)
                self.assertLessEqual(len(packet), audio.MAX_PACKET)
                self.assertEqual(
                    library.opus_packet_get_nb_samples(packet, len(packet), audio.RATE), 480
                )
                self.assertEqual(library.opus_packet_get_nb_channels(packet), 2)
        finally:
            encoder.close()


if __name__ == "__main__":
    unittest.main()
