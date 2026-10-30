# kuberns-oom-test-app

Deliberately unstable app for testing the Kuberns health agent. Never deploy it anywhere real.

Stdlib Python, listens on `0.0.0.0:8000` (the port Kuberns publishes for Python apps) and serves
`/health` (the image HEALTHCHECK hits it).

## Triggers

| Request | Effect | Agent event |
|---|---|---|
| `/oom?mb=25&delay=0.1` | Fills memory until the container is killed. Slow curve: `mb=5&delay=2`. | `oom_kill` |
| `/crash` | Stack trace pointing at `price_of_first_item`, exit 1 | `exit_nonzero` |
| `/exit?code=3` | Exit with the given code | `exit_nonzero` |
| `/`, `/health` | OK plus current container memory | none |

Env var `CRASH_ON_START=1` crashes the app on boot, which gives a restart loop.

## Notes for testing

- Deploy on the 1GB plan. On the 512MB plan the cap is 307m with 717m of swap, so the app has to
  touch over 1GB, and the host-memory guard (`docker restart` below 20M MemAvailable) can fire first.
- Wait about 10 minutes after a deploy before triggering anything: the backend drops events for an
  env with a deploy in progress or a successful one in the last 10 minutes, without saving them.
  A second event within 2 minutes is also dropped as a duplicate.
- The agent reports `oom_kill` only for exit 137 with `OOMKilled=true`, and posts it immediately.
- A one-off `/crash` is usually NOT reported: Docker restarts the container (`restart: always`)
  and the agent skips `exit_nonzero` if the container is running again 15s later. Use
  `CRASH_ON_START=1` for a loop.
- `exit_nonzero` has a 1800s cooldown per service, reset only when the agent restarts.
- Kuberns detects the start command with an LLM. If it picks the wrong one, set it to
  `python app.py` by hand.
# kuberns-oom-test-app
# kuberns-oom-test-app
