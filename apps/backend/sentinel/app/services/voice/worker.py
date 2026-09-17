"""Standalone isolated worker. No imports from the app or its environment."""

import base64
import io
import json
import os
from pathlib import Path
import queue
import sys
import threading
import shutil
import urllib.request
import wave

SPEECH_FILES = {"kokoro-v1.0.int8.onnx": 92361271, "voices-v1.0.bin": 28214398}


def load_speech(root):
    from kokoro_onnx import Kokoro
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 4
    session = ort.InferenceSession(
        str(root / "speech/kokoro-v1.0.int8.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    return Kokoro.from_session(session, str(root / "speech/voices-v1.0.bin"))


def speech_wav(speech, text, speed=1.05):
    import numpy as np

    if not isinstance(speed, (int, float)) or not 0.75 <= speed <= 2.0:
        raise ValueError("Invalid speech speed")
    samples, rate = speech.create(text, voice="af_heart", speed=speed, lang="en-us")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return output.getvalue()


def main():
    root = Path(sys.argv[2])
    if sys.argv[1] == "install":
        from faster_whisper.utils import download_model

        if not (root / "model/model.bin").is_file():
            download_model("base", output_dir=str(root / "model"))
        # Validate the complete download before the manager marks installation ready.
        from faster_whisper import WhisperModel

        WhisperModel(
            str(root / "model"),
            device="cpu",
            compute_type="int8",
            local_files_only=True,
        )
        (root / "speech").mkdir(exist_ok=True)
        for name, size in SPEECH_FILES.items():
            target = root / "speech" / name
            if target.is_file() and target.stat().st_size == size:
                continue
            url = f"https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/{name}"
            partial = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(url, timeout=60) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output)
            if partial.stat().st_size != size:
                raise ValueError(f"Incomplete download: {name}. Retry installation.")
            partial.replace(target)
        speech_wav(load_speech(root), "Voice is ready.")
        return
    requests = queue.Queue()

    def receive():
        for line in sys.stdin:
            requests.put(line)
        # Parent-pipe EOF must also stop an inference currently executing in native code.
        os._exit(0)

    threading.Thread(target=receive, daemon=True).start()
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(root / "model"),
        device="cpu",
        compute_type="int8",
        cpu_threads=4,
        local_files_only=True,
    )
    speech = load_speech(root)
    # Pay lazy phonemizer/inference initialization once, before the user speaks.
    speech_wav(speech, "Ready.")
    print(json.dumps({"ready": True}), flush=True)
    while True:
        try:
            request = json.loads(requests.get())
            if "text" in request:
                text = request["text"]
                if not isinstance(text, str) or not text.strip() or len(text) > 400:
                    raise ValueError("Invalid speech chunk")
                audio = speech_wav(speech, text, request.get("speed", 1.05))
                print(json.dumps({"audio": base64.b64encode(audio).decode()}), flush=True)
                continue
            segments, _ = model.transcribe(
                io.BytesIO(base64.b64decode(request["audio"], validate=True)),
                beam_size=1,
                vad_filter=True,
                condition_on_previous_text=False,
                language="en",
            )
            text = " ".join(segment.text.strip() for segment in segments)
            print(json.dumps({"text": text}), flush=True)
        except Exception:
            print(
                json.dumps(
                    {"error": "Local speech processing failed. Reconnect Voice and try again."}
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
