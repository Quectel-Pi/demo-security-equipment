#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_env.py - 换板子/换环境后的开机体检, 全绿再跑主程序

板端用法:
    cd demo-security-equipment
    /opt/py312/bin/python3.12 tools/check_env.py
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True


def chk(name, fn):
    global ok
    try:
        detail = fn()
        print("  [OK]   %-24s %s" % (name, detail or ""))
    except Exception as e:
        ok = False
        print("  [FAIL] %-24s %s" % (name, e))


def v_python():
    need = (3, 10)
    if sys.version_info < need:
        raise RuntimeError("需要>=%s.%s, 现在%s (NPU用/opt/py312/bin/python3.12)"
                           % (need + sys.version_info[:3]))
    return "%d.%d.%d" % sys.version_info[:3]


def v(mod):
    def f():
        try:
            m = __import__(mod)
        except ImportError:
            raise RuntimeError("缺%s: 运行  sudo sh tools/setup.sh" % mod)
        return getattr(m, "__version__", "")
    return f


def v_model(path):
    def f():
        p = os.path.join(ROOT, path)
        if not os.path.exists(p):
            raise RuntimeError("缺文件 " + path)
        return "%.1f MB" % (os.path.getsize(p) / 1048576.0)
    return f


def v_yaml():
    import yaml
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"),
                              encoding="utf-8"))
    for k in ("camera", "model", "intrusion", "fire_smoke"):
        if k not in cfg:
            raise RuntimeError("config 缺段: " + k)
    return "camera=%s" % cfg["camera"].get("device")


def v_rknn():
    try:
        from rknnlite.api import RKNNLite            # noqa: F401
    except ImportError:
        raise RuntimeError("缺rknnlite: 先运行  sudo sh tools/setup.sh")
    import glob
    nodes = glob.glob("/dev/rknpu*") + glob.glob("/sys/devices/platform/*npu*")
    return "NPU节点=%d(0也正常,推理时才可见)" % len(nodes)


def v_camera():
    sys.path.insert(0, os.path.join(ROOT, "src", "camera"))
    from capture import find_camera
    import yaml
    hint = None
    try:
        cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"),
                                  encoding="utf-8"))
        hint = cfg.get("camera", {}).get("device") or None
    except Exception:
        pass
    d = find_camera(hint)
    if d is None:
        raise RuntimeError("未找到USB摄像头(确认插好; v4l2-ctl --list-devices 核对)")
    return d


print("== demo-security-equipment 环境体检 ==")
chk("python版本", v_python)
for m in ("numpy", "cv2", "yaml", "flask"):
    chk(m, v(m))
chk("rknnlite(NPU)", v_rknn)
if os.uname().sysname == "Linux":
    chk("摄像头节点", v_camera)
    chk("模型-人体", v_model("models/yolo_pose.rknn"))
    chk("模型-火烟", v_model("models/dfire_yolov5s.rknn"))
chk("config.yaml", v_yaml)
try:
    import serial                                   # noqa
    chk("pyserial(舵机)", lambda: "已装")
except ImportError:
    print("  [--]   servo 需 pyserial (仅用舵机时: pip install pyserial)")

print("== 结论:", "全绿, 可以跑 main.py" if ok else "有FAIL项, 先解决==")
sys.exit(0 if ok else 1)
