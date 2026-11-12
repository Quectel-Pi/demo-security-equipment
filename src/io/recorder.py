# -*- coding: utf-8 -*-
"""RingRecorder - 报警事件取证录像 + 手动录像 (src/io)

两种录像, 同一个输出文件夹(record.rec_dir, 可配置, 支持绝对路径):
  1) 事件取证 evt_*.mp4: 报警触发, 前5s(内存环)+后5s, 只留最新 keep_files 段,
     第 keep_files+1 段落盘时自动覆盖最旧的
  2) 手动录像 manual_*.mp4: 网页/API start-stop, 随时长录制(上限10分钟保护),
     只留最新 manual_keep 段
平时零编码负担: 主循环只把显示帧 JPEG 压进环形缓冲(10fps采样)。
"""
import glob
import os
import time
import threading
from collections import deque

import cv2
import numpy as np

from applog import log as _log


class RingRecorder:
    def __init__(self, out_dir, pre_s=5.0, post_s=5.0, fps=10,
                 ring_s=8.0, keep_files=3, manual_keep=15, manual_max=600):
        self.out = out_dir
        os.makedirs(self.out, exist_ok=True)
        self.pre_s = float(pre_s)
        self.post_s = float(post_s)
        self.interval = 1.0 / float(fps)
        self.keep = int(keep_files)
        self.mkeep = int(manual_keep)
        self.mmax = float(manual_max)
        self.ring = deque(maxlen=int(ring_s / self.interval))  # (ts, jpeg)
        self._last_feed = 0.0
        self._last_shape = None
        self._busy = False
        self._lock = threading.Lock()
        self._m = None                    # {"vw","path","t0"} 手动录像状态

    # ---------- 主循环喂帧(带框显示帧) ----------
    def feed(self, frame_bgr):
        self._last_shape = frame_bgr.shape[:2]
        now = time.time()
        if now - self._last_feed < self.interval:
            return
        self._last_feed = now
        if self._m is not None:
            self._manual_write(frame_bgr, now)
        ok, jpg = cv2.imencode(".jpg", frame_bgr,
                               [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.ring.append((now, jpg.tobytes()))

    # ---------- 事件取证: 报警触发 ----------
    def save_async(self, tag):
        with self._lock:
            if self._busy:
                return False
            self._busy = True
        threading.Thread(target=self._save, args=(tag,), daemon=True).start()
        return True

    def _save(self, tag):
        name = os.path.join(self.out, "evt_%s_%s.mp4" % (
            tag.replace("/", "_"), time.strftime("%Y%m%d_%H%M%S")))
        vw = None
        try:
            t_start = time.time()
            deadline = t_start + self.post_s
            last_ts = 0.0
            pre = [j for (ts, j) in list(self.ring)
                   if ts >= t_start - self.pre_s]
            for j in pre:
                img = self._dec(j)
                if img is None:
                    continue
                if vw is None:
                    vw = self._open(name, img.shape)
                if vw is not None:
                    vw.write(img)
            while time.time() < deadline:
                for ts, j in [e for e in list(self.ring)
                              if e[0] > last_ts and e[0] > t_start]:
                    img = self._dec(j)
                    if vw is None or img is None:
                        break
                    vw.write(img)
                    last_ts = ts
                time.sleep(self.interval / 2.0)
        except Exception as e:
            _log("错误", "取证录像异常: %s" % e)
        finally:
            if vw is not None:
                vw.release()
                _log("取证", "事件视频已写入: %s" % name)
                self._rotate("evt_*.mp4", self.keep)
            with self._lock:
                self._busy = False

    # ---------- 手动录像 ----------
    def start_manual(self):
        if self._m is not None:
            return self.manual_status()
        self._m = {"vw": None, "path": None, "t0": time.time()}
        return self.manual_status()

    def stop_manual(self):
        m, self._m = self._m, None
        path = None
        if m is not None:
            if m.get("vw") is not None:
                m["vw"].release()
            path = m.get("path")
            if path:
                _log("取证", "手动录像已写入: %s" % path)
                self._rotate("manual_*.mp4", self.mkeep)
        return {"recording": False,
                "file": (os.path.basename(path) if path else None)}

    def manual_status(self):
        if self._m is None:
            return {"recording": False}
        return {"recording": True,
                "seconds": round(time.time() - self._m["t0"], 1),
                "file": os.path.basename(self._m.get("path") or "") or None}

    def _manual_write(self, frame, now):
        m = self._m
        try:
            if m["vw"] is None:
                h, w = self._last_shape
                w -= w % 2
                h -= h % 2
                m["path"] = os.path.join(
                    self.out, time.strftime("manual_%Y%m%d_%H%M%S.mp4"))
                m["vw"] = cv2.VideoWriter(m["path"],
                                          cv2.VideoWriter_fourcc(*"mp4v"),
                                          1.0 / self.interval, (w, h))
                m["t0"] = now
            m["vw"].write(frame)
            if now - m["t0"] > self.mmax:
                _log("取证", "手动录像达到%d分钟上限, 自动停止" % (self.mmax / 60))
                self.stop_manual()
        except Exception as e:
            _log("错误", "手动录像异常: %s" % e)
            self._m = None

    # ---------- 共用 ----------
    def _open(self, path, shape):
        h, w = shape[:2]        # img.shape是(h,w,3), 只取前两维
        w -= w % 2
        h -= h % 2
        return cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"),
                               1.0 / self.interval, (w, h))

    @staticmethod
    def _dec(jpeg):
        return cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8),
                            cv2.IMREAD_COLOR)

    def _rotate(self, pattern, keep):
        files = sorted(glob.glob(os.path.join(self.out, pattern)),
                       key=os.path.getmtime)
        while len(files) > keep:
            victim = files.pop(0)
            try:
                os.remove(victim)
                _log("取证", "轮换清理旧片: %s" % os.path.basename(victim))
            except OSError:
                break
