"""Deliberately unstable app for testing the Kuberns health agent.

Never deploy this anywhere real. Every failure is triggered by an explicit request,
except CRASH_ON_START=1, which makes the app crash on boot to produce a restart loop.
"""
import json
import os
import subprocess
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

DISK_FILL_PATH = "/tmp/disk-fill"
DISK_FILL_CHUNK_MB = 64
# Past this the host can stop answering SSH, so /disk never goes further.
DISK_FILL_MAX_PERCENT = 97
MEMORY_HOLD_MAX_PERCENT = 98
_stop_fill = threading.Event()

CRASH_LOOP_PATH = "/tmp/crash-loop-until"
CRASH_LOOP_MAX_SECONDS = 300


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


def memory_limit_mb():
    """Container memory limit in MB from the cgroup (v2, then v1); -1 when there is none."""
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path) as f:
                raw = f.read().strip()
        except OSError:
            continue
        if raw == "max":
            return -1
        limit = int(raw) // (1024 * 1024)
        # cgroup v1 reports an absurdly large number for "no limit".
        return limit if limit < 1024 * 1024 else -1
    return -1


def hold_memory(percent, step_mb=5):
    """Grows to `percent` of the container's memory limit and stays there."""
    limit = memory_limit_mb()
    target = limit * percent // 100
    while memory_mb() < target and len(_hog) * step_mb < limit:
        _hog.append(b"x" * (step_mb * 1024 * 1024))
        time.sleep(0.05)
    print(f"holding {memory_mb()} MB of a {limit} MB limit", flush=True)


def burn_cpu(seconds):
    """One busy process per CPU, so the container uses the whole host rather than one core."""
    deadline = time.time() + seconds
    code = "import sys, time\nend = float(sys.argv[1])\nwhile time.time() < end:\n    pass"
    for _ in range(os.cpu_count() or 1):
        subprocess.Popen([sys.executable, "-c", code, str(deadline)])


def disk_percent():
    """Used share of the root filesystem the way `df /` reports it."""
    stats = os.statvfs("/")
    used = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
    available = stats.f_bavail * stats.f_frsize
    return 100 * used / (used + available)


def fill_disk(percent):
    chunk = b"\0" * (DISK_FILL_CHUNK_MB * 1024 * 1024)
    _stop_fill.clear()
    with open(DISK_FILL_PATH, "ab") as f:
        while disk_percent() < percent and not _stop_fill.is_set():
            f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    print(f"disk is {disk_percent():.1f}% full", flush=True)


def release():
    """Gives back the memory and disk the triggers above are holding."""
    _stop_fill.set()
    _hog.clear()
    try:
        os.remove(DISK_FILL_PATH)
    except FileNotFoundError:
        pass


def in_crash_loop():
    """True while a /loop window is open. The file survives restarts because Docker restarts the
    same container, so every boot inside the window exits again."""
    try:
        with open(CRASH_LOOP_PATH) as f:
            return time.time() < float(f.read())
    except (OSError, ValueError):
        return False


def start_crash_loop(seconds):
    with open(CRASH_LOOP_PATH, "w") as f:
        f.write(str(time.time() + seconds))
    threading.Timer(0.5, exit_with, args=(1,)).start()


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
        elif url.path == "/mem":
            percent = min(max(int(query.get("percent", ["90"])[0]), 1), MEMORY_HOLD_MAX_PERCENT)
            limit = memory_limit_mb()
            if limit < 0 or memory_mb() < 0:
                self._reply(400, {"error": "container has no readable memory limit"})
            else:
                threading.Thread(target=hold_memory, args=(percent,), daemon=True).start()
                self._reply(202, {"status": "filling memory", "percent": percent, "limit_mb": limit})
        elif url.path == "/cpu":
            seconds = int(query.get("seconds", ["480"])[0])
            burn_cpu(seconds)
            self._reply(202, {"status": "burning every cpu", "cpus": os.cpu_count(), "seconds": seconds})
        elif url.path == "/disk":
            percent = min(max(int(query.get("percent", ["92"])[0]), 1), DISK_FILL_MAX_PERCENT)
            threading.Thread(target=fill_disk, args=(percent,), daemon=True).start()
            self._reply(202, {"status": "filling disk", "percent": percent, "now": round(disk_percent(), 1)})
        elif url.path == "/release":
            release()
            self._reply(200, {"status": "released", "memory_mb": memory_mb(), "disk_percent": round(disk_percent(), 1)})
        elif url.path == "/loop":
            seconds = min(max(int(query.get("seconds", ["60"])[0]), 1), CRASH_LOOP_MAX_SECONDS)
            start_crash_loop(seconds)
            self._reply(202, {"status": "crashing on every start", "seconds": seconds})
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
    if in_crash_loop():
        print("crash loop window is open, exiting", flush=True)
        sys.exit(1)
    if os.environ.get("CRASH_ON_START") == "1":
        print("CRASH_ON_START=1, crashing on boot", flush=True)
        crash()
    print(f"listening on :{PORT}, container usage {memory_mb()} MB", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
