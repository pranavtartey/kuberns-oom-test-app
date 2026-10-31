#!/bin/sh
# Sends one fake health-agent event to the backend, from inside the agent container so its secret
# is used on the box and never printed. Run it on the EC2 instance:
#
#   ./send-event.sh <status> [message]
#
# For the events the app cannot cause (SSL, the host-memory guard, memory thrash, the crash digest)
# and for anything that would take too long to cause for real (disk is checked once an hour).
set -eu

AGENT="${AGENT:-nginx-server-bgworker-resource-health-agent-1}"
STATUS="${1:-}"
MSG="${2:-}"

usage() {
    echo "usage: $0 <status> [message]"
    echo "statuses: ssl_expiring ssl_expired disk_pressure disk_full cpu_throttled"
    echo "          memory_pressure memory_critical memory_thrash host_memory_restart recurring_selfheal"
    exit 1
}

[ -n "$STATUS" ] || usage

ENV_ID=$(docker exec "$AGENT" printenv KUBERNS_ENV_ID)
APP=$(docker ps --format '{{.Names}}' | grep "^${ENV_ID}[_-]" | head -n 1 || true)
[ -n "$APP" ] || { echo "no container for env $ENV_ID is running"; exit 1; }

case "$STATUS" in
    ssl_expiring)
        ETYPE=ssl_expiry_warning; SVC="$APP"
        DEFAULT="SSL certificate for test.kuberns.site expires in 5 days" ;;
    ssl_expired)
        ETYPE=ssl_expired; SVC="$APP"
        DEFAULT="SSL certificate for test.kuberns.site has EXPIRED" ;;
    disk_pressure)
        ETYPE=disk_usage_warning; SVC="${ENV_ID}_host_disk"
        DEFAULT="Disk usage at 92% (threshold: 90%)" ;;
    disk_full)
        ETYPE=disk_usage_critical; SVC="${ENV_ID}_host_disk"
        DEFAULT="CRITICAL: Disk usage at 97% - imminent failure" ;;
    cpu_throttled)
        ETYPE=cpu_throttling; SVC="$APP"
        DEFAULT="Container CPU sustained at 96% of host (192% of one core, 2 CPUs) across 3 consecutive checks (threshold: 90% of host)" ;;
    memory_pressure)
        ETYPE=memory_pressure; SVC="$APP"
        DEFAULT="Container memory sustained at 89% across 3 consecutive checks (threshold: 85%)" ;;
    memory_critical)
        ETYPE=memory_critical; SVC="$APP"
        DEFAULT="Container memory sustained at 97% across 3 consecutive checks - OOM kill imminent" ;;
    memory_thrash)
        STATUS=memory_pressure; ETYPE=memory_pressure; SVC="${ENV_ID}_host_memory"
        DEFAULT="Server is out of RAM and swapping to disk: psi 31%, 64M available, 180M swap used, sustained across 3 consecutive checks." ;;
    host_memory_restart)
        ETYPE=host_memory_critical; SVC="$APP"
        DEFAULT="Host available memory dropped to 18M - proactively restarted app container to prevent host-level OOM" ;;
    recurring_selfheal)
        ETYPE=crash_digest; SVC="$APP"
        DEFAULT="$APP crashed 7 times in the last ~6h and recovered each time" ;;
    *) usage ;;
esac

[ -n "$MSG" ] || MSG="$DEFAULT"

echo "sending $STATUS for $SVC"
docker exec -e SVC="$SVC" -e ETYPE="$ETYPE" -e STATUS="$STATUS" -e MSG="$MSG" "$AGENT" sh -c '
wget -q -T 10 -O - \
    --header="Content-Type: application/json" \
    --header="X-Agent-Secret: $KUBERNS_AGENT_SECRET" \
    --post-data="{\"env_id\":\"$KUBERNS_ENV_ID\",\"service_name\":\"$SVC\",\"event_type\":\"$ETYPE\",\"health_status\":\"$STATUS\",\"container_id\":\"\",\"exit_code\":\"\",\"crash_logs\":\"$MSG\",\"resource_type\":\"SERVER\",\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" \
    "$KUBERNS_WEBHOOK_URL"' || echo "the backend rejected it (check the status name and that no deploy is in progress)"
echo
