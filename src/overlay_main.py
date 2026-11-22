# -*- coding: utf-8 -*-
# scrcpy 快捷键半透明悬浮小抄
# 修复1: overlay 顶层 HWND 用 EnumWindows 按自身 PID 取(winfo_id 是子窗口)
# 修复2: 折叠把手点不开 -> <Button-1> 与 <ButtonPress-1> 是同一事件, 拖动绑定覆盖了展开;
#        改为 按下记录 + 松手未移动=点击展开, 移动=拖动
import os, sys, tkinter as tk, ctypes
from ctypes import wintypes
try:
    import win32util
except Exception:
    win32util = None

TARGET_EXE = "scrcpy.exe"
# 状态/命令文件跟着脚本自己所在的目录, 不依赖临时目录
_HERE = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(_HERE, "overlay_status.txt")
CMD_FILE = os.path.join(_HERE, "overlay_cmd.txt")
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
EWP = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 1, 2, 16
_H = ctypes.c_void_p
user32.IsWindowVisible.argtypes = [_H]; user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [_H]; user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [_H, ctypes.c_wchar_p, ctypes.c_int]; user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [_H, ctypes.POINTER(wintypes.DWORD)]; user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [_H, ctypes.POINTER(wintypes.RECT)]; user32.GetWindowRect.restype = wintypes.BOOL
user32.IsIconic.argtypes = [_H]; user32.IsIconic.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [_H, _H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]; user32.SetWindowPos.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]; kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]; kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]; kernel32.CloseHandle.restype = wintypes.BOOL


def proc_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    b = ctypes.create_unicode_buffer(1024)
    s = wintypes.DWORD(1024)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, b, ctypes.byref(s))
    kernel32.CloseHandle(h)
    return os.path.basename(b.value) if ok else ""


def find_scrcpy():
    res = []

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if proc_name(p.value).lower() == TARGET_EXE:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            res.append({"hwnd": hwnd, "left": r.left, "top": r.top,
                        "right": r.right, "bottom": r.bottom,
                        "iconic": bool(user32.IsIconic(hwnd))})
            return False
        return True

    user32.EnumWindows(EWP(cb), 0)
    return res[0] if res else None


