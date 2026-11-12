#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fire_lab.py - fire/smoke standalone diagnostic lab (fully decoupled)

Not touched into main.py pipeline. Loads one rknn model by itself,
prints per-update diagnostics to terminal, so you can SEE why a
false positive survives (raw score / center movement / static count).

Board usage (py312 has rknnlite):
  /opt/py312/bin/python3.12 tools/fire_lab.py \
      --model models/fire_smoke.rknn --names fire,other,smoke

  # colleague's D-Fire model (yolov5 output layout):
  /opt/py312/bin/python3.12 tools/fire_lab.py \
      --model models/dfire_yolov5s.rknn --names fire,smoke --layout v5

Ctrl+C (or --seconds timeout) -> summary table for model comparison.
"""
import os
import sys
import time
import math
import argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("src/vision", "src/logic"):
    sys.path.insert(0, os.path.join(ROOT, _sub.replace("/", os.sep)))

import cv2  # noqa: E402
from fire_smoke import FireSmokeDetector  # noqa: E402
from fire_alarm import FireSmokeAlarm, _center  # noqa: E402

ap = argparse.ArgumentParser(description="fire/smoke edge diagnostic")
ap.add_argument("--model", required=True, help="path under models/ or abs path")
ap.add_argument("--names", required=True, help="class order as in training data.yaml")
ap.add_argument("--layout", default="v8", choices=["v8", "v5"])
ap.add_argument("--conf", type=float, default=0.30,
                help="detector raw threshold (keep LOW to inspect scores)")
ap.add_argument("--alarm-conf", type=float, default=0.65,
                help="fire threshold used by alarm logic")
ap.add_argument("--dev", type=int, default=73, help="/dev/videoN")
ap.add_argument("--seconds", type=float, default=60)
ap.add_argument("--every", type=int, default=2, help="run fs infer every N frames")
ap.add_argument("--save", default="", help="optional dir: save annotated frames")
a = ap.parse_args()

model = a.model if os.path.isabs(a.model) else os.path.join(ROOT, a.model)
names = a.names.split(",")
det = FireSmokeDetector(model, names=names, conf_threshold=a.conf,
                        layout=a.layout)
alarm = FireSmokeAlarm(fire_conf=a.alarm_conf)

cap = cv2.VideoCapture("/dev/video%d" % a.dev, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
if not cap.isOpened():
    sys.exit("camera /dev/video%d open failed" % a.dev)

if a.save:
    os.makedirs(a.save, exist_ok=True)

prev_fire_c = None            # lab's own movement metric (independent view)
stats = {n: [0, 0.0] for n in names + ["none"]}   # hits, max_score
locks = 0
was_locked = False
alarms = []
fi = ui = 0
t_end = time.time() + a.seconds

print("== fire_lab | model=%s layout=%s raw_conf=%.2f alarm_fire=%.2f | %.0fs =="
      % (os.path.basename(model), a.layout, a.conf, a.alarm_conf, a.seconds))

try:
    while time.time() < t_end:
        ok, frame = cap.read()
        if not ok:
            break
        fi += 1
        if fi % a.every:
            continue
        ui += 1
        dets, ms = det.infer(frame)
        h, w = frame.shape[:2]
        diag = math.hypot(w, h)
        now = time.time()
        evt = alarm.update(dets, now, img_size=(w, h))
        if alarm._static_locked and not was_locked:
            locks += 1
        was_locked = alarm._static_locked
        if evt:
            alarms.append("%s t=%.1fs" % (evt["type"], now))

        # per-class stats (one hit per class per update)
        seen = set()
        for d in dets:
            n = d["name"]
            if n not in stats:
                stats[n] = [0, 0.0]
            if d["score"] > stats[n][1]:
                stats[n][1] = d["score"]
            seen.add(n)
        for n in seen:
            stats[n][0] += 1
        if not dets:
            stats["none"][0] += 1

        # movement of best fire box (lab-independent metric)
        move_s = ""
        fires = [d for d in dets if d["name"] == "fire"]
        if fires:
            fd = max(fires, key=lambda d: d["score"])
            c = _center(fd["bbox"])
            if prev_fire_c:
                move_s = " move=%.1f%%" % (
                    math.hypot(c[0] - prev_fire_c[0],
                               c[1] - prev_fire_c[1]) / diag * 100.0)
            prev_fire_c = c
        else:
            prev_fire_c = None

        tag = " | ".join("%s %.2f c(%d,%d)" % (d["name"], d["score"],
                                               *_center(d["bbox"]))
                         for d in dets if d["name"] != "other") or "--"
        print("fs=%03d %3.0fms| %-38s%s s_cnt=%2d lock=%d strk=%d %s"
              % (ui, ms, tag, move_s, alarm._static_cnt,
                 int(alarm._static_locked), alarm._fire_streak,
                 alarm.status(now)))
        if a.save and ui % 10 == 0:
            from fire_smoke import draw_detections
            draw_detections(frame, dets)
            cv2.imwrite(os.path.join(a.save, "u%03d.jpg" % ui), frame)
except KeyboardInterrupt:
    pass
cap.release()
det.release()

print("\n==== SUMMARY  updates=%d (camera frames=%d)  static_lock_events=%d ===="
      % (ui, fi, locks))
for n in sorted(stats):
    hits, mx = stats[n]
    print("  %-6s hits=%4d  max_score=%.2f" % (n, hits, mx))
if alarms:
    print("  ALARM events:", "; ".join(alarms))
else:
    print("  ALARM events: none")
print("  compare models by: fire hits/max on BADGE scene should be LOW,"
      " on CANDLE scene HIGH with low move%%")
