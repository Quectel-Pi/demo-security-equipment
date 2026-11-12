# -*- coding: utf-8 -*-
"""火焰/烟雾报警分级逻辑 (demo-security-equipment)

分级策略(抗误报核心):
  fire  L3紧急: 连续 fire_frames 帧都有 fire 检出 -> 长报警(默认10s)
  smoke L1警告: 最近 smoke_window 帧中命中 >= smoke_hits 帧 -> 短提示(默认3s)
                (烟雾半透明、飘忽, 单帧命中大多是反光/蒸汽误报, 滑动窗口杀掉大部分)
  other 不处理: 训练数据里的干扰类, 只画灰框演示"认识但不报警"
两级冷却互相独立: fire_cooldown / smoke_cooldown
"""
from collections import deque
import math


def _center(box):
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


class FireSmokeAlarm:
    def __init__(self, fire_conf=0.5, smoke_conf=0.5,
                 fire_frames=3, smoke_window=30, smoke_hits=10,
                 fire_cooldown=30.0, smoke_cooldown=60.0,
                 fire_min_area=0.008, smoke_min_area=0.01,
                 static_move_thr=0.015, static_reset_thr=0.05,
                 static_lock=10, static_release=8, cand_conf=0.35):
        self.fire_conf = fire_conf
        self.smoke_conf = smoke_conf
        self.fire_frames_need = fire_frames
        self.smoke_window = smoke_window
        self.smoke_hits = smoke_hits
        self.fire_cd = fire_cooldown
        self.smoke_cd = smoke_cooldown
        self.fire_min_area = fire_min_area      # 框面积/画面面积 下限
        self.smoke_min_area = smoke_min_area
        # ---- 静态目标过滤(质心位移版, 抗低分框尺寸抖动) ----
        # 框中心位移 < 对角线1.5% 记一次"没动"; >5% 判"活体"清零解锁
        self.static_move_thr = static_move_thr
        self.static_reset_thr = static_reset_thr
        self.static_lock = static_lock
        self.static_release = static_release
        self.cand_conf = float(cand_conf)   # 候选池分数下限(静态观测用)

        self._fire_streak = 0
        self._smoke_ring = deque(maxlen=smoke_window)  # 1/0 每帧烟雾命中
        self._last_fire_ts = 0.0
        self._last_smoke_ts = 0.0
        self._last_fire_det = None    # 报警时的框(存证/显示用)
        self._last_smoke_det = None
        self._prev_fire_box = None    # 上一帧fire框(静态对比)
        self._static_cnt = 0          # 连续静态计数
        self._static_locked = False   # 已判定为静态物体
        self._miss_cnt = 0            # fire连续未检出计数
        self.last_static_box = None   # 锁定的静态框(供画面灰显)

    def is_static_here(self, bbox, img_size=None):
        """main.py 画框用: 该框是否落在被锁定的静态位置上"""
        if not (self._static_locked and self.last_static_box):
            return False
        diag = math.hypot(img_size[0], img_size[1]) if img_size else 1000.0
        c0, c1 = _center(self.last_static_box), _center(bbox)
        return (math.hypot(c1[0] - c0[0], c1[1] - c0[1]) / diag
                < self.static_reset_thr)

    def _box_ratio(self, det, img_size):
        """框面积占画面比例(img_size没给就放行)"""
        if img_size is None:
            return 1.0
        x1, y1, x2, y2 = det["bbox"]
        return ((x2 - x1) * (y2 - y1)) / (img_size[0] * img_size[1])

    def update(self, dets, now, img_size=None):
        """dets: FireSmokeDetector.infer 输出; now: time.time()
        img_size: (w,h) 传入后启用最小面积过滤(杀小面积误报)
        返回事件 dict 或 None (同帧火烟都中优先报火)"""
        hit_fire, hit_smoke = None, None
        for d in dets:
            if d["name"] == "fire" and d["score"] >= self.fire_conf:
                if self._box_ratio(d, img_size) < self.fire_min_area:
                    continue
                if hit_fire is None or d["score"] > hit_fire["score"]:
                    hit_fire = d
            elif d["name"] == "smoke" and d["score"] >= self.smoke_conf:
                if self._box_ratio(d, img_size) < self.smoke_min_area:
                    continue
                if hit_smoke is None or d["score"] > hit_smoke["score"]:
                    hit_smoke = d

        # ---- fire 静态观测: 候选池(含被面积闸拦掉的小框)全部纳入观察 ----
        # 关键修正: 静态判定不能挂在 hit_fire 后面, 否则远处小误报被
        # 面积过滤拦在门外, 静态过滤永远轮不到, 显示层红框无法转灰
        cands = [d for d in dets if d["name"] == "fire"
                 and d["score"] >= self.cand_conf]
        obs = max(cands, key=lambda d: d["score"]) if cands else None
        diag = math.hypot(img_size[0], img_size[1]) if img_size else 1000.0
        if obs:
            self._miss_cnt = 0
            if self._prev_fire_box is not None:
                c0, c1 = _center(self._prev_fire_box), _center(obs["bbox"])
                moved = math.hypot(c1[0] - c0[0], c1[1] - c0[1]) / diag
                if moved < self.static_move_thr:
                    self._static_cnt += 1
                elif moved > self.static_reset_thr:
                    self._static_cnt = 0
                    self._static_locked = False      # 大幅移动=活体,解锁
            if self._static_cnt >= self.static_lock:
                self._static_locked = True
            self._prev_fire_box = obs["bbox"]
            if self._static_locked:
                self.last_static_box = obs["bbox"]
                # 锁定后: 静态位置上的fire命中拦掉(非同位置的火照常走)
                if hit_fire is not None and \
                        self.is_static_here(hit_fire["bbox"], img_size):
                    hit_fire = None
        else:
            self._miss_cnt += 1
            if self._miss_cnt >= self.static_release:
                self._static_locked = False           # 消失太久, 状态复位
                self._static_cnt = 0
                self._prev_fire_box = None
                self.last_static_box = None

        # ---- fire: 连续N帧 ----
        self._fire_streak = self._fire_streak + 1 if hit_fire else 0
        if hit_fire:
            self._last_fire_det = hit_fire

        # ---- smoke: 滑动窗口计数 ----
        self._smoke_ring.append(1 if hit_smoke else 0)
        if hit_smoke:
            self._last_smoke_det = hit_smoke
        hits = sum(self._smoke_ring)

        if (self._fire_streak >= self.fire_frames_need and
                now - self._last_fire_ts >= self.fire_cd):
            self._last_fire_ts = now
            return {"type": "fire", "level": "L3",
                    "det": hit_fire or self._last_fire_det}

        if (len(self._smoke_ring) >= self.smoke_hits and
                hits >= self.smoke_hits and
                now - self._last_smoke_ts >= self.smoke_cd):
            self._last_smoke_ts = now
            return {"type": "smoke", "level": "L1",
                    "det": hit_smoke or self._last_smoke_det,
                    "hits": hits, "window": self.smoke_window}
        return None

    def status(self, now):
        """给画面/状态接口的文字: idle / SMOKE x/30 / FIRE streak / STATIC"""
        if self._static_locked:
            return "fire:STATIC(ignored)"
        if self._fire_streak > 0:
            return "FIRE? %d/%d" % (self._fire_streak, self.fire_frames_need)
        if now - self._last_fire_ts < 3.0:
            return "FIRE ALARM!"
        hits = sum(self._smoke_ring)
        if hits >= self.smoke_hits:
            return "SMOKE x%d" % hits
        if hits > 0:
            return "smoke %d/%d" % (hits, self.smoke_hits)
        if now - self._last_smoke_ts < 3.0:
            return "SMOKE WARN!"
        return "idle"
