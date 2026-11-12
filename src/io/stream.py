# -*- coding: utf-8 -*-
"""Web 推流 + 状态/抓拍接口 (MJPEG over HTTP)"""
import os
import time

import cv2
from flask import Flask, Response, jsonify, request

from applog import log as _log


def create_app(sec):
    """sec: SecurityApp 实例, 提供 lock/frame/info/on_intrusion 等"""
    app = Flask(__name__)
    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)   # 静音GET刷屏

    def mjpeg():
        last = None
        while True:
            with sec.lock:
                f = sec.frame
            if f is None or f is last:
                time.sleep(0.001)
                continue
            last = f
            ok, jpg = cv2.imencode('.jpg', f, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue
            yield (b'--frame\r\nContent-Type: image/jpeg\r\n\r\n'
                   + jpg.tobytes() + b'\r\n')

    @app.route('/')
    def index():
        return ('<!doctype html><html><head><meta charset="utf-8">'
                '<title>安防监控</title><style>'
                'html,body{margin:0;height:100%;overflow:hidden;'
                'background:linear-gradient(160deg,#f7f8fa,#e8ecf2);'
                'font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif}'
                '#stage{position:fixed;inset:0}'
                '#stage img{width:100vw;height:100vh;object-fit:contain}'
                '#panel{position:absolute;right:16px;top:16px;width:168px;'
                ';background:rgba(255,255,255,.92);backdrop-filter:blur(8px);'
                ';border-radius:12px;color:#333;font-size:13px;'
                ';box-shadow:0 8px 28px rgba(20,30,60,.18);user-select:none;'
                ';overflow:hidden}'
                '#bar{cursor:move;font-size:12px;font-weight:600;color:#5a6472;'
                ';background:#f2f4f8;padding:8px 12px;border-bottom:1px solid #e6eaf0}'
                '.sec{font-size:11px;color:#98a1b3;margin:10px 12px 4px}'
                '.row{display:flex;gap:6px;margin:0 12px}'
                '.row button{flex:1;height:34px;border:1px solid #dde3ec;'
                ';border-radius:8px;background:#f7f9fc;color:#39414f;'
                ';font-size:14px;cursor:pointer}'
                '.row button:active{background:#e8eef7}'
                '.row button.on{background:#fdecec;border-color:#f0b9b9;'
                ';color:#c0392b}'
                '.opt{display:flex;align-items:center;gap:6px;margin:5px 12px;'
                ';font-size:12px;color:#4a5262}'
                '.opt input{accent-color:#3b82f6}'
                '.opt input[type=range]{flex:1}'
                '#tip{margin:8px 12px 12px;font-size:11px;color:#8a94a6;'
                ';min-height:14px}'
                '</style></head><body>'
                '<div id="stage"><img src="/video"></div>'
                '<div id="panel"><div id="bar"><span data-k="panel">控制台</span>'
                '<span id="ang"></span>'
                '<span id="langBtn" style="float:right;cursor:pointer;'
                ';color:#3b82f6;font-weight:400;margin-left:10px">EN</span>'
                '<span id="fold" style="float:right;cursor:pointer;'
                ';color:#8a94a6;font-weight:400">收起</span></div>'
                '<div id="pbody">'
                '<div class="sec" data-k="ptz">云台 · 步进15°</div>'
                '<div class="row"><button onclick="rot(\'left\')">&#9664;</button>'
                '<button onclick="rot(\'center\')">&#9678;</button>'
                '<button onclick="rot(\'right\')">&#9654;</button></div>'
                '<div class="sec" data-k="disp">显示</div>'
                '<label class="opt"><input type="checkbox" id="cB" checked><span data-k="cB">框总开关</span></label>'
                '<label class="opt"><input type="checkbox" id="cP" checked><span data-k="cP">人物框</span></label>'
                '<label class="opt"><input type="checkbox" id="cF" checked><span data-k="cF">火/烟框</span></label>'
                '<label class="opt"><input type="checkbox" id="cZ" checked><span data-k="cZ">区域框</span></label>'
                '<label class="opt"><span data-k="alpha">透明</span><input type="range" id="al" min="20" max="100" value="100"></label>'
                '<div class="sec" data-k="cap">采集</div>'
                '<div class="row"><button onclick="snap()" data-k="snap">拍照</button>'
                '<button id="recBtn" onclick="rec()" data-k="rec">录像</button></div>'
                '<div id="tip"></div></div></div>'
                '<script>'
                'var recOn=false,recT=null;'
                'var I18N={'
                'zh:{title:"安防监控",panel:"控制台",fold:"收起",unfold:"展开",'
                'ptz:"云台 · 步进15°",disp:"显示",cap:"采集",'
                'cB:"框总开关",cP:"人物框",cF:"火/烟框",cZ:"区域框",'
                'alpha:"透明",snap:"拍照",rec:"录像",stop:"停止",'
                'saved:"已保存 ",nofr:"无画面",recing:"录像中, 上限10分钟",'
                'notrec:"未录到",langb:"EN"},'
                'en:{title:"Security Monitor",panel:"Console",fold:"hide",'
                'unfold:"show",ptz:"PTZ · step 15°",disp:"Overlay",cap:"Capture",'
                'cB:"All boxes",cP:"Person",cF:"Fire/Smoke",cZ:"Zone",'
                'alpha:"Alpha",snap:"Snapshot",rec:"Record",stop:"Stop",'
                'saved:"Saved ",nofr:"no frame",recing:"recording (max 10 min)",'
                'notrec:"nothing saved",langb:"中文"}};'
                'var lang=localStorage.getItem("dsm_lang")||"zh";'
                'function T(k){var m=I18N[lang]||{};return m[k]!==undefined?m[k]:k;}'
                'function applyLang(){'
                'document.querySelectorAll("[data-k]").forEach(function(el){'
                'if(el.id==="recBtn"&&recOn)return;'
                'el.textContent=T(el.getAttribute("data-k"));});'
                'document.getElementById("langBtn").textContent=T("langb");'
                'document.title=T("title");'
                'var b=document.getElementById("pbody");'
                'document.getElementById("fold").textContent='
                '(b.style.display==="none")?T("unfold"):T("fold");}'
                'document.getElementById("langBtn").onclick=function(){'
                'lang=(lang==="zh")?"en":"zh";localStorage.setItem("dsm_lang",lang);'
                'applyLang();};'
                'document.getElementById("fold").onclick=function(){'
                'var b=document.getElementById("pbody");'
                'b.style.display=(b.style.display==="none")?"":"none";applyLang();};'
                'function show(j){document.getElementById("ang").textContent='
                'j.angle!=null?("  "+j.angle+"\\u00b0"):"  !";}'
                'function rot(d){fetch("/api/servo?dir="+d)'
                '.then(r=>r.json()).then(show);}'
                'function ck(i){return document.getElementById(i).checked?"1":"0";}'
                'function syncd(){fetch("/api/display?show_boxes="+ck("cB")'
                '+"&show_person="+ck("cP")+"&show_fire="+ck("cF")'
                '+"&show_zone="+ck("cZ")+"&alpha="+(al.value/100));}'
                '["cB","cP","cF","cZ"].forEach(function(i){'
                'document.getElementById(i).onchange=syncd;});'
                'document.getElementById("al").oninput=syncd;'
                'function tip(s){var t=document.getElementById("tip");t.textContent=s;'
                'setTimeout(function(){if(t.textContent===s)t.textContent="";},5000);}'
                'function snap(){fetch("/snapshot").then(r=>r.json()).then(function(j){'
                'tip(j.saved?(T("saved")+j.saved):(j.error||T("nofr")));});}'
                'function rec(){fetch("/api/record?ctrl="+(recOn?"stop":"start"))'
                '.then(r=>r.json()).then(function(j){'
                'if(j.err){tip(j.err);return;}'
                'recOn=!!j.recording;var b=document.getElementById("recBtn");'
                'b.className=recOn?"on":"";b.textContent=recOn?T("stop"):T("rec");'
                'if(recOn){tip(T("recing"));recT=setInterval(poll,1000);}'
                'else{clearInterval(recT);'
                'tip(j.file?(T("saved")+j.file):T("notrec"));}});}'
                'function poll(){fetch("/api/record").then(r=>r.json()).then(function(j){'
                'var b=document.getElementById("recBtn");'
                'if(!j.recording){recOn=false;b.textContent=T("rec");b.className="";'
                'clearInterval(recT);return;}'
                'b.textContent=T("stop")+" "+Math.floor(j.seconds)+"s";});}'
                'var p=document.getElementById("panel"),h=document.getElementById("bar");'
                'var g=0,x0=0,y0=0,l0=0,t0=0;'
                'p&&h&&(h.onpointerdown=function(e){g=1;'
                'l0=p.offsetLeft;t0=p.offsetTop;'
                'p.style.left=l0+"px";p.style.top=t0+"px";p.style.right="auto";'
                'x0=e.clientX;y0=e.clientY;});'
                'window.onpointermove=function(e){if(g){p.style.left=l0+e.clientX-x0+"px";'
                'p.style.top=t0+e.clientY-y0+"px";}};'
                'window.onpointerup=function(){g=0;};'
                'fetch("/api/servo").then(r=>r.json()).then(show);'
                'fetch("/api/display").then(r=>r.json()).then(function(j){'
                'cB.checked=j.show_boxes;cP.checked=j.show_person;'
                'cF.checked=j.show_fire;cZ.checked=j.show_zone;'
                'al.value=Math.round(j.box_alpha*100);});'
                'applyLang();'
                '</script></body></html>')

    @app.route('/video')
    def video():
        return Response(mjpeg(),
                        mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route('/status')
    def status():
        with sec.lock:
            return jsonify(sec.info)

    @app.route('/favicon.ico')
    def favicon():
        return ('', 204)

    @app.route('/snapshot')
    def snapshot():
        with sec.lock:
            f = sec.frame
        if f is None:
            return jsonify({"error": "no frame"}), 503
        name = "snap_manual_%s.jpg" % time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(sec.rec_dir, name)
        cv2.imwrite(path, f)
        _log("取证", "手动拍照: %s" % path)
        return jsonify({"saved": name, "path": path})

    @app.route('/events')
    def events():
        """最近事件列表 (读 events.jsonl 最后50行)"""
        p = sec.events_log
        if not os.path.exists(p):
            return jsonify([])
        with open(p, encoding="utf-8") as fh:
            lines = fh.readlines()[-50:]
        import json
        out = []
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
        return jsonify(out)

    @app.route('/recordings')
    def recordings():
        """取证录像列表: 新在前"""
        rc = getattr(sec, "rec", None)
        d = rc.out if rc is not None else os.path.join(sec.rec_dir, "rec")
        if not os.path.isdir(d):
            return jsonify([])
        import glob
        files = sorted(glob.glob(os.path.join(d, "*.mp4")),
                       key=os.path.getmtime, reverse=True)
        out = []
        for f in files[:60]:
            out.append({"name": os.path.basename(f),
                        "mb": round(os.path.getsize(f) / 1048576.0, 1),
                        "time": time.strftime("%Y-%m-%d %H:%M:%S",
                                              time.localtime(os.path.getmtime(f)))})
        return jsonify(out)

    @app.route('/recordings/<name>')
    def recording_file(name):
        from flask import send_file
        rc = getattr(sec, "rec", None)
        d = rc.out if rc is not None else os.path.join(sec.rec_dir, "rec")
        p = os.path.join(d, os.path.basename(name))
        if not os.path.exists(p):
            return jsonify(err="not found"), 404
        return send_file(p, mimetype="video/mp4")

    @app.route('/api/record')
    def api_record():
        """手动录像: ?ctrl=start|stop ; 无参=查询状态"""
        rc = getattr(sec, "rec", None)
        if rc is None:
            return jsonify(err="recorder disabled"), 503
        act = request.args.get("ctrl")
        if act == "start":
            _log("取证", "手动录像开始 -> %s" % rc.out)
            return jsonify(rc.start_manual())
        if act == "stop":
            return jsonify(rc.stop_manual())
        return jsonify(rc.manual_status())

    @app.route('/api/display')
    def api_display():
        """显示设置热更新:
          GET /api/display                      = 查询当前
          ?show_boxes=0|show_person=|show_fire=|show_zone=&alpha=0.2~1.0
        """
        d = sec.disp
        for k in ("show_boxes", "show_person", "show_fire", "show_zone"):
            v = request.args.get(k)
            if v is not None:
                d[k] = v in ("1", "true", "on", "True")
        a = request.args.get("alpha")
        if a is not None:
            try:
                d["box_alpha"] = max(0.2, min(1.0, float(a)))
            except ValueError:
                pass
        return jsonify({k: d[k] for k in ("show_boxes", "show_person",
                                          "show_fire", "show_zone",
                                          "box_alpha")})

    @app.route('/api/servo')
    def api_servo():
        """层A手动接口(遗留函数接口):
          /api/servo?angle=90&t=800   转到90度(0~270) 用800ms
          /api/servo?dir=center       回中位
          /api/servo?ping=1           在线检测(需接RX)
          /api/servo?rad=1            回读当前角度(需接RX)
          /api/servo?torque=off|on    释放/恢复扭力(手动摆支架用)
          /api/servo?angle=stop       停在当前位置
        """
        sv = getattr(sec, "servo", None)
        if sv is None:
            return jsonify(err="servo disabled (config servo.enabled=true)"), 503
        # 无参数 = 查询当前角度(给页面初始显示用, 不动硬件)
        if not any(k in request.args for k in
                   ("angle", "dir", "center", "ping", "rad", "torque")):
            return jsonify(angle=int(getattr(sec, "servo_angle", 0)))
        # 步进模式(网页按钮): dir=left|right|center [&step=15]
        d = request.args.get("dir")
        if d in ("left", "right", "center"):
            step = float(request.args.get(
                "step", sec.cfg.get("servo", {}).get("step", 15)))
            if d == "center":
                new = sv.angle_max / 2.0
                sv.center()
            else:
                cur = float(getattr(sec, "servo_angle", sv.angle_max / 2.0))
                # 摄像头装在舵机臂上, 物理视角与角度增减相反:
                # 点"左"=角度增大, 点"右"=角度减小 (仅此处映射, 勿改驱动)
                new = max(0.0, min(sv.angle_max,
                                   cur + (step if d == "left" else -step)))
                sv.move(new)
            sec.servo_angle = new
            _log("舵机", "-> %.0f° (%s)" % (new, d))
            return jsonify(angle=int(new))
        if request.args.get("ping"):
            return jsonify(ping=sv.ping())
        if request.args.get("rad"):
            return jsonify(angle=sv.read_angle(), pulse=sv.read_pulse())
        tq = request.args.get("torque")
        if tq == "off":
            sv.release_torque(); return jsonify(ok="torque released")
        if tq == "on":
            sv.restore_torque(); return jsonify(ok="torque restored")
        a = request.args.get("angle")
        if a is None:
            return jsonify(err="need angle|dir|ping|rad|torque"), 400
        if a == "stop":
            sv.stop_here(); return jsonify(ok="stopped")
        try:
            t = int(request.args.get("t", 800))
            sv.move(float(a), t)
            sec.servo_angle = float(a)
            _log("舵机", "-> %.0f° (绝对)" % float(a))
            return jsonify(angle=int(float(a)), t_ms=t)
        except ValueError:
            return jsonify(err="bad angle: %s" % a), 400

    return app
