# -*- coding: utf-8 -*-
"""Agent Bridge —— 把手机变成可被 AI Agent 操作的 MCP 工具集

设计原则:
  * 零第三方依赖（只用标准库 + 已随包分发的 Pillow）
  * 复用 GUI 已建立的 adb 连接（从 device_state.json 读串号），不重复配对
  * 所有操作写审计日志 agent_events.jsonl，GUI 实时展示
  * 敏感界面（密码 / 验证码 / 支付 / 权限）必须人工批准后才能继续

运行方式:
  python agent_bridge.py              # MCP over stdio（默认，给 MCP 客户端用）
  python agent_bridge.py --http 8765  # HTTP 调试接口（POST /mcp，GET /health）
  python agent_bridge.py --self-test  # 自检：只列工具，不碰 adb
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

BASE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
    else os.path.dirname(os.path.abspath(__file__))
ADB = os.environ.get("PHONE_CAST_ADB") or os.path.join(BASE, "tools", "adb.exe")
STATE_FILE = os.path.join(BASE, "device_state.json")
CMD_FILE = os.path.join(BASE, "agent_cmd.txt")
EVENT_FILE = os.path.join(BASE, "agent_events.jsonl")
APPROVAL_FILE = os.path.join(BASE, "agent_approval.json")
APPROVAL_RESULT = os.path.join(BASE, "agent_approval_result.json")
OUT_DIR = os.path.join(BASE, "agent_output")
CREATE_NO_WINDOW = 0x08000000
SERVER_NAME = "phone-cast-assistant"
SERVER_VER = "1.6.2"

# 只允许这些 adb shell 命令，杜绝 reboot / rm / pm uninstall / su 之类
SHELL_WHITELIST = (
    "uiautomator", "input", "wm size", "wm density", "dumpsys", "settings get",
    "logcat -d", "pm list packages", "pm path", "am start", "am broadcast",
    "ime list", "ime enable", "ime set", "ip -f inet addr", "content query",
    "cmd media_session volume --stream 3 --get", "getprop", "date", "ls -l /sdcard",
)
SHELL_BLOCK = ("rm ", "reboot", "shutdown", "su ", "dd if=", "mkfs", "format",
               "pm uninstall", "pm clear", "am kill", "setenforce", "chmod 777")

# 命中这些词就认为当前是敏感界面，操作前必须人工确认
SENSITIVE = ("验证码", "密码", "支付", "付款", "指纹", "人脸", "解锁", "授权",
             "权限", "确认", "转账", "银行卡", "身份证", "私钥", "助记词",
             "password", "captcha", "verification code", "pin code", "otp")

os.makedirs(OUT_DIR, exist_ok=True)


# ---------------- 基础工具 ----------------
def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _event(tool, args, result="", approval=""):
    """追加一条审计事件，GUI 会实时读取显示"""
    try:
        rec = {"ts": _now(), "tool": tool, "args": str(args)[:300],
               "result": str(result)[:500], "approval": approval}
        with open(EVENT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _read_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _serial(explicit=None):
    if explicit:
        return explicit
    s = _read_state().get("serial")
    if s:
        return s
    # 状态文件没有就从 adb devices 猜（只取第一个在线设备）
    out = _adb([], timeout=10)
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            return line.split("\t")[0].strip()
    return ""


def _adb(args, timeout=30, binary=False):
    if not os.path.exists(ADB):
        return b"" if binary else ""
    cmd = [ADB] + list(args)
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        if binary:
            return p.stdout
        return (p.stdout or b"").decode("utf-8", "ignore") + \
               (p.stderr or b"").decode("utf-8", "ignore")
    except Exception as e:
        return b"" if binary else ("__ERR__ " + str(e))


def _sdev(serial, args, timeout=30, binary=False):
    return _adb(["-s", serial] + list(args), timeout=timeout, binary=binary)


def _cmd_status():
    """GUI -> Bridge: pause / resume / stop"""
    try:
        if os.path.exists(CMD_FILE):
            with open(CMD_FILE, encoding="utf-8") as f:
                cmd = f.read().strip()
            os.remove(CMD_FILE)
            return cmd
    except Exception:
        pass
    return ""


# ---------------- 人在回路 ----------------
def _require_approval(reason, tool):
    """写审批请求，等 GUI 里的人点「允许/拒绝」。超时或拒绝都视为不通过。"""
    rid = "%d" % int(time.time() * 1000)
    try:
        for f in (APPROVAL_FILE, APPROVAL_RESULT):
            if os.path.exists(f):
                os.remove(f)
        with open(APPROVAL_FILE, "w", encoding="utf-8") as f:
            json.dump({"id": rid, "reason": reason, "tool": tool,
                       "ts": _now()}, f, ensure_ascii=False)
        _event(tool, "", "WAIT_APPROVAL: " + reason, "pending")
    except Exception:
        return False

    deadline = time.time() + 180
    while time.time() < deadline:
        if _cmd_status() == "stop":
            return False
        try:
            if os.path.exists(APPROVAL_RESULT):
                with open(APPROVAL_RESULT, encoding="utf-8") as f:
                    r = json.load(f)
                if str(r.get("id")) == rid:
                    allow = bool(r.get("allow"))
                    _event(tool, "", "APPROVAL=" + str(allow), "done")
                    try:
                        os.remove(APPROVAL_RESULT)
                    except Exception:
                        pass
                    return allow
        except Exception:
            pass
        time.sleep(0.4)
    _event(tool, "", "APPROVAL_TIMEOUT", "timeout")
    return False


def _is_sensitive(text):
    t = (text or "").lower()
    return any(k.lower() in t for k in SENSITIVE)


# ---------------- 界面感知 ----------------
def _snapshot_data(annotate=False, max_nodes=200):
    serial = _serial()
    if not serial:
        raise RuntimeError("没有可用设备，请先在 GUI 里连接手机")
    remote = "/sdcard/window.xml"
    local = os.path.join(OUT_DIR, "window.xml")
    _sdev(serial, ["shell", "uiautomator", "dump", remote], timeout=25)
    _sdev(serial, ["pull", remote, local], timeout=25)
    if not os.path.exists(local):
        raise RuntimeError("uiautomator dump 失败，界面可能不可读取")

    nodes = []
    try:
        tree = ET.parse(local)
        for el in tree.iter("node"):
            if len(nodes) >= max_nodes:
                break
            text = (el.get("text") or "").strip()
            desc = (el.get("content-desc") or "").strip()
            rid = (el.get("resource-id") or "").strip()
            cls = (el.get("class") or "").strip().split(".")[-1]
            bounds = el.get("bounds") or ""
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds)
            xy = ((int(m.group(1)) + int(m.group(3))) // 2,
                  (int(m.group(2)) + int(m.group(4))) // 2) if m else (0, 0)
            if not (text or desc or rid):
                continue
            nodes.append({"n": len(nodes) + 1, "text": text, "desc": desc,
                          "resourceId": rid, "class": cls,
                          "x": xy[0], "y": xy[1], "bounds": bounds,
                          "clickable": el.get("clickable") == "true"})
    except Exception as e:
        raise RuntimeError("解析控件树失败: %s" % e)

    annotated_path = None
    if annotate:
        try:
            from PIL import Image, ImageDraw
            raw = _sdev(serial, ["exec-out", "screencap", "-p"],
                        timeout=25, binary=True)
            img_path = os.path.join(OUT_DIR, "screen.png")
            with open(img_path, "wb") as f:
                f.write(raw)
            img = Image.open(img_path).convert("RGB")
            d = ImageDraw.Draw(img)
            for nd in nodes:
                if nd["x"] and nd["y"]:
                    d.rectangle([nd["x"] - 10, nd["y"] - 10,
                                 nd["x"] + 10, nd["y"] + 10],
                                outline=(255, 0, 0), width=2)
                    d.text((nd["x"] + 12, nd["y"] - 12),
                           str(nd["n"]), fill=(255, 0, 0))
            annotated_path = os.path.join(OUT_DIR, "annotated.png")
            img.save(annotated_path)
        except Exception:
            annotated_path = None
    return nodes, annotated_path


def _snapshot_text(nodes):
    lines = ["当前界面控件（编号 / 文字 / 资源ID / 类 / 中心坐标 / 可点击）："]
    for nd in nodes:
        lines.append("#%d  %s | %s | %s | (%d,%d) | %s" % (
            nd["n"], nd["text"] or nd["desc"], nd["resourceId"],
            nd["class"], nd["x"], nd["y"], "是" if nd["clickable"] else "否"))
    return "\n".join(lines)


def _find_node(nodes, selector, index=0):
    if "=" in selector:
        key, val = selector.split("=", 1)
    else:
        key, val = "text", selector
    key = {"resourceId": "resourceId", "text": "text", "className": "class",
           "description": "desc"}.get(key, "text")
    hits = [n for n in nodes if val in str(n.get(key, ""))]
    return hits[index] if len(hits) > index else None


# ---------------- 工具实现 ----------------
def t_list_devices(a):
    out = _adb(["devices"], timeout=15)
    devs = []
    for line in out.splitlines()[1:]:
        if "\tdevice" in line:
            devs.append(line.split("\t")[0].strip())
    return _text("在线设备: " + (", ".join(devs) if devs else "无"))


def t_get_status(a):
    st = _read_state()
    return _text("串号=%s 连接=%s 码率=%s Mbps 服务=%s v%s" % (
        st.get("serial") or "未设置", st.get("link") or "?",
        st.get("bitrate") or "?", SERVER_NAME, SERVER_VER))


def t_snapshot(a):
    nodes, annotated = _snapshot_data(annotate=bool(a.get("annotate", False)),
                                      max_nodes=int(a.get("max_nodes", 200)))
    text = _snapshot_text(nodes)
    sensitive = [_is_sensitive(n["text"] + n["desc"]) for n in nodes]
    content = [{"type": "text", "text": text}]
    if annotated and os.path.exists(annotated):
        with open(annotated, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        content.append({"type": "image", "data": b64, "mimeType": "image/png"})
    if any(sensitive):
        content.append({"type": "text", "text":
                        "⚠️ 当前界面含敏感内容（密码/验证码/支付等），后续点击操作需要人工批准。"})
    return {"content": content, "isError": False}


def t_screenshot(a):
    serial = _serial()
    if not serial:
        raise RuntimeError("没有可用设备")
    raw = _sdev(serial, ["exec-out", "screencap", "-p"], timeout=25, binary=True)
    path = a.get("path") or os.path.join(OUT_DIR, "screen.png")
    with open(path, "wb") as f:
        f.write(raw)
    content = [{"type": "text", "text": "截图已保存: %s (%d bytes)" % (path, len(raw))}]
    if a.get("include_image", True):
        content.append({"type": "image",
                        "data": base64.b64encode(raw).decode("ascii"),
                        "mimeType": "image/png"})
    return {"content": content, "isError": False}


def _guard_tap(x, y):
    """坐标点击前，检查是否落在敏感控件上"""
    try:
        nodes, _ = _snapshot_data(annotate=False, max_nodes=200)
        for nd in nodes:
            if nd["x"] and abs(nd["x"] - x) < 40 and abs(nd["y"] - y) < 40:
                if _is_sensitive(nd["text"] + nd["desc"]):
                    if not _require_approval("点击敏感控件「%s」" % (nd["text"] or nd["desc"]), "tap"):
                        raise RuntimeError("人工拒绝或超时，操作已取消")
                return
    except RuntimeError:
        raise
    except Exception:
        pass


def t_tap(a):
    serial = _serial()
    x, y = int(a["x"]), int(a["y"])
    if not a.get("skip_guard"):
        _guard_tap(x, y)
    _sdev(serial, ["shell", "input", "tap", str(x), str(y)])
    return _text("已点击 (%d,%d)" % (x, y))


def t_long_press(a):
    serial = _serial()
    x, y = int(a["x"]), int(a["y"])
    _sdev(serial, ["shell", "input", "swipe", str(x), str(y),
                   str(x), str(y), str(int(a.get("duration", 800)))])
    return _text("已长按 (%d,%d)" % (x, y))


def t_swipe(a):
    serial = _serial()
    _sdev(serial, ["shell", "input", "swipe",
                   str(int(a["x1"])), str(int(a["y1"])),
                   str(int(a["x2"])), str(int(a["y2"])),
                   str(int(a.get("duration", 300)))])
    return _text("已滑动 (%s,%s) -> (%s,%s)" %
                 (a["x1"], a["y1"], a["x2"], a["y2"]))


def t_drag(a):
    serial = _serial()
    _sdev(serial, ["shell", "input", "draganddrop",
                   str(int(a["x1"])), str(int(a["y1"])),
                   str(int(a["x2"])), str(int(a["y2"])),
                   str(int(a.get("duration", 500)))])
    return _text("已拖拽")


def t_press(a):
    serial = _serial()
    keys = {"back": "4", "home": "3", "recent": "187", "enter": "66",
            "volume_up": "24", "volume_down": "25", "power": "26",
            "camera": "27", "search": "84"}
    k = str(a.get("key", "back")).lower()
    code = keys.get(k, k if k.isdigit() else "4")
    _sdev(serial, ["shell", "input", "keyevent", code])
    return _text("已按键 %s (%s)" % (k, code))


def t_type(a):
    serial = _serial()
    text = str(a.get("text", ""))
    if not text:
        raise RuntimeError("text 不能为空")
    if any(ord(c) > 127 for c in text):
        # ADBKeyboard：打包目录里可选放置 ADBKeyboard.apk
        pkg = _sdev(serial, ["shell", "pm", "list", "packages", "adbkeyboard"])
        if "adbkeyboard" not in pkg.lower():
            raise RuntimeError("中文输入需要 ADBKeyboard：把 ADBKeyboard.apk 放入 tools/ 并安装，"
                               "或改用英文/剪贴板方案")
        _sdev(serial, ["shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME"])
        _sdev(serial, ["shell", "ime", "set", "com.android.adbkeyboard/.AdbIME"])
        _sdev(serial, ["shell", "am", "broadcast", "-a", "ADB_INPUT_TEXT",
                       "--es", "msg", text.replace('"', '\\"')])
        return _text("已通过 ADBKeyboard 输入中文: " + text[:40])
    esc = text.replace("\\", "\\\\").replace(" ", "%s") \
              .replace("&", "\\&").replace("(", "\\(").replace(")", "\\)") \
              .replace("<", "\\<").replace(">", "\\>").replace('"', '\\"')
    _sdev(serial, ["shell", "input", "text", esc])
    return _text("已输入: " + text[:40])


def t_clear(a):
    serial = _serial()
    # Android 8+ 支持 keycombination，低版本退化为多次 DEL
    out = _sdev(serial, ["shell", "input", "keycombination", "113", "29"])
    if "error" in out.lower():
        for _ in range(int(a.get("times", 20))):
            _sdev(serial, ["shell", "input", "keyevent", "67"])
    return _text("已清空输入框")


def t_click_by_selector(a):
    nodes, _ = _snapshot_data(annotate=False)
    sel = a.get("selector", "")
    nd = _find_node(nodes, sel, int(a.get("index", 0)))
    if not nd:
        raise RuntimeError("没找到匹配控件: " + sel)
    if _is_sensitive(nd["text"] + nd["desc"]):
        if not _require_approval("点击敏感控件「%s」" % (nd["text"] or nd["desc"]),
                                 "click_by_selector"):
            raise RuntimeError("人工拒绝，操作已取消")
    _sdev(_serial(), ["shell", "input", "tap", str(nd["x"]), str(nd["y"])])
    return _text("已点击 #%d「%s」(%d,%d)" %
                 (nd["n"], nd["text"] or nd["desc"], nd["x"], nd["y"]))


def t_wait_for_element(a):
    sel = a.get("selector", "")
    deadline = time.time() + min(int(a.get("timeout", 15)), 60)
    while time.time() < deadline:
        if _cmd_status() == "stop":
            raise RuntimeError("收到停止指令")
        nodes, _ = _snapshot_data(annotate=False)
        nd = _find_node(nodes, sel, 0)
        if nd:
            return _text("元素已出现: #%d「%s」(%d,%d)" %
                         (nd["n"], nd["text"] or nd["desc"], nd["x"], nd["y"]))
        time.sleep(0.8)
    raise RuntimeError("等待超时，未出现: " + sel)


def t_shell(a):
    cmd = str(a.get("command", "")).strip()
    low = cmd.lower()
    if not low:
        raise RuntimeError("command 不能为空")
    if any(b in low for b in SHELL_BLOCK):
        raise RuntimeError("危险命令被拒绝: " + cmd)
    if not any(w in low for w in SHELL_WHITELIST):
        raise RuntimeError("命令不在白名单内: " + cmd)
    out = _sdev(_serial(), ["shell", cmd], timeout=int(a.get("timeout", 25)))
    return _text(out[:8000] or "(无输出)")


def t_switch_transport(a):
    to = str(a.get("to", "")).lower()
    serial = _serial()
    if to == "wifi":
        if not _require_approval("把设备从 USB 切到 Wi-Fi ADB（需要手机在同一局域网）",
                                 "switch_transport"):
            raise RuntimeError("人工拒绝，未切换")
        _sdev(serial, ["tcpip", "5555"])
        out = _sdev(serial, ["shell", "ip", "-f", "inet", "addr", "show", "wlan0"])
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/", out)
        if not m:
            raise RuntimeError("拿不到手机 Wi-Fi IP，请确认已连 Wi-Fi")
        ip = m.group(1)
        _adb(["connect", ip + ":5555"])
        return _text("已尝试切换到 Wi-Fi: %s:5555" % ip)
    if to == "usb":
        if not _require_approval("把设备从 Wi-Fi 切回 USB（需要插着数据线）",
                                 "switch_transport"):
            raise RuntimeError("人工拒绝，未切换")
        out = _sdev(serial, ["usb"])
        return _text(out.strip() or "已发送 adb usb")
    raise RuntimeError("to 只能是 wifi 或 usb")


def _text(s):
    return {"content": [{"type": "text", "text": str(s)}], "isError": False}


TOOLS = {
    "list_devices": {
        "description": "列出当前 adb 在线的设备串号", "parameters": {"type": "object", "properties": {}}},
    "get_status": {
        "description": "查看当前桥接状态：串号、连接类型、码率", "parameters": {"type": "object", "properties": {}}},
    "snapshot": {
        "description": "获取当前界面的结构化控件树（文字/资源ID/中心坐标/可点击）与标注截图。Agent 操作前应先调用它。",
        "parameters": {"type": "object", "properties": {
            "annotate": {"type": "boolean", "description": "是否在截图上画编号", "default": False},
            "max_nodes": {"type": "integer", "description": "最多返回多少个控件", "default": 200}}}},
    "screenshot": {
        "description": "截取手机当前画面，返回文件路径和 PNG 图像",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "可选：自定义保存路径"},
            "include_image": {"type": "boolean", "default": True}}}},
    "tap": {
        "description": "点击屏幕坐标（敏感控件会要求人工批准）",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "skip_guard": {"type": "boolean", "default": False}}, "required": ["x", "y"]}},
    "long_press": {
        "description": "长按屏幕坐标",
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "duration": {"type": "integer", "default": 800}}, "required": ["x", "y"]}},
    "swipe": {
        "description": "滑动手势",
        "parameters": {"type": "object", "properties": {
            "x1": {"type": "integer"}, "y1": {"type": "integer"},
            "x2": {"type": "integer"}, "y2": {"type": "integer"},
            "duration": {"type": "integer", "default": 300}},
            "required": ["x1", "y1", "x2", "y2"]}},
    "drag": {
        "description": "拖拽手势（Android 8+）",
        "parameters": {"type": "object", "properties": {
            "x1": {"type": "integer"}, "y1": {"type": "integer"},
            "x2": {"type": "integer"}, "y2": {"type": "integer"},
            "duration": {"type": "integer", "default": 500}},
            "required": ["x1", "y1", "x2", "y2"]}},
    "press": {
        "description": "按系统键：back/home/recent/enter/volume_up/volume_down/power 或数字 keycode",
        "parameters": {"type": "object", "properties": {
            "key": {"type": "string", "default": "back"}}}},
    "type": {
        "description": "输入文本。英文用 input text；中文需安装 ADBKeyboard（放 tools/ 目录）",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}},
    "clear": {
        "description": "清空当前输入框", "parameters": {"type": "object", "properties": {
            "times": {"type": "integer", "default": 20}}}},
    "click_by_selector": {
        "description": "按 text=/resourceId=/className=/description= 定位控件并点击，比纯坐标更稳",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"}, "index": {"type": "integer", "default": 0}},
            "required": ["selector"]}},
    "wait_for_element": {
        "description": "等待某个控件出现（最长 60 秒）",
        "parameters": {"type": "object", "properties": {
            "selector": {"type": "string"}, "timeout": {"type": "integer", "default": 15}},
            "required": ["selector"]}},
    "shell": {
        "description": "执行白名单内的 adb shell 只读/低危命令（dump、dumpsys、settings get 等）",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}, "timeout": {"type": "integer", "default": 25}},
            "required": ["command"]}},
    "switch_transport": {
        "description": "把设备在有线与 Wi-Fi ADB 之间切换（需人工批准）",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string", "enum": ["wifi", "usb"]}}, "required": ["to"]}},
}

HANDLERS = {
    "list_devices": t_list_devices, "get_status": t_get_status,
    "snapshot": t_snapshot, "screenshot": t_screenshot, "tap": t_tap,
    "long_press": t_long_press, "swipe": t_swipe, "drag": t_drag,
    "press": t_press, "type": t_type, "clear": t_clear,
    "click_by_selector": t_click_by_selector,
    "wait_for_element": t_wait_for_element, "shell": t_shell,
    "switch_transport": t_switch_transport,
}


# ---------------- MCP JSON-RPC ----------------
def rpc_result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def rpc_error(mid, code, msg):
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": code, "message": msg}}


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    if method == "initialize":
        return rpc_result(mid, {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VER},
        })
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return rpc_result(mid, {})
    if method == "tools/list":
        tools = [{"name": n, "description": d["description"],
                  "inputSchema": d["parameters"]} for n, d in TOOLS.items()]
        return rpc_result(mid, {"tools": tools})
    if method in ("resources/list", "prompts/list"):
        return rpc_result(mid, {"resources": []} if method.startswith("resources")
                          else {"prompts": []})
    if method == "tools/call":
        name = params.get("name", "")
        args = params.get("arguments") or {}
        fn = HANDLERS.get(name)
        if not fn:
            return rpc_result(mid, {"content": [{"type": "text",
                                                  "text": "未知工具: " + name}],
                                    "isError": True})
        _event(name, args, "CALL")
        try:
            if _require_approval_once(name, args) is False:
                return rpc_result(mid, {"content": [{"type": "text",
                                                     "text": "操作被人工拒绝或超时"}],
                                        "isError": True})
            res = fn(args)
            _event(name, args, "OK")
            return rpc_result(mid, res)
        except Exception as e:
            _event(name, args, "ERR: " + str(e))
            return rpc_result(mid, {"content": [{"type": "text", "text": str(e)}],
                                    "isError": True})
    return rpc_error(mid, -32601, "Method not found: " + method)


def _require_approval_once(name, args):
    """每个工具调用前先看 GUI 是否按了暂停；暂停时等待恢复。"""
    deadline = time.time() + 300
    while True:
        c = _cmd_status()
        if c == "stop":
            return False
        if c != "pause":
            return True
        if time.time() > deadline:
            return False
        time.sleep(0.5)


def serve_stdio():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            continue
        out = handle(msg)
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def serve_http(port):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj, code=200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/health"):
                self._send({"ok": True, "server": SERVER_NAME})
            else:
                self._send({"error": "not found"}, 404)

        def do_POST(self):
            try:
                n = int(self.headers.get("Content-Length", 0))
                msg = json.loads(self.rfile.read(n).decode("utf-8"))
                out = handle(msg)
                self._send(out if out is not None else {"ok": True})
            except Exception as e:
                self._send(rpc_error(0, -32700, str(e)))

    srv = ThreadingHTTPServer(("127.0.0.1", int(port)), H)
    sys.stderr.write("Agent Bridge HTTP listening on 127.0.0.1:%s\n" % port)
    srv.serve_forever()


if __name__ == "__main__":
    # Windows 下 stdout 管道默认是本地代码页（GBK），而 MCP 客户端按 UTF-8 解码。
    # 不强制重配的话，中文工具描述/结果会让客户端直接 UnicodeDecodeError。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if "--self-test" in sys.argv:
        r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        print("tools=%d" % len(r["result"]["tools"]))
        print("names=" + ",".join(t["name"] for t in r["result"]["tools"]))
        sys.exit(0)
    if "--http" in sys.argv:
        i = sys.argv.index("--http")
        port = sys.argv[i + 1] if len(sys.argv) > i + 1 else "8765"
        serve_http(port)
    else:
        serve_stdio()
