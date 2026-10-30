"""Deliberately unstable app for testing the Kuberns health agent.

Never deploy this anywhere real. Every failure is triggered by an explicit request,
except CRASH_ON_START=1, which makes the app crash on boot to produce a restart loop.
"""
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8000"))

# Keeps every allocated block referenced so Python never frees it.
_hog = []

# While the clock is before this, /health answers 500 and the container's HEALTHCHECK fails.
_sick_until = 0.0


def memory_mb():
    """Container memory in MB from the cgroup (v2, then v1); -1 if unreadable."""
    for path in (
        "/sys/fs/cgroup/memory.current",
        "/sys/fs/cgroup/memory/memory.usage_in_bytes",
    ):
        try:
            with open(path) as f:
                return int(f.read()) // (1024 * 1024)
        except OSError:
            continue
    return -1


def allocate(step_mb, delay):
    while True:
        # b"x" * n really writes the pages, so the memory is resident, not just reserved.
        _hog.append(b"x" * (step_mb * 1024 * 1024))
        print(
            f"allocated {len(_hog) * step_mb} MB, container usage {memory_mb()} MB",
            flush=True,
        )
        time.sleep(delay)


def price_of_first_item(order):
    return order["items"][0]["price"] * order["qty"]


def crash():
    try:
        price_of_first_item({"items": [], "qty": 2})
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)


def exit_with(code):
    print(f"exiting with code {code}", flush=True)
    os._exit(code)


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        global _sick_until
        url = urlparse(self.path)
        query = parse_qs(url.query)

        if url.path == "/health" and time.time() < _sick_until:
            self._reply(500, {"status": "sick", "seconds_left": int(_sick_until - time.time())})
        elif url.path in ("/", "/health"):
            self._reply(200, {"status": "ok", "memory_mb": memory_mb(), "hog_blocks": len(_hog)})
        elif url.path == "/sick":
            seconds = int(query.get("seconds", ["180"])[0])
            _sick_until = time.time() + seconds
            self._reply(202, {"status": "health check will fail", "seconds": seconds})
        elif url.path == "/oom":
            step_mb = int(query.get("mb", ["25"])[0])
            delay = float(query.get("delay", ["0.1"])[0])
            threading.Thread(target=allocate, args=(step_mb, delay), daemon=True).start()
            self._reply(202, {"status": "allocating", "step_mb": step_mb, "delay_s": delay})
        elif url.path == "/crash":
            threading.Timer(0.5, crash).start()
            self._reply(202, {"status": "crashing in 0.5s"})
        elif url.path == "/exit":
            code = int(query.get("code", ["3"])[0])
            threading.Timer(0.5, exit_with, args=(code,)).start()
            self._reply(202, {"status": "exiting in 0.5s", "code": code})
        else:
            self._reply(404, {"error": "not found"})


if __name__ == "__main__":
    if os.environ.get("CRASH_ON_START") == "1":
        print("CRASH_ON_START=1, crashing on boot", flush=True)
        crash()
    print(f"listening on :{PORT}, container usage {memory_mb()} MB", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
