#!/bin/sh
# demo-security-equipment 一键启动
# 用法(板子上任意目录):  sh /root/demo-security-equipment/start.sh  [--no-alarm]

cd "$(dirname "$0")" || exit 1

# 踢掉上一次没退干净的自己(摄像头独占, 二次启动必失败)
pkill -9 -f src/main.py 2>/dev/null
sleep 0.5

exec /opt/py312/bin/python3.12 src/main.py "$@"
