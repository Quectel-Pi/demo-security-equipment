# -*- coding: utf-8 -*-
"""Camera capture module - V4L2 USB camera open/config/read helpers."""
import glob
import os
import re
import time

import cv2


def _cam_name(dev_path):
    """读 /sys/class/video4linux/videoN/name 里的设备名"""
    try:
        with open("/sys/class/video4linux/%s/name"
                  % os.path.basename(dev_path)) as f:
            return f.read().strip()
    except OSError:
        return ""


def _key(d):
    m = re.search(r"/dev/video(\d+)$", d)
    return int(m.group(1)) if m else 9999


def _alive(dev, width=1280, height=720, fourcc="MJPG", budget=1.2):
    cap = open_camera(dev, width, height, fourcc)
    if not cap.isOpened():
        cap.release()
        return False
    ok = False
    t0 = time.time()
    while time.time() - t0 < budget:
        r, _ = cap.read()
        if r:
            ok = True
            break
        time.sleep(0.02)
    cap.release()
    return ok


def find_camera(prefer=None, hint="USB Camera", need_alive=True,
                size=(1280, 720), fourcc="MJPG"):
    """定位可用的USB摄像头节点(重启后编号会变, 不建议写死).

    按设备名收集候选(数字结尾优先), need_alive 时逐个试开读帧,
    返回第一个真能出画面的节点; 全无返回 None。
    """
    cands = []
    pool = ([prefer] if prefer and os.path.exists(prefer) else []) \
        + glob.glob("/dev/video*")
    seen = set()
    for d in pool:
        if d in seen:
            continue
        seen.add(d)
        if hint.lower() in _cam_name(d).lower():
            cands.append((_key(d), d))
    if not cands:
        return None
    cands.sort()
    if not need_alive:
        return cands[0][1]
    for _, d in cands:
        if _alive(d, size[0], size[1], fourcc):
            return d
    return None


def open_camera(dev, width, height, fourcc="MJPG", fps=30):
    """统一打开摄像头并设参数(初始/重连路径).

    MJPG + 指定分辨率是 RK3576 USB Camera 保持帧率的关键。
    返回已打开的 VideoCapture(可能未就绪, 由调用方 isOpened 判断,
    与原 main.py 的宽容行为一致——掉线重连场景靠重试收敛)。
    """
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    return cap
