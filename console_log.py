"""Short desktop console events. Callers supply public messages, never request content."""
import os
from pathlib import Path
import sys
import threading
import time
import traceback


_lock = threading.Lock()
_last_event = {}


def event(area, message, *, level="ИНФО", error=None, repeat_after=None):
    with _lock:
        if repeat_after is not None:
            # Only fixed messages use this option; no user strings are retained.
            key = (area, message, level)
            now = time.monotonic()
            if key in _last_event and now - _last_event[key] < repeat_after:
                return
            _last_event[key] = now
        stream = sys.stdout if level == "ИНФО" else sys.stderr
        line = f"[{time.strftime('%H:%M:%S')}] [{level}] [{area}] {message}"
        if error is not None and os.environ.get("QUIZLENS_DEBUG") == "1":
            # Exception text and source lines may contain a question, file name or key.
            frames = traceback.extract_tb(error.__traceback__)
            where = " → ".join(f"{Path(f.filename).name}:{f.lineno} ({f.name})" for f in frames)
            line += f" [{type(error).__name__}]" + (f" {where}" if where else "")
        print(line, file=stream, flush=True)
