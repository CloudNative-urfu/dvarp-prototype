#!/bin/sh
# Ревизия 3 (K3.7): рабочее окружение изоляции для verify_command.
#   - новое сетевое пространство (unshare -n) без внешних интерфейсов и маршрутов;
#   - поднятый loopback (тест HTTP-клиента на 127.0.0.1 выполняется, а не пропускается);
#   - вложенный user namespace с непривилегированным uid (права файлов действуют,
#     тест защиты снимка от записи выполняется);
#   - RESEARCH_LLM_* сняты с окружения.
# Требуется: util-linux unshare с --map-user (≥ 2.38), разрешённые user namespaces.
# Использование: sh scripts/isolated_verify.sh [каталог результатов, по умолчанию runs/verify_isolated]
set -eu
OUT=${1:-runs/verify_isolated}
U=$(id -u); G=$(id -g)
[ "$U" = 0 ] && U=1000
[ "$G" = 0 ] && G=1000
cd "$(dirname "$0")/.."
exec unshare -rn sh -c '
  ip link set lo up 2>/dev/null || python3 -I -c "
import fcntl, socket, struct
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
ifr = struct.pack(\"16sH\", b\"lo\", 0)
flags = struct.unpack(\"16sH\", fcntl.ioctl(s, 0x8913, ifr))[1]
fcntl.ioctl(s, 0x8914, struct.pack(\"16sH\", b\"lo\", flags | 1))"
  exec unshare --user --map-user="$0" --map-group="$1" \
    env -u RESEARCH_LLM_BASE_URL -u RESEARCH_LLM_MODEL -u RESEARCH_LLM_API_KEY \
    DARP_ISOLATION=netns+lo+uid python3 scripts/verify_e2e.py --out "$2"
' "$U" "$G" "$OUT"
