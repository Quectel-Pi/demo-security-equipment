# -*- coding: utf-8 -*-
"""applog - 统一日志: [YYYY-mm-dd HH:MM:SS] [类别] 内容

类别: 启动 / 事件 / 取证 / 舵机 / 错误 / 系统
所有 print(含第三方库输出)同时镜像到 run.log(每次启动覆盖)。
用法:
    import applog
    applog.setup("/path/records/logs/run.log")
    applog.log("事件", "入侵: 区域内滞留5.1s")
"""
import os
import sys
import time

_logpath = None


class _Tee:
    def __init__(self, con, f):
        self.con = con
        self.f = f

    def write(self, s):
        try:
            self.con.write(s)
        except Exception:
            pass
        try:
            self.f.write(s)
            self.f.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.con.flush()
        except Exception:
            pass

    def isatty(self):
        return self.con.isatty()


def setup(logpath):
    """开启落盘镜像, 返回日志绝对路径"""
    global _logpath
    d = os.path.dirname(logpath)
    if d:
        os.makedirs(d, exist_ok=True)
    f = open(logpath, "w", encoding="utf-8")
    sys.stdout = _Tee(sys.__stdout__, f)
    sys.stderr = _Tee(sys.__stderr__, f)
    _logpath = os.path.abspath(logpath)
    return _logpath


def path():
    return _logpath


def log(cat, msg):
    print("[%s] [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), cat, msg))
