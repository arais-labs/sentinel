"""One explicit clipboard transfer. Payload travels through stdin, never argv."""

import json
import signal
import sys

from sentinel_display import Clipboard


def transfer(request):
    action = request.get("action")
    clipboard = Clipboard()
    if action == "write":
        text = request.get("text")
        if not isinstance(text, str) or len(text.encode()) > 262144 or "\0" in text:
            raise ValueError("Clipboard text must be at most 256 KiB without NUL")
        clipboard.write(text)
        return {}
    if action == "read":
        return {"text": clipboard.read()}
    raise ValueError("Unknown clipboard operation")


if __name__ == "__main__":

    def timed_out(*_):
        raise TimeoutError("Clipboard transfer timed out")

    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(10)
    try:
        payload = sys.stdin.buffer.read(2_097_153)
        if len(payload) > 2_097_152:
            raise ValueError("Clipboard request too large")
        print(json.dumps({"ok": True, **transfer(json.loads(payload))}), flush=True)
    except Exception as error:
        print(json.dumps({"ok": False, "reason": str(error)}), flush=True)
        sys.exit(1)
