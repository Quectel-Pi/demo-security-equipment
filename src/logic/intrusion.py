# -*- coding: utf-8 -*-
"""人员入侵检测模块 (demo-security-equipment)

判定逻辑:
  1. 简易 IoU 跟踪: 相邻帧人框重叠率 >= iou_thr 视为同一个人(track)
  2. track 人框中心点落在入侵区域内 -> 开始计时 (inside_since)
  3. 同一 track 在区域内持续 >= hold_seconds -> 触发入侵事件
  4. 触发后进入 cooldown_s 冷却, 期间不再重复报警
  5. track 丢失超过 track_timeout -> 删除(计时随之清零)

区域 zone 用归一化坐标 [0,1], 换分辨率/换摄像头不用重新标点。
"""
import cv2
import numpy as np
import time


def iou(a, b):
    """两个 xyxy 框的 IoU"""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    sa = (a[2] - a[0]) * (a[3] - a[1])
    sb = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (sa + sb - inter + 1e-9)


class Track:
    _next_id = 1

    def __init__(self, bbox, ts):
        self.id = Track._next_id
        Track._next_id += 1
        self.bbox = tuple(float(v) for v in bbox)
        self.last_seen = ts
        self.inside_since = None   # None = 当前不在区域内

    def center(self):
        return ((self.bbox[0] + self.bbox[2]) / 2.0,
                (self.bbox[1] + self.bbox[3]) / 2.0)


class IntrusionDetector:
    def __init__(self, zone_norm, hold_seconds=5.0, cooldown_s=30.0,
                 iou_thr=0.30, track_timeout=0.8):
        """
        zone_norm: [[x,y],[x,y],...] 归一化多边形(至少3点, 顺时针/逆时针均可)
        hold_seconds: 区域内持续多少秒触发报警
        cooldown_s: 一次报警后的冷却时间
        """
        self.zone_norm = np.array(zone_norm, dtype=np.float32)
        self.hold = float(hold_seconds)
        self.cooldown = float(cooldown_s)
        self.iou_thr = float(iou_thr)
        self.track_timeout = float(track_timeout)
        self.tracks = []
        self.last_alarm_ts = 0.0
        self._cache_key = None      # (w,h) 缓存像素 zone
        self._zone_px = None

    # ---------- 工具 ----------
    def zone_pixel(self, w, h):
        """归一化区域 -> 当前帧像素坐标(带缓存)"""
        if self._cache_key != (w, h):
            self._zone_px = (self.zone_norm * [w, h]).astype(np.float32)
            self._cache_key = (w, h)
        return self._zone_px

    def in_zone(self, cx, cy, w, h):
        zp = self.zone_pixel(w, h)
        return cv2.pointPolygonTest(zp, (float(cx), float(cy)), False) >= 0

    # ---------- 主接口: 每帧调用 ----------
    def update(self, dets, img_w, img_h, now=None):
        """
        dets: pose_detector 输出 [{'bbox':(x1,y1,x2,y2), 'score':..}, ...]
        返回: 触发的事件 dict 或 None
        """
        now = now if now is not None else time.time()
        boxes = [d["bbox"] for d in dets]

        # 1) IoU 贪心匹配: track <-> 本帧框
        pairs = []
        for ti, t in enumerate(self.tracks):
            # 超时 track 不参与匹配
            if now - t.last_seen > self.track_timeout:
                continue
            for bi, b in enumerate(boxes):
                r = iou(t.bbox, b)
                if r >= self.iou_thr:
                    pairs.append((r, ti, bi))
        pairs.sort(reverse=True)
        used_t, used_b = set(), set()
        for r, ti, bi in pairs:
            if ti in used_t or bi in used_b:
                continue
            used_t.add(ti)
            used_b.add(bi)
            t = self.tracks[ti]
            t.bbox = tuple(float(v) for v in boxes[bi])
            t.last_seen = now

        # 2) 没匹配上的框 -> 新建 track
        for bi, b in enumerate(boxes):
            if bi not in used_b:
                self.tracks.append(Track(b, now))

        # 3) 清理超时 track
        self.tracks = [t for t in self.tracks
                       if now - t.last_seen <= self.track_timeout]

        # 4) 区域计时 + 事件判定
        event = None
        for t in self.tracks:
            cx, cy = t.center()
            if self.in_zone(cx, cy, img_w, img_h):
                if t.inside_since is None:
                    t.inside_since = now
                dur = now - t.inside_since
                if (dur >= self.hold and
                        now - self.last_alarm_ts >= self.cooldown):
                    self.last_alarm_ts = now
                    event = {"type": "intrusion", "track_id": t.id,
                             "seconds": round(dur, 1),
                             "bbox": t.bbox, "ts": now}
            else:
                t.inside_since = None   # 离开区域, 该 track 计时清零

        return event

    # ---------- 状态(画面上显示用) ----------
    def status(self, img_w, img_h, now=None):
        """返回 (状态文字, 是否在报警冷却, 最长区域内秒数)"""
        now = now if now is not None else time.time()
        worst = 0.0
        for t in self.tracks:
            if t.inside_since is not None:
                worst = max(worst, now - t.inside_since)
        if now - self.last_alarm_ts < 2.0 and self.last_alarm_ts > 0:
            return "ALARM!", True, worst
        if worst > 0:
            return "INTRUDER %.1fs/%.0fs" % (worst, self.hold), False, worst
        return "idle", False, worst

    def draw_zone(self, frame, color=(0, 220, 220), alarm_color=(0, 0, 255),
                  alarming=False):
        """把入侵区域画成多边形叠在画面上"""
        h, w = frame.shape[:2]
        zp = self.zone_pixel(w, h).astype(np.int32)
        c = alarm_color if alarming else color
        cv2.polylines(frame, [zp.reshape(-1, 1, 2)], True, c, 2)
