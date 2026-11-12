# -*- coding: utf-8 -*-
"""火焰/烟雾检测推理模块 (demo-security-equipment 阶段4)

模型: ultralytics 原生导出的 yolov8 detect (经 rknn-toolkit2 fp16 转换)
输出格式: (1, 4+nc, 8400)  —— xywh已解码中心点 + 各类logit, 无关键点
类别: 由 config 指定, 例如 ['fire','other','smoke']

与 pose_detector 的区别:
  - 多类别(取每类最高分), 不是单类
  - 不输出关键点
运行时: .rknn -> rknnlite(NPU) / .onnx -> OpenCV DNN(CPU调试)
线程安全: 每个实例私有 context, 独立线程调用(勿跨线程共享实例)
"""
import os
import time

import cv2
import numpy as np

from pose_detector import Letterbox, _nms, _sigmoid


class FireSmokeDetector:
    def __init__(self, model_path, names, input_size=(640, 640),
                 conf_threshold=0.5, nms_threshold=0.45, core_mask=None,
                 layout="v8"):
        """layout: 'v8' ultralytics输出(4+nc, N) / 'v5' yolov5输出(N, 5+nc)"""
        self.model_path = str(model_path)
        self.names = list(names)                 # 类别顺序须与data.yaml一致
        self.input_size = tuple(input_size)
        self.conf_thr = float(conf_threshold)
        self.nms_thr = float(nms_threshold)
        self.layout = layout
        self.net = None
        self.rknn = None
        self.infer_ms = 0.0

        if self.model_path.endswith(".rknn"):
            from rknnlite.api import RKNNLite
            self.rknn = RKNNLite()
            if self.rknn.load_rknn(self.model_path) != 0:
                raise RuntimeError("加载 rknn 失败: %s" % self.model_path)
            kwargs = {}
            if core_mask:                        # RK3576: 0xFF自动/1=核0/2=核1
                kwargs["core_mask"] = core_mask
            if self.rknn.init_runtime(**kwargs) != 0:
                raise RuntimeError("init_runtime失败(NPU?)")
            # 输入尺寸以 config.input_size 为准(此版rknnlite无query接口)
        else:
            self.net = cv2.dnn.readNetFromONNX(self.model_path)
            self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

    # ---------------- 推理 ----------------
    def infer(self, frame_bgr):
        """返回 [{'bbox':xyxy原图, 'name':类名, 'score':..}, ...], 耗时ms"""
        lb = Letterbox(*self.input_size)
        img = lb(frame_bgr)
        blob = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        t0 = time.time()
        if self.rknn is not None:
            outputs = self.rknn.inference(inputs=[np.expand_dims(blob, 0)])
        else:
            self.net.setInput(blob.transpose(2, 0, 1)[None].astype(np.float32))
            outputs = [self.net.forward()]
        self.infer_ms = (time.time() - t0) * 1000.0
        return self._postprocess(outputs, lb), self.infer_ms

    def _postprocess(self, outputs, lb):
        out = np.asarray(outputs[0])
        if out.ndim == 3 and out.shape[0] == 1:
            out = out[0]                         # (C, N) 或 (N, C)
        if self.layout == "v5":
            return self._postprocess_v5(out, lb)
        nc1 = 4 + len(self.names)
        if out.shape[0] != nc1 and out.shape[-1] == nc1:
            out = out.T
        if out.ndim != 2 or out.shape[0] < nc1:
            return []                            # 格式不符, 导出时加 --dynamic 或换简化导出

        cx, cy, w, h = out[:4]                   # 输入尺度
        scores = out[4:nc1]                      # (nc, N) logit
        if scores.max() > 1.0 or scores.min() < 0.0:
            scores = _sigmoid(scores)

        best_cls = scores.argmax(0)              # (N,)
        best_score = scores.max(0)               # (N,)
        mask = best_score >= self.conf_thr
        if not mask.any():
            return []

        b = np.stack([cx - w / 2, cy - h / 2,
                      cx + w / 2, cy + h / 2], -1)   # xyxy 输入尺度
        return self._finalize(b, best_score, best_cls, mask, lb)

    def _postprocess_v5(self, out, lb):
        """yolov5 导出: (N, 5+nc) = [cx,cy,w,h(px), obj, cls...]
        注意: v5导出的obj/cls列已经过sigmoid; 而坐标列本来就有负数,
        绝不能用整体min/max判断是否补sigmoid(会二次sigmoid→分数全压平)"""
        nc = len(self.names)
        if out.ndim != 2:
            return []
        if out.shape[1] != 5 + nc:
            if out.shape[0] == 5 + nc:
                out = out.T
            else:
                return []
        obj, cls = out[:, 4:5], out[:, 5:]
        score_cols = out[:, 4:]                    # 只看分数列
        if score_cols.min() < 0.0 or score_cols.max() > 1.0:   # 是raw logits
            obj, cls = _sigmoid(obj), _sigmoid(cls)
        probs = obj * cls                        # (N, nc)
        best_cls = probs.argmax(1)
        best_score = probs.max(1)
        mask = best_score >= self.conf_thr
        if not mask.any():
            return []
        cx, cy, w, h = out[:, 0], out[:, 1], out[:, 2], out[:, 3]
        b = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], -1)
        return self._finalize(b, best_score, best_cls, mask, lb)

    def _finalize(self, b, best_score, best_cls, mask, lb):
        bb, s, c = b[mask], best_score[mask], best_cls[mask]
        keep = _nms(bb, s, self.nms_thr)
        results = []
        for i in keep:
            box = lb.back(bb[i].reshape(-1, 2)).reshape(-1)
            results.append({"bbox": (float(box[0]), float(box[1]),
                                     float(box[2]), float(box[3])),
                            "name": self.names[c[i]],
                            "score": float(s[i])})
        return results

    def release(self):
        if self.rknn is not None:
            try:
                self.rknn.release()
            except Exception:
                pass


def draw_detections(frame, dets, colors=None, lw=2, scale=0.55):
    """按类别画框: fire红 smoke黄 other灰
    d["ignored"]=True 的(被静态过滤锁死的)画灰框标注 static-ignored"""
    colors = colors or {"fire": (0, 0, 255), "smoke": (0, 220, 255),
                        "other": (150, 150, 150)}
    for d in dets:
        x1, y1, x2, y2 = map(int, d["bbox"])
        if d.get("ignored"):
            cv2.rectangle(frame, (x1, y1), (x2, y2), (120, 120, 120), lw)
            cv2.putText(frame, "%s STATIC-ignored" % d["name"],
                        (x1, max(0, y1 - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, scale - 0.05,
                        (120, 120, 120), 1)
            continue
        c = colors.get(d["name"], (0, 255, 0))
        cv2.rectangle(frame, (x1, y1), (x2, y2), c, lw)
        cv2.putText(frame, "%s %.2f" % (d["name"], d["score"]),
                    (x1, max(0, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, c, max(1, lw - 1))