def self_top_hwnd():
    me = os.getpid()
    res = []

    def cb(hwnd, lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == me:
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 40 and r.bottom - r.top > 20:
                res.append(hwnd)
                return False
        return True

    user32.EnumWindows(EWP(cb), 0)
    return res[0] if res else 0


BG, FG, TBG, ACC, MUT = "#202124", "#e8eaed", "#35363a", "#8ab4f8", "#9aa0a6"
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", False)
try:
    root.attributes("-alpha", 0.88)
except tk.TclError:
    pass
root.configure(bg=BG)

normal = tk.Frame(root, bg=BG)
head = tk.Frame(normal, bg=TBG)
head.pack(fill="x")
tk.Label(head, text="⌨ scrcpy 快捷键", bg=TBG, fg=FG,
         font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=8, pady=5)
close_btn = tk.Label(head, text="✕", bg=TBG, fg=MUT, font=("Segoe UI", 11, "bold"), cursor="hand2")
close_btn.pack(side="right", padx=8)
min_btn = tk.Label(head, text="—", bg=TBG, fg=ACC, font=("Segoe UI", 11, "bold"), cursor="hand2")
min_btn.pack(side="right", padx=8)
follow_var = tk.BooleanVar(value=True)
follow_btn = tk.Label(head, text="", bg=TBG, fg=ACC, font=("Microsoft YaHei", 9), cursor="hand2")
follow_btn.pack(side="right", padx=8)
pin_btn = tk.Label(head, text="", bg=TBG, fg=ACC, font=("Microsoft YaHei", 9), cursor="hand2")
pin_btn.pack(side="right", padx=8)

rows = [("返回", "Alt+B / 右键"), ("Home", "Alt+H / 中键"), ("多任务", "Alt+S"),
        ("音量 +/−", "Alt+↑ / Alt+↓"), ("熄屏(仍投屏)", "Alt+O"), ("点亮屏幕", "Alt+Shift+O"),
        ("电源键", "Alt+P"), ("复制 手机→PC", "Alt+C"), ("粘贴 PC→手机", "Alt+V"),
        ("旋转 左/右", "Alt+← / Alt+→"), ("全屏", "F11"), ("1:1 像素", "Alt+G"),
        ("去黑边", "Alt+W"), ("通知栏", "Alt+N"), ("退出投屏", "Alt+Q")]
body = tk.Frame(normal, bg=BG)
body.pack(fill="both", expand=True, padx=10, pady=6)
for i, (a, b) in enumerate(rows):
    tk.Label(body, text=a, bg=BG, fg=FG, font=("Microsoft YaHei", 9),
             anchor="w").grid(row=i, column=0, sticky="w", pady=1)
    tk.Label(body, text=b, bg=BG, fg=ACC, font=("Consolas", 9),
             anchor="e").grid(row=i, column=1, sticky="e", padx=(18, 0), pady=1)
tip = tk.Label(normal, text="MOD=左Alt/Win · 拖动移动 · — 折叠", bg=BG, fg=MUT,
               font=("Microsoft YaHei", 8))
tip.pack(fill="x", padx=10, pady=(0, 6))

mini = tk.Frame(root, bg=TBG)
mini_lbl = tk.Label(mini, text="⌨ 快捷键 ▸", bg=TBG, fg=ACC,
                    font=("Microsoft YaHei", 9, "bold"), cursor="hand2")
mini_lbl.pack(side="left", padx=8, pady=3)
mini_close = tk.Label(mini, text="✕", bg=TBG, fg=MUT, font=("Segoe UI", 10, "bold"), cursor="hand2")
mini_close.pack(side="right", padx=6)

st = {"collapsed": False, "auto_hidden": False, "hwnd": 0, "moved": False}
dg = {"x": 0, "y": 0, "ox": 0, "oy": 0}


def rf():
    follow_btn.config(text=("● 跟随" if follow_var.get() else "○ 跟随"))
    try:
        _hw = win32util.find_window() if win32util else 0
        pin_btn.config(text=("● 置顶" if (_hw and win32util.is_topmost(_hw)) else "○ 置顶"))
    except Exception:
        pin_btn.config(text="○ 置顶")


def collapse(e=None):
    st["collapsed"] = True
    normal.pack_forget()
    mini.pack(fill="x")
    root.update_idletasks()


def expand(e=None):
    st["collapsed"] = False
    mini.pack_forget()
    

def main():
    """执行原有顶层主循环(保持原缩进, 用 exec 在当前全局命名空间运行)"""
    exec(compile(_MAIN_BODY, '<overlay>', 'exec'), globals())


_MAIN_BODY = 'normal.pack(fill="both", expand=True)\nif st["auto_hidden"]:\n    root.deiconify()\n    st["auto_hidden"] = False\nroot.update_idletasks()\n\n\ndef on_press(e):\n    dg["x"], dg["y"] = e.x_root, e.y_root\n    dg["ox"], dg["oy"] = root.winfo_x(), root.winfo_y()\n    st["moved"] = False\n\n\ndef on_move(e):\n    if abs(e.x_root - dg["x"]) > 3 or abs(e.y_root - dg["y"]) > 3:\n        st["moved"] = True\n    if not st["moved"]:\n        return\n    follow_var.set(False)\n    rf()\n    root.geometry("+%d+%d" % (dg["ox"] + e.x_root - dg["x"], dg["oy"] + e.y_root - dg["y"]))\n\n\ndef on_release(e):\n    if st["collapsed"] and not st["moved"]:\n        expand()\n\n\ndef on_follow(e):\n    follow_var.set(not follow_var.get())\n    rf()\n\n\ndef set_pin(on):\n    hwnd = win32util.find_window() if win32util else 0\n    if not hwnd:\n        return\n    want = (not win32util.is_topmost(hwnd)) if on is None else on\n    win32util.set_topmost(hwnd, want)\n    rf()\n\n\ndef on_pin(e):\n    set_pin(None)\n\n\ndef on_close(e):\n    root.destroy()\n\n\nclose_btn.bind("<Button-1>", on_close)\nmini_close.bind("<Button-1>", on_close)\nmin_btn.bind("<Button-1>", collapse)\nfollow_btn.bind("<Button-1>", on_follow)\npin_btn.bind("<Button-1>", on_pin)\nfor w in (head, body, tip):\n    w.bind("<ButtonPress-1>", on_press)\n    w.bind("<B1-Motion>", on_move)\nfor w in (mini, mini_lbl):\n    w.bind("<ButtonPress-1>", on_press)\n    w.bind("<B1-Motion>", on_move)\n    w.bind("<ButtonRelease-1>", on_release)\nroot.bind("<Escape>", lambda e: root.destroy())\n\nsw = root.winfo_screenwidth()\nrf()\n\n\ndef set_z(scrcpy_hwnd):\n    if not st["hwnd"]:\n        return\n    try:\n        gfw = user32.GetForegroundWindow\n        gfw.argtypes = []\n        gfw.restype = _H\n        # scrcpy 是前台窗口(用户点了它)时, 用 HWND_TOP 把贴图提到顶浮起;\n        # 跨进程 insertAfter=scrcpy 无效, 且前台窗口恒顶, 故只能这样跟随\n        st["fg"] = (gfw() == scrcpy_hwnd)\n        if st["fg"]:\n            user32.SetWindowPos(st["hwnd"], 0, 0, 0, 0, 0,\n                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)\n            st["raised"] = True\n        else:\n            st["raised"] = False\n    except Exception:\n        pass\n\n\ndef place(info):\n    root.update_idletasks()\n    W = root.winfo_width()\n    x = info["right"] + 12\n    side = "right"\n    if x + W > sw:\n        x = info["left"] - W - 12\n        side = "left"\n    root.geometry("+%d+%d" % (x, info["top"]))\n    return side\n\n\ndef _handle_cmd():\n    try:\n        if os.path.exists(CMD_FILE):\n            with open(CMD_FILE, "r", encoding="utf-8") as f:\n                cmd = f.read().strip()\n            os.remove(CMD_FILE)\n            if cmd == "collapse":\n                collapse()\n            elif cmd == "expand":\n                expand()\n            elif cmd == "follow_on":\n                follow_var.set(True); rf()\n            elif cmd == "follow_off":\n                follow_var.set(False); rf()\n            elif cmd == "topmost_on":\n                set_pin(True)\n            elif cmd == "topmost_off":\n                set_pin(False)\n            elif cmd == "topmost_toggle":\n                set_pin(None)\n    except Exception:\n        pass\n\n\ndef tick():\n    if not st["hwnd"]:\n        st["hwnd"] = self_top_hwnd()\n    _handle_cmd()\n    rf()\n    info = find_scrcpy()\n    ok = bool(info) and not info["iconic"]\n    status = ""\n    if not st["collapsed"] and not ok:\n        if not st["auto_hidden"]:\n            root.withdraw()\n            st["auto_hidden"] = True\n        status = "auto-hidden"\n    else:\n        if st["auto_hidden"]:\n            root.deiconify()\n            st["auto_hidden"] = False\n        if follow_var.get() and ok:\n            status = place(info)\n            set_z(info["hwnd"])\n        else:\n            status = "collapsed" if st["collapsed"] else "follow-off"\n    try:\n        with open(STATUS_FILE, "w", encoding="utf-8") as f:\n            f.write("collapsed=%s follow=%s ovh=%s pos=%d,%d status=%s fg_scrcpy=%s raised=%s\\n" % (\n                st["collapsed"], follow_var.get(), hex(st["hwnd"]),\n                root.winfo_x(), root.winfo_y(), status,\n                st.get("fg"), st.get("raised")))\n    except Exception:\n        pass\n    root.after(300, tick)\n\n\nnormal.pack(fill="both", expand=True)\nroot.update_idletasks()\n_i = find_scrcpy()\nif _i and not _i["iconic"]:\n    place(_i)\nroot.after(100, tick)\nroot.mainloop()\n'


if __name__ == "__main__":
    main()
