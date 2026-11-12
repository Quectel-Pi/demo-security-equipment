# -*- coding: utf-8 -*-
"""demo-security-equipment 主程序
实时人体检测 + 入侵区域判定(连续5s同一人报警) + Web推流 + 报警/抓拍

用法(板端, 项目根目录下):
    /opt/py312/bin/python3.12 app/main.py
    /opt/py312/bin/python3.12 app/main.py --no-alarm   # 只检测不响
"""
import argparse
import json
import os
import sys
import threading
import time

import cv2
import numpy as np
import yaml
import contextlib

os.environ.setdefault("RKNN_LOG_LEVEL", "1")   # NPU运行库降噪(兜底, 见_hush)


@contextlib.contextmanager
def _hush():
    """临时吞掉C库/第三方往stdout/stderr打的字(model load的I/W行),
    我们自己的print不受影响(在hush外打印)。"""
    try:
        fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    so, se = os.dup(1), os.dup(2)
    try:
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        yield
    finally:
        os.dup2(so, 1)
        os.dup2(se, 2)
        os.close(fd)
        os.close(so)
        os.close(se)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 模块化布局: 入口=src/main.py, 模块=src/{vision,logic,io,servo,camera}
for _sub in ("src/vision", "src/logic", "src/io", "src/servo", "src/camera"):
    sys.path.insert(0, os.path.join(ROOT, _sub.replace("/", os.sep)))

from pose_detector import PoseDetector          # noqa: E402  vision
from intrusion import IntrusionDetector         # noqa: E402  logic
from alarm import AlarmController               # noqa: E402  io
from applog import log as _log, setup as _logsetup, path as _logpath  # noqa: E402 io

try:                                             # 火焰烟雾(可选, 无模型自动跳过)
    from fire_smoke import FireSmokeDetector, draw_detections   # vision
    from fire_alarm import FireSmokeAlarm                        # logic
    _FS_OK = True
except ImportError:
    _FS_OK = False


