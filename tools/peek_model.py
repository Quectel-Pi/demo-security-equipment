#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""peek_model.py - rknn model autopsy on board (one-shot truth tool)

Brute-forces input size (640/416/512/320), then dumps the raw output
tensors: shape / min / max / top scores under v8-style interpretation.
Use it to debug ANY rknn model that "loads but never detects".

Board usage (keep camera pointed at a real flame, e.g. phone video!):
  /opt/py312/bin/python3.12 tools/peek_model.py models/fire_yolov8n.rknn
"""
import sys
import os

import numpy as np
import cv2

model = sys.argv[1] if len(sys.argv) > 1 else "models/fire_yolov8n.rknn"

cap = cv2.VideoCapture("/dev/video73", cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
frame = None
for _ in range(30):
    ok, f = cap.read()
    if ok:
        frame = f
        break
cap.release()
if frame is None:
    sys.exit("camera read failed")
print("test frame: %dx%d  (point camera at the flame before running!)"
      % (frame.shape[1], frame.shape[0]))

from rknnlite.api import RKNNLite

rk = RKNNLite()
if rk.load_rknn(model) != 0:
    sys.exit("load_rknn failed")
if rk.init_runtime() != 0:
    sys.exit("init_runtime failed")
print("runtime ready, probing input sizes...")

hit = None
for sz in (640, 416, 512, 320, 256):
    blob = cv2.cvtColor(cv2.resize(frame, (sz, sz)), cv2.COLOR_BGR2RGB)
    blob = np.expand_dims(blob, 0)
    outs = rk.inference(inputs=[blob])
    ok = outs is not None and len(outs) and outs[0] is not None
    print("  input %3d -> %s" % (sz, "accepted" if ok else "REJECTED(None)"))
    if ok:
        hit = (sz, outs)
        break

if hit is None:
    sys.exit("NO input size accepted?! model broken or needs float input")

sz, outs = hit
print("\n== raw outputs (input %d) ==" % sz)
for i, o in enumerate(outs):
    a = np.asarray(o, dtype=np.float32)
    print("out[%d] shape=%s min=%.3f max=%.3f mean=%.3f"
          % (i, a.shape, a.min(), a.max(), a.mean()))
    if a.ndim == 3:
        a = a[0]
    if a.ndim != 2:
        continue
    # try both layouts: (C,N) with C in 5..10, or (N,C)
    for lay, m in (("C,N", a), ("N,C", a.T if a.shape[0] > 100 else None)):
        if m is None:
            continue
        if m.shape[0] < 5 or m.shape[0] > 40:
            continue
        sc = m[4:]                       # (nc, N)
        top = sc.max(axis=1)
        arg = sc.argmax(axis=1)
        print("  interpret %s C=%d N=%d  class_maxscore=%s at row %s"
              % (lay, m.shape[0], m.shape[1],
                 np.round(top, 3).tolist(), arg.tolist()[:3]))
        # raw (no sigmoid) AND sigmoid both
        for name, vals in (("raw", sc.max(1)), ("sig", 1/(1+np.exp(-sc.max(1))))):
            k = np.argsort(vals)[-5:][::-1]
            print("    %s top5: %s" % (name, np.round(vals[k], 4).tolist()))

# also dump a 2nd frame with a totally blank scene? skip - one frame is enough
print("\ndone. paste everything above.")
