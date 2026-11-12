# -*- coding: utf-8 -*-
"""
ZPServo - driver for ZhongLing single-bus digital servo (ZP15S).

Protocol (ASCII over UART 115200, half duplex single wire):
    move : "#000P1500T1000!"   id000 pulse1500 time1000ms
    rad  : "#000PRAD!"      -> "#000P1500!"
    ping : "#000PID!"       -> "#000P!"
    ulk/ulr: torque release / restore

Pulse 0500~2500 maps 0~270 deg in default MOD1 (270 clockwise).
Header wiring (verified 2026-11-11, qpi-config whitelist = i2c3|i2c7|spi1|uart1):
  enable once: sudo qpi-config uart1 enable && sudo reboot
  then: servo orange(signal)/brown(GND)/red(V+) to the UART1 header pins —
  numbering follows board silkscreen, see README wiring card (§8).
  (usb-ttl module on /dev/ttyUSB0 is the zero-config fallback)

Layer A usage (manual/API):
    s = ZPServo("/dev/ttyS4", sid=0)
    s.move(90)            # 90 deg, default 800ms
    s.center()            # pulse 1500
Layer B (PTZ) will call move() from the detection loop.
"""
import re


P_MIN, P_MAX = 500, 2500


class ZPServo:
    def __init__(self, port="/dev/ttyS4", sid=0, baud=115200,
                 angle_max=270.0, default_time_ms=800):
        import serial                      # pip: pyserial
        self.sid = int(sid)
        self.angle_max = float(angle_max)
        self.default_t = int(default_time_ms)
        self.ser = serial.Serial(port, baud, timeout=0.25)

    # ---------- low level ----------
    def _send(self, cmd):
        self.ser.write(("%s" % cmd).encode("ascii"))
        self.ser.flush()

    def _ask(self, cmd, pattern):
        """half-duplex: RX may be wired or not -> None means 'no echo',
        which is NORMAL for control-only wiring (never treat as error)."""
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self._send(cmd)
        deadline = 0.5
        buf = ""
        import time
        t0 = time.time()
        while time.time() - t0 < deadline:
            buf += self.ser.read(32).decode("ascii", errors="ignore")
            m = re.search(pattern, buf)
            if m:
                return m
        return None

    # ---------- public API (Layer A atoms) ----------
    def pulse(self, p, time_ms=None):
        p = int(max(P_MIN, min(P_MAX, p)))
        t = int(time_ms if time_ms is not None else self.default_t)
        t = max(0, min(9999, t))
        self._send("#%03dP%04dT%04d!" % (self.sid, p, t))

    def move(self, angle, time_ms=None):
        """angle 0..270 (MOD1). bounds-clamped, no exception."""
        angle = max(0.0, min(self.angle_max, float(angle)))
        p = P_MIN + (P_MAX - P_MIN) * angle / self.angle_max
        self.pulse(int(round(p)), time_ms)
        return angle

    def center(self, time_ms=None):
        self.pulse(1500, time_ms)

    def stop_here(self):
        self._send("#%03dPDST!" % self.sid)

    def release_torque(self):
        self._send("#%03dPULK!" % self.sid)

    def restore_torque(self):
        self._send("#%03dPULR!" % self.sid)

    def set_mode(self, n):
        """1/2:270 cw/ccw  3/4:180 cw/ccw  5..8:motor modes."""
        self._send("#%03dPMOD%d!" % (self.sid, int(n)))

    # ---------- optional read-back (needs RX wired) ----------
    def ping(self):
        return self._ask("#%03dPID!" % self.sid,
                         r"#%03dP!" % self.sid) is not None

    def read_pulse(self):
        m = self._ask("#%03dPRAD!" % self.sid, r"#\d{3}P(\d{4})!")
        return int(m.group(1)) if m else None

    def read_angle(self):
        p = self.read_pulse()
        if p is None:
            return None
        return (p - P_MIN) / float(P_MAX - P_MIN) * self.angle_max

    def close(self):
        try:
            self.center(300)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