def draw_boxes(frame, tracks_info, color_idle=(0, 220, 0),
               color_alarm=(0, 0, 255), lw=2, scale=0.55):
    """只画人框 + 分数"""
    for t in tracks_info:
        x1, y1, x2, y2 = map(int, t["bbox"])
        col = color_alarm if t.get("inside") else color_idle
        cv2.rectangle(frame, (x1, y1), (x2, y2), col, lw)
        label = "person %.2f" % t["score"]
        if t.get("inside_since") is not None:
            label += "  IN %.1fs" % t["inside_dur"]
        cv2.putText(frame, label, (x1, max(0, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, col, max(1, lw - 1))


class SecurityApp:
    def __init__(self, cfg, no_alarm=False):
        self.cfg = cfg
        c = cfg["camera"]
        self.dev = c.get("device") or ""          # 空=自动找USB摄像头
        self.size = (c.get("width", 1280), c.get("height", 720))
        self.fourcc = c.get("fourcc", "MJPG")
        dp = cfg.get("display", {})
        self.disp = {   # 运行期可被 /api/display 热改
            "show_boxes": bool(dp.get("show_boxes", True)),
            "show_person": bool(dp.get("show_person", True)),
            "show_fire": bool(dp.get("show_fire", True)),
            "show_zone": bool(dp.get("show_zone", True)),
            "lw": int(dp.get("line_width", 2)),
            "fs": float(dp.get("font_scale", 0.55)),
            "box_alpha": max(0.2, min(1.0, float(dp.get("box_alpha", 1.0)))),
        }
        self._cam_status_printed = False

        # AI 检测器
        m = cfg["model"]
        with _hush():
            self.det = PoseDetector(
                os.path.join(ROOT, m["path"]),
                input_size=tuple(m.get("input_size", [640, 640])),
                conf_threshold=m.get("conf_threshold", 0.35),
                nms_threshold=m.get("nms_threshold", 0.45))
            self.det.infer(np.zeros((360, 640, 3), np.uint8))  # 预热+首帧警告静音
        _log("启动", "人体检测: OK")

        # 火焰/烟雾检测器(可选) —— 人体用默认核(自动), 火烟绑 NPU 核1
        self.fs_det = None
        self.fs_alarm = None
        fc = cfg.get("fire_smoke", {})
        if _FS_OK and fc.get("enabled", True):
            fs_path = os.path.join(ROOT, fc.get("model", "models/fire_smoke.rknn"))
            if os.path.exists(fs_path):
                with _hush():
                    self.fs_det = FireSmokeDetector(
                        fs_path, names=fc.get("names", ["fire", "other", "smoke"]),
                        conf_threshold=fc.get("conf_threshold", 0.5),
                        nms_threshold=fc.get("nms_threshold", 0.45),
                        core_mask=fc.get("npu_core", 2),
                        layout=fc.get("layout", "v8"))
                    self.fs_det.infer(np.zeros((360, 640, 3), np.uint8))
                self.fs_alarm = FireSmokeAlarm(
                    fire_conf=fc.get("fire_conf", 0.65),
                    smoke_conf=fc.get("smoke_conf", 0.55),
                    fire_frames=fc.get("fire_frames", 3),
                    smoke_window=fc.get("smoke_window", 30),
                    smoke_hits=fc.get("smoke_hits", 10),
                    fire_cooldown=fc.get("fire_cooldown", 30.0),
                    smoke_cooldown=fc.get("smoke_cooldown", 60.0),
                    fire_min_area=fc.get("fire_min_area", 0.008),
                    smoke_min_area=fc.get("smoke_min_area", 0.01),
                    static_move_thr=fc.get("static_move_thr", 0.015),
                    static_reset_thr=fc.get("static_reset_thr", 0.05),
                    static_lock=fc.get("static_lock", 10),
                    static_release=fc.get("static_release", 8))
                self.fs_every_n = int(fc.get("every_n_frames", 2))
                _log("启动", "火烟检测: OK")
            else:
                _log("启动", "火烟检测: 模型缺失(%s), 已关闭" % fs_path)
        self.fs_dets_cache = []       # 隔帧推理时复用的缓存结果
        self.fs_status_txt = "off" if self.fs_det is None else "idle"

        # 入侵判定
        ic = cfg["intrusion"]
        self.intr = IntrusionDetector(
            ic["zone"],
            hold_seconds=ic.get("hold_seconds", 5.0),
            cooldown_s=ic.get("cooldown_s", 30.0),
            iou_thr=ic.get("iou_thr", 0.30),
            track_timeout=ic.get("track_timeout", 0.8))

        # 报警 (复用官方 alarm.py: LED + 喇叭)
        # wav 转绝对路径, 避免受启动目录影响
        wav = cfg.get("alarm", {}).get("speaker", {}).get("wav", "")
        if wav and not os.path.isabs(wav):
            cfg["alarm"]["speaker"]["wav"] = os.path.join(ROOT, wav)
        self.no_alarm = no_alarm
        with _hush():
            self.alarm = AlarmController(cfg.get("alarm", {}))
        _log("启动", "报警(LED/喇叭): OK")

        # 舵机 ZP15S(层A): 启动即初始化并回中; 当前角度由 self.servo_angle 维护
        sc = cfg.get("servo", {})
        self.servo = None
        self.servo_angle = float(sc.get("center", 135.0))
        if sc.get("enabled", False):
            try:
                from servo_zp import ZPServo
                self.servo = ZPServo(sc.get("port", "/dev/ttyS1"),
                                     sid=sc.get("id", 0),
                                     baud=sc.get("baud", 115200),
                                     angle_max=sc.get("angle_max", 270))
                self.servo.center()
                _log("启动", "舵机: OK (%s, 已回中)" % sc.get("port", "/dev/ttyS1"))
            except Exception as e:
                self.servo = None
                _log("启动", "舵机: 初始化失败(%s), 已关闭" % e)
        else:
            _log("启动", "舵机: 未启用")

        # 事件存档
        self.rec_dir = os.path.join(ROOT, cfg["record"].get("save_dir", "records"))
        os.makedirs(self.rec_dir, exist_ok=True)
        self.events_log = os.path.join(self.rec_dir, "events.jsonl")

        # 事件取证录像(轻量: JPEG环形缓冲, 报警才编码落盘, 平时零开销)
        rc = cfg["record"]
        self.rec = None
        if rc.get("enabled", True):
            try:
                from recorder import RingRecorder
                rd = rc.get("rec_dir", "records/rec")
                self.rec = RingRecorder(
                    rd if os.path.isabs(rd) else os.path.join(ROOT, rd),
                    pre_s=rc.get("pre_s", 5), post_s=rc.get("post_s", 5),
                    fps=rc.get("fps", 10), keep_files=rc.get("keep_files", 3),
                    manual_keep=rc.get("manual_keep", 15))
                _log("启动", "取证录像: OK (前后各%.0fs, 留%d段, %s)" %
                     (self.rec.pre_s, self.rec.keep, self.rec.out))
            except Exception as e:
                _log("启动", "取证录像: 初始化失败(%s), 已关闭" % e)

        # 推流共享帧
        self.lock = threading.Lock()
        self.frame = None
        self.info = {"ms": 0.0, "persons": 0, "status": "idle"}
        self.stop = threading.Event()

    # ---------- 事件处理 ----------
    def on_intrusion(self, event, frame):
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        _log("事件", "入侵: 区域内滞留%.1fs (track=%s)" %
             (event["seconds"], event["track_id"]))
        if not self.no_alarm:
            self.alarm.trigger(self.cfg["alarm"].get("duration_s", 10))
        # 抓拍存证
        name = "snap_intrusion_%s_track%s.jpg" % (
            time.strftime("%Y%m%d_%H%M%S"), event["track_id"])
        path = os.path.join(self.rec_dir, name)
        cv2.imwrite(path, frame)
        rec = {"ts": ts, "type": "intrusion", "track_id": event["track_id"],
               "seconds": event["seconds"], "bbox": [round(v, 1) for v in event["bbox"]],
               "snapshot": name}
        with open(self.events_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _log("取证", "入侵抓拍: %s" % path)
        if self.rec:
            self.rec.save_async("intrusion_t%s" % event["track_id"])

    def on_fire_smoke(self, event, frame):
        """火焰L3/烟雾L1 报警处理"""
        etype = event["type"]
        det = event.get("det") or {}
        fc = self.cfg.get("fire_smoke", {})
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        _log("事件", "%s conf=%.2f" % (
            "明火" if etype == "fire" else "烟雾",
            det.get("score", 0.0)))
        if not self.no_alarm:
            dur = fc.get("fire_duration_s" if etype == "fire"
                         else "smoke_duration_s", 10 if etype == "fire" else 3)
            self.alarm.trigger(dur)
        name = "snap_%s_%s.jpg" % (etype, time.strftime("%Y%m%d_%H%M%S"))
        _p = os.path.join(self.rec_dir, name)
        cv2.imwrite(_p, frame)
        _log("取证", "%s抓拍: %s" % ("明火" if etype == "fire" else "烟雾", _p))
        rec = {"ts": ts, "type": etype, "level": event.get("level"),
               "score": round(det.get("score", 0), 3),
               "bbox": [round(v, 1) for v in det.get("bbox", (0, 0, 0, 0))],
               "hits": event.get("hits"), "snapshot": name}
        with open(self.events_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if self.rec:
            self.rec.save_async(etype)

    # ---------- 采集+推理线程 ----------
    def _open_cap(self):
        """统一打开摄像头(自动定位+活性探测: 名字对且真能出画面的节点)"""
        from capture import open_camera, find_camera
        with _hush():      # 探测期间候选节点的cv2警告一并静音
            dev = find_camera(self.dev or None, size=self.size,
                              fourcc=self.fourcc)
        if dev is None:
            if not self._cam_status_printed:
                _log("错误", "摄像头不可用(未插好/被占用; 查: ls /dev/video*)")
                self._cam_status_printed = True
            time.sleep(2)
            return cv2.VideoCapture()
        if not self._cam_status_printed:
            _log("启动", "摄像头: OK (%s)" % dev)
            self._cam_status_printed = True
        return open_camera(
            dev, self.size[0], self.size[1], self.fourcc,
            self.cfg["camera"].get("fps", 30))

    def worker(self):
        cap = self._open_cap()
        fail = 0
        frame_i = 0
        while not self.stop.is_set():
            if not cap.isOpened():
                _log("错误", "摄像头打开失败, 2s后重试")
                time.sleep(2)
                cap = self._open_cap()
                continue
            ret, frame = cap.read()
            if not ret:
                fail += 1
                if fail > 30:
                    _log("错误", "摄像头疑似掉线, 重开")
                    cap.release()
                    cap = self._open_cap()
                    fail = 0
                time.sleep(0.01)
                continue
            fail = 0

            dets, ms = self.det.infer(frame)
            h, w = frame.shape[:2]
            event = self.intr.update(dets, w, h)
            status, alarming, worst = self.intr.status(w, h)

            # 收集 track 信息供画框
            tracks_info = []
            for t in self.intr.tracks:
                inside = t.inside_since is not None
                tracks_info.append({
                    "bbox": t.bbox,
                    "score": 0.0,   # track里没带score, 下面从dets找
                    "inside": inside,
                    "inside_since": t.inside_since,
                    "inside_dur": (time.time() - t.inside_since) if inside else 0,
                })
            # 把score对回去(IoU最大的det)
            for ti in tracks_info:
                best, bs = 0.0, 0.0
                for d in dets:
                    r = self._iou(ti["bbox"], d["bbox"])
                    if r > bs:
                        bs, best = r, d["score"]
                ti["score"] = best

            # ---- 火焰/烟雾: 隔帧推理绑核1, 结果缓存复用 ----
            fs_event = None
            fs_dets = self.fs_dets_cache
            if self.fs_det is not None:
                frame_i += 1
                try:
                    if frame_i % self.fs_every_n == 0:
                        fs_dets, _fs_ms = self.fs_det.infer(frame)
                        self.fs_dets_cache = fs_dets
                        fs_event = self.fs_alarm.update(fs_dets, time.time(),
                                                        img_size=(w, h))
                    self.fs_status_txt = self.fs_alarm.status(time.time())
                except Exception as e:
                    # 隔离降级: 视频/入侵/舵机继续, 只关火烟链路
                    _log("错误", "火烟链路异常已隔离(不影响其他功能): %s" % e)
                    self.fs_det = None
                    self.fs_status_txt = "error"

            # ---- 画框层(受 display 开关/透明度控制, 状态文字始终不透明) ----
            dp = self.disp
            if dp["show_boxes"]:
                base = frame.copy() if dp["box_alpha"] < 1.0 else frame
                if dp["show_zone"]:
                    self.intr.draw_zone(base, alarming=alarming)
                if dp["show_person"]:
                    draw_boxes(base, tracks_info, lw=dp["lw"], scale=dp["fs"])
                if self.fs_det is not None and dp["show_fire"]:
                    for d in fs_dets:
                        if d["name"] == "fire" and \
                                self.fs_alarm.is_static_here(d["bbox"], (w, h)):
                            d["ignored"] = True
                    draw_detections(base, fs_dets, lw=dp["lw"], scale=dp["fs"])
                if dp["box_alpha"] < 1.0:
                    cv2.addWeighted(base, dp["box_alpha"], frame,
                                    1.0 - dp["box_alpha"], 0.0, frame)
            else:
                # 全关模式: 只给左上角一个小提示, 避免误以为画面卡死
                cv2.putText(frame, "overlay OFF", (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            cv2.putText(frame, "%.1f ms  persons=%d  %s" %
                        (ms, len(dets), status),
                        (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                        (0, 0, 255) if alarming else (0, 255, 0), 2)
            if self.fs_det is not None:
                fs_col = (0, 0, 255) if ("FIRE" in self.fs_status_txt) else \
                         ((0, 165, 255) if "SMOKE" in self.fs_status_txt.upper()
                          else (0, 255, 255))
                cv2.putText(frame, "fire/smoke: %s" % self.fs_status_txt,
                            (10, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.75, fs_col, 2)

            if event:
                self.on_intrusion(event, frame.copy())
            if fs_event:
                self.on_fire_smoke(fs_event, frame.copy())
            if self.rec is not None:
                self.rec.feed(frame)   # 喂显示帧(含框), 内部限流10fps

            with self.lock:
                self.frame = frame
                self.info = {"ms": round(ms, 1), "persons": len(dets),
                             "status": status, "alarming": alarming,
                             "fire_smoke": self.fs_status_txt}
        cap.release()

    @staticmethod
    def _iou(a, b):
        ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
        ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
        inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
        sa = (a[2] - a[0]) * (a[3] - a[1])
        sb = (b[2] - b[0]) * (b[3] - b[1])
        return inter / (sa + sb - inter + 1e-9)

    def run(self):
        th = threading.Thread(target=self.worker, daemon=True)
        th.start()
        from stream import create_app
        app = create_app(self)
        port = self.cfg.get("web", {}).get("port", 5000)
        _log("启动", "视频: http://<板子IP>:%d (页面右上角=控制台)" % port)
        app.run(host="0.0.0.0", port=port, threaded=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(ROOT, "config.yaml"))
    ap.add_argument("--no-alarm", action="store_true", help="只检测不报警")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _logsetup(os.path.join(ROOT, cfg["record"].get("save_dir", "records"),
                           "logs", "run.log"))
    _log("启动", "日志: %s (每次启动覆盖)" % _logpath())
    app = SecurityApp(cfg, no_alarm=args.no_alarm)
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        app.alarm.release()
        app.det.release()
        if getattr(app, "fs_det", None):
            app.fs_det.release()


if __name__ == "__main__":
    main()
