#!/bin/sh
# Ревизия 9: полный unittest в той же изоляции, что verify (netns без внешних интерфейсов + lo,
# вложенный user namespace, RESEARCH_LLM_* сняты). Использование: sh scripts/isolated_unittest.sh [лог]
set -eu
LOG=${1:-runs/unittest_isolated.log}
U=$(id -u); G=$(id -g)
[ "$U" = 0 ] && U=1000
[ "$G" = 0 ] && G=1000
cd "$(dirname "$0")/.."
mkdir -p "$(dirname "$LOG")"
unshare -rn sh -c '
  ip link set lo up 2>/dev/null || python3 -I -c "
import fcntl, socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ifr = struct.pack(\"16sH\", b\"lo\", 0)
flags = struct.unpack(\"16sH\", fcntl.ioctl(s, 0x8913, ifr))[1]
fcntl.ioctl(s, 0x8914, struct.pack(\"16sH\", b\"lo\", flags | 1))"
  exec unshare --user --map-user="$0" --map-group="$1" \
    env -u RESEARCH_LLM_BASE_URL -u RESEARCH_LLM_MODEL -u RESEARCH_LLM_API_KEY \
    DARP_ISOLATION=netns+lo+uid python3 -m unittest discover -s tests -t .
' "$U" "$G" > "$LOG" 2>&1 && rc=0 || rc=$?
echo "rc=$rc" >> "$LOG"
exit $rc
