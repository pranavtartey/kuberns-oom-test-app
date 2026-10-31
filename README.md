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
| `/loop?seconds=60` | Exits now and again on every start for that long (max 300), so the container stays down past the agent's 15s wait | `exit_nonzero` (plus an `exit_recovered` if the agent sees it back up afterwards) |
| `/mem?percent=90` | Grows to that share of the container's memory limit and holds there (97 for critical, never above 98) | `memory_pressure` from 85%, `memory_critical` from 95% |
| `/cpu?seconds=480` | One busy process per CPU for that long, so the container uses the whole host | `cpu_throttled` |
| `/disk?percent=92` | Writes `/tmp/disk-fill` until the root filesystem is that full (never above 97) | `disk_pressure` from 90%, `disk_full` from 95% |
| `/release` | Frees the memory `/mem` and `/oom` hold and deletes the disk file | none |
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

## Events the app cannot cause

`send-event.sh` sends a fake agent event to the backend from the EC2 instance, through the agent
container, so its secret is used on the box and never printed:

```
./send-event.sh ssl_expiring
./send-event.sh disk_pressure "Disk usage at 93% (threshold: 90%)"
```

Statuses: `ssl_expiring`, `ssl_expired`, `disk_pressure`, `disk_full`, `cpu_throttled`,
`memory_pressure`, `memory_critical`, `memory_thrash`, `host_memory_restart`, `recurring_selfheal`.
It prints the backend's reply; `skipped_duplicate` means the same kind of event was already
recorded inside its window.

## Notes for the disk, memory and CPU triggers

- The agent samples memory and CPU every 120s and needs 3 breaches in a row, so keep the load for
  at least 6 minutes (`/cpu` defaults to 8). Disk is checked once an hour, so `/disk` can take up to
  an hour to show; use `send-event.sh` when you do not want to wait. Call `/release` afterwards:
  the file is on the host's disk.
- `/mem` needs the container to have a memory limit; without one it answers 400.
- The backend keeps one incident per kind per window: SSL 24h, disk 6h, CPU and memory 30 minutes,
  crash digest 6h. A repeat inside the window is dropped. Disk and host-memory incidents are
  recorded against the server, not the app container.
- The host-memory guard (`host_memory_restart`) and memory thrash only fire when the host itself is
  nearly out of RAM; use `send-event.sh` for them rather than starving the box.

# kuberns-oom-test-app
# kuberns-oom-test-app
