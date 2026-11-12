#!/bin/sh
# setup.sh - 新板子一键环境配置 (demo-security-equipment)
# 用法: sudo sh tools/setup.sh    (需要板子联网; 幂等, 重复跑自动跳过)
#
# 做四件事:
#   1) 自检官方解释器
#   2) Python3.12 + rknnlite + 依赖库 自举 (原厂镜像没有, 用 uv 装)
#   3) uart1 使能 (舵机串口)
#   4) 结论与下一步提示

cd "$(dirname "$0")/.." || exit 1
PYDIR=/opt/py312
PYBIN="$PYDIR/bin/python3.12"
NEED_REBOOT=0

echo "== demo-security-equipment 环境配置 =="

# 0) 联网检查 (无ping命令时跳过此检查, 靠后续下载安装自然暴露问题)
if command -v ping >/dev/null 2>&1; then
    if ! ping -c1 -W3 pypi.org >/dev/null 2>&1 && \
       ! ping -c1 -W3 www.baidu.com >/dev/null 2>&1; then
        echo "[失败] 板子无网络: Python环境安装需要联网(网线或WiFi)"
        exit 1
    fi
    echo "[OK]   网络可访问"
fi

# 1) Python3.12 环境 (原厂Debian13自带3.13, 装不了rknn wheel, 故用uv自举3.12)
if [ -x "$PYBIN" ] && "$PYBIN" -c "import rknnlite" 2>/dev/null; then
    echo "[OK]   Python3.12 环境已就绪 ($($PYBIN -V 2>&1))"
else
    echo "[配置] 安装 Python3.12 + NPU运行库 + 依赖 (约2~4分钟)..."
    if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    fi
    UV=$(command -v uv || echo "$HOME/.local/bin/uv")
    "$UV" python install 3.12 || { echo "[失败] uv python install"; exit 1; }
    if [ -d "$PYDIR" ] && [ ! -x "$PYBIN" ]; then rm -rf "$PYDIR"; fi
    "$UV" venv --python 3.12 "$PYDIR" >/dev/null 2>&1 || true
    PKGS="rknn-toolkit-lite2 numpy opencv-python-headless pyyaml flask pyserial"
    "$UV" pip install --python "$PYDIR/bin/python" $PKGS 2>&1 | tail -2 || \
    "$UV" pip install --default-index https://pypi.tuna.tsinghua.edu.cn/simple \
        --python "$PYDIR/bin/python" $PKGS 2>&1 | tail -2
    if "$PYBIN" -c "import numpy,cv2,yaml,flask,serial,rknnlite" 2>/dev/null; then
        echo "[OK]   Python3.12 环境安装完成"
    else
        echo "[失败] 环境不完整; 重跑本脚本(网络恢复后)或手动检查上面报错"
        exit 1
    fi
fi

# 2) uart1 (舵机串口): 官方开关, 开启需重启一次
if [ -e /dev/ttyS1 ]; then
    echo "[OK]   /dev/ttyS1 已就绪"
elif command -v qpi-config >/dev/null 2>&1; then
    echo "[配置] 使能 uart1 (qpi-config)..."
    qpi-config uart1 enable && NEED_REBOOT=1
else
    echo "[警告] 无 qpi-config 也无 ttyS1 — 请手动确认舵机串口"
fi

# 3) 结论
echo
if [ "$NEED_REBOOT" = 1 ]; then
    echo ">> 配置完成: uart1 刚开启, 请执行  sudo reboot"
    echo ">> 重启后: /opt/py312/bin/python3.12 tools/check_env.py 然后 sh start.sh"
else
    echo ">> 环境就绪。验证:  /opt/py312/bin/python3.12 tools/check_env.py"
    echo ">> 启动:          sh start.sh"
fi
