"""Desktop playback only: Pulse-compatible monitor -> Opus -> worker socket.

Works with PulseAudio or PipeWire's Pulse server. Never opens a microphone.
The desktop session owns this foreground process and its sound server; there
is no host daemon, TCP listener, retry loop, or dependency on X11/Wayland.
"""

import argparse
import ctypes as C
import json
import socket
import struct
import sys
from contextlib import ExitStack

RATE = 48000
CHANNELS = 2
SAMPLES = 480  # 10 ms
MAX_PACKET = 1275


class SampleSpec(C.Structure):
    _fields_ = [("format", C.c_int), ("rate", C.c_uint32), ("channels", C.c_uint8)]


class BufferAttr(C.Structure):
    _fields_ = [
        (field, C.c_uint32) for field in ("maxlength", "tlength", "prebuf", "minreq", "fragsize")
    ]


class Monitor:
    def __init__(self, source):
        # Only sink monitors may be selected, never an input device.
        if source != "@DEFAULT_MONITOR@" and not source.endswith(".monitor"):
            raise ValueError("Desktop audio requires an output monitor")
        self.library = C.CDLL("libpulse-simple.so.0")
        self.library.pa_simple_new.argtypes = [
            C.c_char_p,
            C.c_char_p,
            C.c_int,
            C.c_char_p,
            C.c_char_p,
            C.POINTER(SampleSpec),
            C.c_void_p,
            C.POINTER(BufferAttr),
            C.POINTER(C.c_int),
        ]
        self.library.pa_simple_new.restype = C.c_void_p
        self.library.pa_simple_read.argtypes = [
            C.c_void_p,
            C.c_void_p,
            C.c_size_t,
            C.POINTER(C.c_int),
        ]
        self.library.pa_simple_read.restype = C.c_int
        self.library.pa_simple_free.argtypes = [C.c_void_p]
        self.library.pa_simple_free.restype = None
        # PA_SAMPLE_S16LE=3, PA_STREAM_RECORD=2. PULSE_SERVER is inherited.
        spec = SampleSpec(3, RATE, CHANNELS)
        attr = BufferAttr(
            SAMPLES * CHANNELS * 2 * 8, 0xFFFFFFFF, 0xFFFFFFFF, 0xFFFFFFFF, SAMPLES * CHANNELS * 2
        )
        error = C.c_int()
        self.stream = self.library.pa_simple_new(
            None,
            b"Sentinel desktop",
            2,
            source.encode(),
            b"Desktop output",
            C.byref(spec),
            None,
            C.byref(attr),
            C.byref(error),
        )
        if not self.stream:
            raise RuntimeError(f"Cannot capture desktop output (Pulse error {error.value})")
        self.samples = (C.c_int16 * (SAMPLES * CHANNELS))()

    def read(self):
        error = C.c_int()
        if (
            self.library.pa_simple_read(
                self.stream, self.samples, C.sizeof(self.samples), C.byref(error)
            )
            < 0
        ):
            raise RuntimeError(f"Desktop sound server disconnected (Pulse error {error.value})")
        return self.samples

    def close(self):
        if self.stream:
            self.library.pa_simple_free(self.stream)
            self.stream = None


class Encoder:
    def __init__(self):
        self.library = C.CDLL("libopus.so.0")
        self.library.opus_encoder_create.argtypes = [
            C.c_int32,
            C.c_int,
            C.c_int,
            C.POINTER(C.c_int),
        ]
        self.library.opus_encoder_create.restype = C.c_void_p
        self.library.opus_encoder_destroy.argtypes = [C.c_void_p]
        self.library.opus_encoder_destroy.restype = None
        self.library.opus_encode.argtypes = [
            C.c_void_p,
            C.POINTER(C.c_int16),
            C.c_int,
            C.POINTER(C.c_ubyte),
            C.c_int32,
        ]
        self.library.opus_encode.restype = C.c_int32
        # The first two parameters are fixed; the third is the variadic value.
        self.library.opus_encoder_ctl.argtypes = [C.c_void_p, C.c_int]
        self.library.opus_encoder_ctl.restype = C.c_int
        error = C.c_int()
        self.encoder = self.library.opus_encoder_create(
            RATE, CHANNELS, 2049, C.byref(error)
        )  # OPUS_APPLICATION_AUDIO
        if not self.encoder or error.value:
            raise RuntimeError(f"Cannot start desktop audio encoder ({error.value})")
        try:
            for request, value in [(4002, 96000), (4010, 5)]:  # bitrate, complexity
                if self.library.opus_encoder_ctl(self.encoder, request, C.c_int(value)) != 0:
                    raise RuntimeError("Cannot configure desktop audio encoder")
        except BaseException:
            self.close()
            raise
        self.packet = (C.c_ubyte * MAX_PACKET)()

    def encode(self, samples):
        size = self.library.opus_encode(self.encoder, samples, SAMPLES, self.packet, MAX_PACKET)
        if size <= 0:
            raise RuntimeError(f"Desktop audio encoding failed ({size})")
        return bytes(self.packet[:size])

    def close(self):
        if self.encoder:
            self.library.opus_encoder_destroy(self.encoder)
            self.encoder = None


def connect(path):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(2)
    try:
        connection.connect(path)
        connection.sendall(b'{"action":"audio"}\n')
        with connection.makefile("rb") as reader:
            line = reader.readline(4097)
        if len(line) > 4096 or not line.endswith(b"\n") or json.loads(line).get("ok") is not True:
            raise RuntimeError("Desktop audio output is unavailable")
        return connection
    except BaseException:
        connection.close()
        raise


def stream(path, source, socket_fd=None):
    with ExitStack() as cleanup:
        # The privileged supervisor may authorize the audio-only connection and
        # pass it to this already-unprivileged process. Never reopen the worker's
        # control socket or broaden its permissions to authenticate with Pulse.
        connection = connect(path) if socket_fd is None else socket.socket(fileno=socket_fd)
        cleanup.callback(connection.close)
        connection.settimeout(2)
        print(f"Opening desktop playback monitor: {source}", file=sys.stderr, flush=True)
        monitor = Monitor(source)
        cleanup.callback(monitor.close)
        print("Desktop playback monitor connected", file=sys.stderr, flush=True)
        encoder = Encoder()
        cleanup.callback(encoder.close)
        print(json.dumps({"event": "ready"}), flush=True)
        while True:
            packet = encoder.encode(monitor.read())
            # Bounded length prefix; the worker supplies its own clock/timestamp.
            connection.sendall(struct.pack("!H", len(packet)) + packet)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--socket", default="/run/sentinel-desktop/display.sock")
    transport.add_argument("--socket-fd", type=int)
    parser.add_argument("--source", default="@DEFAULT_MONITOR@")
    args = parser.parse_args()
    stream(args.socket, args.source, args.socket_fd)
