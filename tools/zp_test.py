#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zp_test.py - ZP15S bus servo standalone tester (decoupled, no main app)

Board usage (install pyserial first if missing):
  /opt/py312/bin/python3.12 -m pip install pyserial
  /opt/py312/bin/python3.12 tools/zp_test.py --port /dev/ttyS4 --demo sweep

Wiring check order:
  1) TX-only works: sweep/move (servo moves = TX + power + gnd OK)
  2) RX also wired: ping/rad reply (needs RX<->signal, half duplex)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src", "servo"))

import argparse  # noqa: E402
from servo_zp import ZPServo  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--port", default="/dev/ttyUSB0")
ap.add_argument("--id", type=int, default=0)
ap.add_argument("--demo", default="sweep",
                choices=["ping", "sweep", "move", "rad", "step"])
ap.add_argument("--angle", type=float, default=90)
a = ap.parse_args()

s = ZPServo(a.port, sid=a.id)
print("opened %s @115200, id=%d" % (a.port, a.id))

if a.demo == "ping":
    print("ping:", s.ping(), "(False is OK if RX not wired yet)")
elif a.demo == "move":
    print("move -> %.0f deg" % a.angle)
    s.move(a.angle)
    time.sleep(1.5)
elif a.demo == "rad":
    print("angle now:", s.read_angle(), "(None = RX not wired)")
elif a.demo == "step":
    for ang in (45, 135, 90):
        print("-> %s" % ang)
        s.move(ang)
        time.sleep(1.0)
else:  # sweep
    print("Ctrl+C to stop. sweep 45->135->45 (clamped to no cable pull)")
    try:
        while True:
            for ang in (45, 90, 135, 90):
                s.move(ang, time_ms=1200)
                print("-> %s deg" % ang)
                time.sleep(1.5)
    except KeyboardInterrupt:
        s.center(800)
        print("centered")
s.close()
