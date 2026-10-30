# kuberns-oom-test-app

Deliberately unstable app for testing the Kuberns health agent. Never deploy it anywhere real.

Stdlib Python, listens on `0.0.0.0:8000` (the port Kuberns publishes for Python apps) and serves
`/health` (the image HEALTHCHECK hits it).

## Triggers

| Request | Effect | Agent event |
|---|---|---|
| `/oom?mb=25&delay=0.1` | Fills memory until the container is killed. Slow curve: `mb=5&delay=2`. | `oom_kill` |
| `/crash` | Stack trace pointing at `price_of_first_item`, exit 1 | `exit_recovered` (Docker restarts it at once) |
| `/exit?code=3` | Exit with the given code | `exit_recovered`, or `exit_nonzero` if it stays down |
| `/sick?seconds=180` | `/health` answers 500 for that long while the app keeps running | `unhealthy` |
| `/`, `/health` | OK plus current container memory | none |

Env var `CRASH_ON_START=1` crashes the app on boot, which gives a restart loop.

## Notes for testing

- Deploy on the 1GB plan. On the 512MB plan the cap is 307m with 717m of swap, so the app has to
  touch over 1GB, and the host-memory guard (`docker restart` below 20M MemAvailable) can fire first.
- Wait about 10 minutes after a deploy before triggering anything: the backend drops events for an
  env with a deploy in progress or a successful one in the last 10 minutes, without saving them.
  A second event within 2 minutes is also dropped as a duplicate.
- The agent reports `oom_kill` only for exit 137 with `OOMKilled=true`, and posts it immediately.
- A one-off `/crash` is recovered by Docker (`restart: always`) within a second. An agent that
  predates `exit_recovered` skips it; a newer one records it and diagnoses it. A second `/crash`
  inside the 1800s cooldown is counted but not diagnosed again. Use `CRASH_ON_START=1` for a loop.
- `/sick` needs the container's HEALTHCHECK: 30s interval, 3 retries, so `unhealthy` shows up about
  90s after the app starts answering 500, and `seconds` has to outlast that. One `unhealthy`
  diagnosis per service per 1800s.
- `exit_nonzero` (still down after 15s) and `exit_recovered` each have a 1800s cooldown per
  service, reset only when the agent restarts.
- A `docker stop` or `docker restart` is not a crash: Docker signals the container first, and the
  agent treats a die that follows a signal as deliberate.
- Kuberns detects the start command with an LLM. If it picks the wrong one, set it to
  `python app.py` by hand.
# kuberns-oom-test-app
# kuberns-oom-test-app
