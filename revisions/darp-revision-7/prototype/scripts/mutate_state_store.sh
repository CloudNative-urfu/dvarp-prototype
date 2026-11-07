#!/bin/sh
# Мутационная проверка state_store: копия пакета и тестов во временный каталог,
# замена одной строки, прогон test_state_store.py. Вызов: W F OLD NEW NAME.
set -e
W=$1; F=$2; OLD=$3; NEW=$4; NAME=$5
D=$(mktemp -d)
cp -r "$W/darp_range" "$W/tests" "$D/"
python3 - "$D/$F" "$OLD" "$NEW" <<'PY'
import sys; p,o,n=sys.argv[1:]; s=open(p).read(); assert o in s, o; open(p,'w').write(s.replace(o,n,1))
PY
if (cd "$D" && python3 -m unittest discover -s tests -t . -p test_state_store.py >/dev/null 2>&1); then echo "SURVIVED: $NAME"; else echo "killed: $NAME"; fi
rm -rf "$D"
