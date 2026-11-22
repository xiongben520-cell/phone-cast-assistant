# -*- coding: utf-8 -*-
"""
手机投屏连接助手 —— 小米 / Android 无线投屏一键连接
=====================================================
功能:
  * 自动发现局域网内的无线调试设备
  * 首次配对引导 (6 位配对码)
  * 一键启动 scrcpy 投屏 (息屏续显 + 音频转发到电脑)
  * 音频自检 (响一声测试音)
  * 音量设置
  * 快捷键贴图 (可选)

打包: PyInstaller --noconsole --onefile
依赖: exe 同目录下的 tools\\adb.exe 与 tools\\scrcpy\\
"""
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

APP_NAME = "手机投屏连接助手"
APP_VER = "1.5"


# ============ 路径解析 (同时支持源码运行与 exe 运行) ============
def app_dir():
    """exe 所在目录 (打包后) 或脚本目录 (源码运行)"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


BASE = app_dir()
TOOLS = os.path.join(BASE, "tools")
ADB = os.path.join(TOOLS, "adb.exe")
SCRCPY_DIR = os.path.join(TOOLS, "scrcpy")
SCRCPY = os.path.join(SCRCPY_DIR, "scrcpy.exe")
CRASH_LOG = os.path.join(BASE, "crash.log")
OVERLAY_CMD = os.path.join(BASE, "overlay_cmd.txt")
OVERLAY_STATUS = os.path.join(BASE, "overlay_status.txt")

try:
    import win32util
except Exception:
    win32util = None   # 非 Windows 环境下按钮会给出提示，不影响打包


def _log_crash(exc_type, exc_value, exc_tb):
    """把未捕获异常写进 exe 同目录的 crash.log, 方便现场排查"""
    import traceback
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write("\n===== crash =====\n")
            f.write(f"frozen={getattr(sys, 'frozen', False)}  exe={sys.executable}\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except Exception:
        pass


sys.excepthook = _log_crash

TARGET_VOLUME = 90   # 150 量程的 60%
VOL_STEP = 10
CREATE_NO_WINDOW = 0x08000000


# ============ 工具函数 ============
def run(cmd, timeout=40):
    try:
        kw = {}
        if os.name == "nt":
            kw["creationflags"] = CREATE_NO_WINDOW
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="ignore",
                           timeout=timeout, **kw)
        return (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        return "__ERR__ 超时"
    except Exception as e:
        return f"__ERR__ {e}"


def adb_devices():
    """返回 [ (serial, state) ]"""
    out = run([ADB, "devices"])
    res = []
    for line in out.splitlines()[1:]:
        if "\t" not in line:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            res.append((parts[0].strip(), parts[1].strip()))
    return res


def adb(serial, *args, timeout=40):
    return run([ADB, "-s", serial] + list(args), timeout=timeout)


# ---------- 带宽实测 ----------
# scrcpy 的视频流是 手机 -> PC, 且走 adb forward 的 socket (sync 级效率),
# 所以测 download 方向、用 adb pull 才准。
# 实测对比: adb pull 322 Mbps / exec-out cat 117 Mbps / dd 110 Mbps
# (后两者受 shell 封装和手机端 CPU 生成速度限制, 严重低估)
#
# pull 到 Windows 的 "nul" 设备, 不落盘、不污染手机。
BANDWIDTH_USB_MB = 40      # USB 测 40MB, 约 1 秒
BANDWIDTH_WIFI_MB = 4      # 无线测 4MB, 约 4 秒
BW_TEST_PATH = "/data/local/tmp/.scrcpy_bwtest.bin"


def measure_bandwidth(serial, is_usb, timeout=90):
    """
    实测 adb 通道下行带宽 (手机 -> PC)。
    返回 Mbps (float), 失败返回 None。
    """
    size_mb = BANDWIDTH_USB_MB if is_usb else BANDWIDTH_WIFI_MB

    # 在手机上造测试数据 (dd 到 tmpfs, 40MB 约 0.1 秒)
    run([ADB, "-s", serial, "shell",
         f"dd if=/dev/zero of={BW_TEST_PATH} bs=1048576 count={size_mb} 2>/dev/null"],
        timeout=40)

    try:
        t0 = time.time()
        r = subprocess.run([ADB, "-s", serial, "pull", BW_TEST_PATH, "nul"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="ignore",
                           timeout=timeout, creationflags=CREATE_NO_WINDOW)
        elapsed = time.time() - t0
        out = (r.stdout or "") + (r.stderr or "")

        # adb 自己报告纯传输时间: "(41943040 bytes in 1.005s)"
        # 用它算可以排除 adb 进程启动开销, 更准
        m = re.search(r"\((\d+)\s*bytes in ([\d.]+)s\)", out)
        if m:
            nbytes = int(m.group(1))
            sec = float(m.group(2))
            if sec > 0.01 and nbytes > 1048576:
                return nbytes * 8 / sec / 1e6

        # 回退: 用总耗时 (会略偏低)
        if elapsed > 0.1:
            return size_mb * 1048576 * 8 / elapsed / 1e6
        return None
    except Exception:
        return None
    finally:
        # 清掉手机上的测试文件, 不占用户存储
        try:
            run([ADB, "-s", serial, "shell", f"rm -f {BW_TEST_PATH}"], timeout=15)
        except Exception:
            pass


def pick_video_bitrate(serial, is_usb):
    """
    按实测带宽的 80% 决定视频码率 (Mbps)。
    每次连接都重新测, 因为 USB 口速率、Wi-Fi 信号、路由器状态都可能变。
    返回 (码率 Mbps, 说明文字)
    """
    bw = measure_bandwidth(serial, is_usb)
    if not bw:
        fallback = 100 if is_usb else 8
        return fallback, "带宽实测失败, 用保守默认值 %d Mbps" % fallback

    target = bw * 0.8
    # 上下限: 太低没意义; 上限防止 USB3 之类超高带宽时算出离谱的码率
    # (1440p60 在 200Mbps 以上画质已接近无损, 再高只是浪费编码算力)
    lo, hi = (20, 300) if is_usb else (2, 30)
    target = max(lo, min(hi, target))
    target = int(round(target))
    return target, "实测 %.0f Mbps, 取其 80%% = %d Mbps" % (bw, target)


# ---------- USB 有线连接相关的探测 ----------
# adb devices 的输出里, USB 设备串号和无线设备长得不一样:
#   USB  : "a1b2c3d4"                     (纯序列号)
#   无线 : "192.168.1.10:5555"          (IP:端口)
#          "adb-a1b2c3d4-XXXXXX._adb-tls-connect._tcp"  (mDNS 名)
XIAOMI_VIDS = ("2717", "18D1")   # 2717=小米  18D1=Google(Pixel/原生)


def is_usb_serial(serial):
    """判断串号是不是 USB 直连设备"""
    if not serial:
        return False
    if ":" in serial:                 # IP:端口
        return False
    if "_adb-tls-" in serial:         # mDNS 服务名
        return False
    return True


def usb_device_present():
    """
    是否有小米/安卓手机接在 USB 上 (不管 adb 有没有授权)。
    用 Windows PnP 查 VID, 不依赖 adb。
    返回 (True/False, 描述文字)
    """
    ps = (
        "$d = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.InstanceId -match 'VID_2717|VID_18D1' }; "
        "if ($d) { $d | ForEach-Object { \"$($_.Status)|$($_.Class)|$($_.FriendlyName)\" } }"
    )
    out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-Command", ps], timeout=25)
    lines = [ln.strip() for ln in out.splitlines() if "|" in ln]
    if not lines:
        return False, ""
    # 找到 ADB 接口的那条, 判断是否有驱动问题
    has_adb_iface = any("ADB" in ln for ln in lines)
    has_problem = any(ln.lower().startswith("error") or "problem" in ln.lower()
                      for ln in lines)
    return True, ("ADB接口已识别" if has_adb_iface else "设备已插入但ADB接口未识别") + \
                 ("，驱动异常" if has_problem else "")


def usb_serial_from_sysfs():
    """
    用 Windows 直接读小米设备的 USB 序列号, 用于给用户提示。
    (仅作辅助, 真正连不连得上还是看 adb devices)
    """
    ps = (
        "$d = Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.InstanceId -match 'VID_2717' -and $_.FriendlyName -match 'ADB' }; "
        "if ($d) { ($d | Select-Object -First 1).InstanceId }"
    )
    out = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
               "-Command", ps], timeout=20)
    m = re.search(r"\\([0-9A-Za-z]{6,})\s*$", out.strip())
    return m.group(1) if m else ""


def get_phone_ip():
    """尽力猜出本机局域网 IP"""
    out = run(["powershell", "-NoProfile", "-Command",
               "(Get-NetIPAddress -AddressFamily IPv4 | "
               "Where-Object {$_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*'} | "
               "Select-Object -First 1).IPAddress"], timeout=15)
    ip = out.strip().splitlines()[-1].strip() if out.strip() else ""
    return ip if re.match(r"^\d+\.\d+\.\d+\.\d+$", ip) else ""


# ============ 主窗口 ============
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VER}")
        self.geometry("760x620")
        self.minsize(700, 560)

        self.serial = None
        self.scrcpy_proc = None
        self.overlay_proc = None
        self.qr_proc = None
        self._qr_img_ref = None
        self.screen_on = True          # 新版默认亮屏，方便看到锁屏/密码界面并点击
        self._anchor_thread = None
        self._anchor_stop = threading.Event()

        self._build_ui()
        self._check_env()
        self.refresh_devices()
        self._start_watchdog()

    # ---------- 设备巡检 ----------
    def _start_watchdog(self):
        """
        每 5 秒看一次 USB/无线设备状态:
          * 已连接设备消失 (拔线 / 掉 wifi) -> 清空选中, scrcpy 已退出则提示
          * 之前没有设备, 现在插上了 -> 自动刷新
        """
        def tick():
            try:
                self._watchdog_tick()
                self._refresh_ui_states()
            except Exception:
                pass
            self.after(5000, tick)
        self.after(6000, tick)

    def _watchdog_tick(self):
        try:
            ready = [d[0] for d in adb_devices() if d[1] == "device"]
        except Exception:
            return

        prev = getattr(self, "_last_ready", None)
        self._last_ready = ready
        if prev is None:
            return

        # 设备消失
        gone = [s for s in prev if s not in ready]
        if gone:
            self.log(f"设备已断开: {', '.join(gone)}")
            if self.serial in gone:
                self.serial = None
                self.dev_var.set("")
                kind = "数据线被拔出或USB调试被关闭" if is_usb_serial(gone[0]) \
                       else "无线调试断开或Wi-Fi切换"
                self.log(f"   -> 可能是{kind}")
                self.log("   -> 恢复后点「刷新」，或重新插一下数据线")
            self.refresh_devices()
            return

        # 设备新出现
        added = [s for s in ready if s not in prev]
        if added:
            names = ", ".join(("USB " if is_usb_serial(s) else "无线 ") + s for s in added)
            self.log(f"检测到新设备: {names}")
            self.refresh_devices()

    # ---------- 界面 ----------
    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        # 顶部: 设备
        top = ttk.LabelFrame(self, text=" 设备 ", padding=10)
        top.pack(fill="x", padx=12, pady=(12, 6))

        # 连接方式: 无线 / 有线 USB  (默认自动)
        mode_row = ttk.Frame(top)
        mode_row.pack(fill="x", pady=(0, 8))
        ttk.Label(mode_row, text="连接方式:").pack(side="left")
        self.link_mode = tk.StringVar(value="auto")
        for text, val in (("自动", "auto"), ("有线 USB", "usb"), ("无线 Wi-Fi", "wifi")):
            ttk.Radiobutton(mode_row, text=text, value=val,
                            variable=self.link_mode,
                            command=self._on_mode_changed).pack(side="left", padx=(8, 0))
        self.usb_hint = tk.StringVar(value="")
        self.usb_hint_label = ttk.Label(mode_row, textvariable=self.usb_hint,
                                        foreground="#888")
        self.usb_hint_label.pack(side="left", padx=(16, 0))

        row1 = ttk.Frame(top)
        row1.pack(fill="x")
        ttk.Label(row1, text="已发现设备:").pack(side="left")
        self.dev_var = tk.StringVar()
        self.dev_combo = ttk.Combobox(row1, textvariable=self.dev_var,
                                      state="readonly", width=48)
        self.dev_combo.pack(side="left", padx=8)
        self.dev_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        ttk.Button(row1, text="刷新", command=self.refresh_devices, width=8).pack(side="left")

        row2 = ttk.Frame(top)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Button(row2, text="连接并投屏", command=self.connect, width=16).pack(side="left")
        ttk.Button(row2, text="断开", command=self.disconnect_all, width=8).pack(side="left", padx=6)
        ttk.Button(row2, text="声音自检", command=self.ping_sound, width=10).pack(side="left")
        ttk.Button(row2, text="快捷键贴图", command=self.toggle_overlay, width=12).pack(side="left", padx=6)

        # 第二排: 投屏过程中的即时控制
        row3 = ttk.Frame(top)
        row3.pack(fill="x", pady=(6, 0))
        self.screen_var = tk.StringVar(value="手机：亮屏")
        ttk.Button(row3, textvariable=self.screen_var, command=self.toggle_screen,
                   width=14).pack(side="left")
        self.topmost_var = tk.StringVar(value="投屏置顶：关")
        ttk.Button(row3, textvariable=self.topmost_var, command=self.toggle_topmost,
                   width=14).pack(side="left", padx=6)
        self.follow_var = tk.StringVar(value="贴图跟随：开")
        ttk.Button(row3, textvariable=self.follow_var, command=self.toggle_follow,
                   width=14).pack(side="left")

        # 配对 (两种方式: 手动填写 / 二维码扫码)
        pair = ttk.LabelFrame(self, text=" 首次配对 (新手机需要一次) ", padding=10)
        pair.pack(fill="x", padx=12, pady=6)

        nb = ttk.Notebook(pair)
        nb.pack(fill="x")

        # ---- 方式一: 手动填写 ----
        tab1 = ttk.Frame(nb, padding=8)
        nb.add(tab1, text="  手动填写配对码  ")

        pr1 = ttk.Frame(tab1)
        pr1.pack(fill="x")
        ttk.Label(pr1, text="IP 地址:").pack(side="left")
        self.pair_ip = tk.StringVar()
        ttk.Entry(pr1, textvariable=self.pair_ip, width=18).pack(side="left", padx=(6, 14))
        ttk.Label(pr1, text="端口:").pack(side="left")
        self.pair_port = tk.StringVar()
        ttk.Entry(pr1, textvariable=self.pair_port, width=8).pack(side="left", padx=(6, 14))
        ttk.Label(pr1, text="配对码:").pack(side="left")
        self.pair_code = tk.StringVar()
        ttk.Entry(pr1, textvariable=self.pair_code, width=14).pack(side="left", padx=6)

        pr1b = ttk.Frame(tab1)
        pr1b.pack(fill="x", pady=(8, 0))
        ttk.Button(pr1b, text="开始配对", command=self.do_pair, width=12).pack(side="left")
        ttk.Label(pr1b, text="  手机: 无线调试 → 使用配对码配对设备").pack(side="left")

        # ---- 方式二: 二维码 ----
        tab2 = ttk.Frame(nb, padding=8)
        nb.add(tab2, text="  二维码扫码  ")

        qr_left = ttk.Frame(tab2)
        qr_left.pack(side="left", fill="y")
        ttk.Label(qr_left, text="① 手机上点\n「使用二维码配对设备」\n（会显示手机摄像头画面）",
                  justify="left").pack(anchor="w")
        ttk.Button(qr_left, text="生成二维码", command=self.start_qr_pair,
                   width=16).pack(anchor="w", pady=(10, 0))
        ttk.Button(qr_left, text="取消", command=self.stop_qr_pair,
                   width=16).pack(anchor="w", pady=(6, 0))

        qr_right = ttk.Frame(tab2)
        qr_right.pack(side="left", fill="both", expand=True, padx=(16, 0))
        ttk.Label(qr_right, text="② 用手机扫这里的二维码，扫完自动配对并连接",
                  justify="left").pack(anchor="w")
        self.qr_label = tk.Label(qr_right, text="点「生成二维码」后显示",
                                 width=32, height=10, relief="sunken",
                                 bg="white", fg="#888")
        self.qr_label.pack(anchor="w", pady=(6, 0))
        self.qr_status = tk.StringVar(value="")
        ttk.Label(qr_right, textvariable=self.qr_status,
                  foreground="#0a7").pack(anchor="w", pady=(6, 0))

        # 音量
        vol = ttk.LabelFrame(self, text=" 手机媒体音量 ", padding=10)
        vol.pack(fill="x", padx=12, pady=6)
        vr = ttk.Frame(vol)
        vr.pack(fill="x")
        self.vol_label = tk.StringVar(value="--")
        ttk.Label(vr, text="当前:").pack(side="left")
        ttk.Label(vr, textvariable=self.vol_label, width=12).pack(side="left")
        ttk.Button(vr, text="设为 60%", command=lambda: self.set_volume(90), width=10).pack(side="left", padx=4)
        ttk.Button(vr, text="设为 40%", command=lambda: self.set_volume(60), width=10).pack(side="left", padx=4)
        ttk.Button(vr, text="刷新", command=self.refresh_volume, width=8).pack(side="left", padx=4)

        # 日志
        logf = ttk.LabelFrame(self, text=" 运行日志 ", padding=6)
        logf.pack(fill="both", expand=True, padx=12, pady=(6, 12))
        self.log_text = scrolledtext.ScrolledText(logf, height=12, wrap="word",
                                                  font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # ---------- 日志 ----------
    def log(self, msg):
        def _do():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", time.strftime("[%H:%M:%S] ") + str(msg) + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        try:
            self.after(0, _do)
        except Exception:
            pass

    # ---------- 环境检查 ----------
    def _check_env(self):
        missing = []
        if not os.path.exists(ADB):
            missing.append(f"adb.exe  (应在 {ADB})")
        if not os.path.exists(SCRCPY):
            missing.append(f"scrcpy.exe  (应在 {SCRCPY})")
        if missing:
            self.log("!! 缺少必要文件:")
            for m in missing:
                self.log("   " + m)
            self.log("-> 请确认 tools 文件夹与本程序在同一目录")
            messagebox.showerror(
                "缺少文件",
                "找不到 adb 或 scrcpy。\n\n请确保 tools 文件夹与程序在同一目录：\n"
                f"{TOOLS}")
        else:
            self.log(f"环境检查通过 (adb + scrcpy 就绪)")
            self.log(f"程序目录: {BASE}")

    # ---------- 设备 ----------
    def _on_mode_changed(self):
        label = {"auto": "自动", "usb": "有线 USB", "wifi": "无线 Wi-Fi"}[self.link_mode.get()]
        self.log(f"连接方式切换为: {label}")
        self.refresh_devices()

    def _scan_usb_state(self):
        """检查 USB 口上有没有手机 (不依赖 adb 授权状态)"""
        present, desc = usb_device_present()
        devs = adb_devices()
        usb_ready = [d for d in devs if d[1] == "device" and is_usb_serial(d[0])]
        usb_unauth = [d for d in devs if d[1] != "device" and is_usb_serial(d[0])]

        if usb_ready:
            hint, color = f"有线: 已连接 ({usb_ready[0][0]})", "#0a7"
        elif usb_unauth:
            hint, color = "有线: 已插入，请在手机上点「允许USB调试」", "#c60"
        elif present:
            hint, color = f"有线: 已插入，{desc}（检查手机是否开了USB调试）", "#c60"
        else:
            hint, color = "有线: 未检测到设备", "#888"
        return hint, color, usb_ready

    def refresh_devices(self):
        if not os.path.exists(ADB):
            return
        def work():
            # 先探测 USB 状态 (给界面提示)
            try:
                hint, color, usb_ready = self._scan_usb_state()
            except Exception:
                hint, color, usb_ready = "有线: 检测失败", "#888", []

            def _apply_hint():
                self.usb_hint.set(hint)
                try:
                    self.usb_hint_label.configure(foreground=color)
                except Exception:
                    pass
            self.after(0, _apply_hint)

            devs = adb_devices()
            ready = [d for d in devs if d[1] == "device"]

            # 列表为空但 mDNS 能看到服务 -> 自动 connect (配对后常见)
            mode = self.link_mode.get()
            if not ready and mode in ("auto", "wifi"):
                auto = self._mdns_auto_connect()
                if auto:
                    devs = adb_devices()
                    ready = [d for d in devs if d[1] == "device"]

            # 按连接方式过滤
            def keep(serial):
                if mode == "usb":
                    return is_usb_serial(serial)
                if mode == "wifi":
                    return not is_usb_serial(serial)
                return True

            ready = [d for d in ready if keep(d[0])]
            others = [d for d in devs if d[1] != "device" and keep(d[0])]

            items = []
            for s, st in ready:
                tag = "USB 有线" if is_usb_serial(s) else "Wi-Fi 无线"
                items.append(f"{s}   [{tag}]  已连接")
            for s, st in others:
                tag = "USB" if is_usb_serial(s) else "Wi-Fi"
                items.append(f"{s}   [{tag}]  {st}")

            def _apply():
                self.dev_combo["values"] = items
                cur = self.dev_var.get()
                # 当前选中的设备没了 或 是首次填充 -> 重新选
                if items and (not cur or cur not in items):
                    # 自动模式下优先选 USB (有线更稳)
                    chosen = items[0]
                    if mode == "auto":
                        for it in items:
                            if "USB" in it and "已连接" in it:
                                chosen = it
                                break
                    self.dev_var.set(chosen)
                    self._on_device_selected()
                if not items:
                    self.dev_var.set("")
                    self.serial = None
                self.log(f"设备扫描: 发现 {len(ready)} 台可用" +
                         (f", {len(others)} 台未授权" if others else "") +
                         f"  (方式: { {'auto': '自动', 'usb': '有线', 'wifi': '无线'}[mode] })")
            self.after(0, _apply)
        threading.Thread(target=work, daemon=True).start()

    def _mdns_auto_connect(self):
        """设备列表空时, 尝试用 mDNS 发现的服务自动连接. 成功返回 True"""
        try:
            out = run([ADB, "mdns", "services"], timeout=15)
            targets = []
            for line in out.splitlines():
                if "_adb-tls-connect._tcp" in line:
                    m = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)\s*$", line.strip())
                    if m:
                        targets.append(m.group(1))
            for tgt in targets:
                r = run([ADB, "connect", tgt], timeout=20)
                if "connected to" in r:
                    time.sleep(1.2)
                    for ln in run([ADB, "devices"]).splitlines()[1:]:
                        if tgt in ln and "\tdevice" in ln:
                            self.log(f"已自动重连: {tgt}")
                            return True
        except Exception:
            pass
        return False

    def _on_device_selected(self, *_):
        v = self.dev_var.get()
        self.serial = v.split("   [")[0].strip() if v else None
        if self.serial:
            self.log(f"选中设备: {self.serial}")
            self.refresh_volume()

    # ---------- 投屏窗口即时控制 ----------
    def _set_screen_label(self):
        self.screen_var.set("手机：黑屏" if self.screen_on else "手机：亮屏")

    def toggle_screen(self):
        """黑屏/亮屏切换。走 scrcpy 自己的快捷键，只关显示面板、不触发锁屏。"""
        if not win32util or not win32util.find_window():
            messagebox.showwarning("投屏未运行", "请先点「连接并投屏」")
            return
        want_off = self.screen_on
        ok, msg = win32util.send_screen_key(want_off)
        if ok:
            self.screen_on = not want_off
            self._set_screen_label()
            self.log(msg)
            if want_off:
                self.log("   手机面板已灭但仍在投屏；要操作锁屏/密码请先切回亮屏")
        else:
            self.log(f"黑屏/亮屏发送失败: {msg}")

    def toggle_topmost(self):
        if not win32util:
            return
        hwnd = win32util.find_window()
        if not hwnd:
            messagebox.showwarning("投屏未运行", "请先点「连接并投屏」")
            return
        want = not win32util.is_topmost(hwnd)
        win32util.set_topmost(hwnd, want)
        self.topmost_var.set("投屏置顶：开" if want else "投屏置顶：关")
        self.log(f"投屏窗口置顶已{'开启' if want else '关闭'}")
        # 同步给贴图标题栏的置顶按钮
        self._write_overlay_cmd("topmost_on" if want else "topmost_off")

    def toggle_follow(self):
        """控制快捷键贴图是否跟随投屏窗口（贴图进程内每 300ms 读取命令文件）"""
        on = self.follow_var.get().endswith("关")
        self.follow_var.set("贴图跟随：开" if on else "贴图跟随：关")
        self._write_overlay_cmd("follow_on" if on else "follow_off")
        self.log(f"贴图跟随已{'开启' if on else '关闭'}")

    @staticmethod
    def _write_overlay_cmd(cmd):
        try:
            with open(OVERLAY_CMD, "w", encoding="utf-8") as f:
                f.write(cmd)
        except Exception as e:
            print("write overlay cmd failed:", e)

    def _refresh_ui_states(self):
        """从窗口/贴图状态文件回读，让按钮文字和实际状态一致"""
        try:
            if win32util:
                hwnd = win32util.find_window()
                if hwnd:
                    on = win32util.is_topmost(hwnd)
                    self.topmost_var.set("投屏置顶：开" if on else "投屏置顶：关")
            if os.path.exists(OVERLAY_STATUS):
                with open(OVERLAY_STATUS, encoding="utf-8", errors="ignore") as f:
                    line = f.readline()
                m = re.search(r"follow=(True|False)", line)
                if m:
                    on = m.group(1) == "True"
                    self.follow_var.set("贴图跟随：开" if on else "贴图跟随：关")
        except Exception:
            pass

    # ---------- 旋转锚点 ----------
    def _start_anchor(self):
        """投屏画面横竖屏切换时窗口尺寸会突变，按旧中心点重新定位，避免跳太远。"""
        if self._anchor_thread and self._anchor_thread.is_alive():
            return
        self._anchor_stop.clear()
        proc = self.scrcpy_proc

        def loop():
            last = None
            l, t, r, b = win32util.work_area() if win32util else (0, 0, 1920, 1080)
            while not self._anchor_stop.is_set() and proc and proc.poll() is None:
                try:
                    hwnd = win32util.find_window()
                    cur = win32util.rect(hwnd) if hwnd else None
                    if (cur and not win32util.is_minimized(hwnd)
                            and not win32util.is_maximized(hwnd)
                            and cur[2] > 100 and cur[3] > 100):
                        x, y, w, h = cur
                        # 全屏/接近整个工作区时不干预
                        fullscreen = (w >= r - l - 8 and h >= b - t - 8)
                        if last and not fullscreen:
                            pw, ph = last[2], last[3]
                            # 只有明显变化（横竖屏翻转）才重定位，普通缩放跟随用户
                            if abs(w - pw) > 120 or abs(h - ph) > 120:
                                nx = last[0] + (pw - w) // 2
                                ny = last[1] + (ph - h) // 2
                                nx = max(l, min(nx, r - w))
                                ny = max(t, min(ny, b - h))
                                win32util.set_pos(hwnd, nx, ny)
                                x, y = nx, ny
                                self.log("画面方向变化，投屏窗口已按中心点重新定位")
                        last = (x, y, w, h)
                except Exception:
                    pass
                time.sleep(0.25)

        self._anchor_thread = threading.Thread(target=loop, daemon=True)
        self._anchor_thread.start()

    # ---------- 音量 ----------
    def refresh_volume(self):
        if not self.serial:
            self.vol_label.set("未连接")
            return
        s = self.serial
        def work():
            out = adb(s, "shell", "cmd media_session volume --stream 3 --get")
            m = re.search(r"volume is (\d+)(?: in range \[0\.\.(\d+)\])?", out)
            if m:
                cur, mx = int(m.group(1)), int(m.group(2) or 150)
                pct = round(cur / mx * 100)
                self.after(0, lambda: self.vol_label.set(f"{cur}/{mx} ({pct}%)"))
            else:
                self.after(0, lambda: self.vol_label.set("读取失败"))
        threading.Thread(target=work, daemon=True).start()

    def set_volume(self, target):
        if not self.serial:
            messagebox.showwarning("未连接", "请先连接设备")
            return
        s = self.serial
        def work():
            for _ in range(20):
                out = adb(s, "shell", "cmd media_session volume --stream 3 --get")
                m = re.search(r"volume is (\d+)", out)
                if not m:
                    break
                cur = int(m.group(1))
                if abs(cur - target) <= VOL_STEP // 2:
                    break
                adb(s, "shell",
                    "input keyevent 25" if cur > target else "input keyevent 24")
                time.sleep(0.7)
            self.log(f"音量已调整到目标 {target}")
            self.refresh_volume()
        threading.Thread(target=work, daemon=True).start()

    # ---------- 配对 ----------
    def do_pair(self):
        ip = self.pair_ip.get().strip()
        port = self.pair_port.get().strip()
        code = self.pair_code.get().strip()

        if not ip or not port or not code:
            messagebox.showwarning("信息不全", "请填写 IP 地址、端口和配对码")
            return
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            messagebox.showwarning("格式错误", "IP 地址应形如 192.168.1.10")
            return
        if not port.isdigit():
            messagebox.showwarning("格式错误", "端口应该是数字")
            return
        threading.Thread(target=self._pair_worker,
                         args=(f"{ip}:{port}", code), daemon=True).start()

    # ---------- 二维码配对 ----------
    #
    # 原理 (实测确认):
    #   adb pair --qr 会自己生成一次性的配对凭据 (服务名 + 随机密码),
    #   并在终端里用半块字符把二维码画出来, 然后挂起等手机扫码。
    #   这两项凭据每次运行都不同, 不能自己伪造, 只能从 adb 的输出里取。
    #   所以: 启动 adb -> 抓终端字符 -> 还原成像素 -> 解码出 WIFI:T:ADB;...
    #        -> 用 qrcode 库重绘成干净大图 -> 显示在窗口里。
    #   手机扫屏幕后, adb 自己完成配对并打印 Successfully paired。
    def start_qr_pair(self):
        if self.qr_proc and self.qr_proc.poll() is None:
            self.log("二维码配对已在进行中")
            return
        try:
            import qrcode  # noqa: F401
            from PIL import Image, ImageTk  # noqa: F401
        except ImportError:
            messagebox.showerror("缺少组件", "二维码功能需要 qrcode 与 Pillow 组件")
            return

        self.qr_status.set("正在生成二维码 ...")
        self.qr_label.configure(image="", text="正在生成 ...")
        self._qr_img_ref = None
        threading.Thread(target=self._qr_worker, daemon=True).start()

    # 把 adb 终端里的 █ ▀ ▄ 还原成像素矩阵 (1 = 黑)
    @staticmethod
    def _blocks_to_matrix(text):
        rows = []
        for ln in text.splitlines():
            if not any(ch in ln for ch in "\u2588\u2580\u2584"):
                continue
            top, bot = [], []
            for ch in ln:
                if ch == "\u2588":      # █ 整块黑
                    top.append(1); bot.append(1)
                elif ch == "\u2580":    # ▀ 上半黑
                    top.append(1); bot.append(0)
                elif ch == "\u2584":    # ▄ 下半黑
                    top.append(0); bot.append(1)
                else:
                    top.append(0); bot.append(0)
            rows.append(top)
            rows.append(bot)
        while rows and not any(rows[-1]):
            rows.pop()
        return rows

    # 在矩阵里寻找二维码区域并解码 (实现见 qr_locator.py)
    @staticmethod
    def _decode_matrix(rows):
        import qr_locator
        return qr_locator.decode(rows)

    def _qr_worker(self):
        self.log("二维码配对: adb 正在生成一次性配对凭据 ...")
        proc = subprocess.Popen(
            [ADB, "pair", "--qr"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.qr_proc = proc

        chunks = []

        def reader():
            while True:
                b = proc.stdout.read(1)
                if not b:
                    break
                chunks.append(b)

        threading.Thread(target=reader, daemon=True).start()

        payload = ""
        for _ in range(20):          # 最多等 8 秒左右
            time.sleep(0.4)
            raw = b"".join(chunks).decode("utf-8", "replace")
            plain = re.sub(r"\x1b\[[0-9;]*m", "", raw)
            if "Waiting for device" not in plain:
                continue
            try:
                payload = self._decode_matrix(self._blocks_to_matrix(plain))
            except Exception as e:
                self.log(f"二维码解析异常: {e}")
            if payload:
                break

        if not payload:
            self.log("二维码生成失败, adb 没给出可识别的二维码")
            self.log("   -> 请改用「手动填写配对码」方式")
            self.qr_status.set("生成失败，请改用手动填写")
            try:
                proc.kill()
            except Exception:
                pass
            self.qr_proc = None
            return

        m = re.match(r"WIFI:T:ADB;S:(.*?);P:(.*?);;", payload)
        self.log("二维码已生成" + (f" (服务名 {m.group(1)})" if m else ""))
        self.log("请用手机扫下面的二维码（手机: 设置 → 开发者选项 → 无线调试 → 使用二维码配对设备）")

        # 用 qrcode 重绘成干净的大图
        try:
            import qrcode
            from PIL import Image, ImageTk
            q = qrcode.QRCode(border=2, box_size=8,
                              error_correction=qrcode.constants.ERROR_CORRECT_L)
            q.add_data(payload)
            q.make()
            img = q.make_image().convert("RGB")

            def show():
                tkimg = ImageTk.PhotoImage(img)
                self._qr_img_ref = tkimg
                self.qr_label.configure(image=tkimg, text="", width=img.width, height=img.height)
                self.qr_status.set("等待手机扫码 ...")
            self.after(0, show)
        except Exception as e:
            self.log(f"二维码绘制失败: {e}")
            self.qr_status.set("绘制失败")
            return

        # 等 adb 自己完成配对
        self.log("等待手机扫码 ...")
        try:
            proc.wait(timeout=180)
        except subprocess.TimeoutExpired:
            self.log("二维码等待超时（3 分钟）")
            self.qr_status.set("等待超时，请重新生成")
            try:
                proc.kill()
            except Exception:
                pass
            self.qr_proc = None
            return

        out = b"".join(chunks).decode("utf-8", "replace")
        self.qr_proc = None
        self.log(out.strip()[-300:])

        if "Successfully paired" not in out:
            self.log("扫码配对失败, 请重新生成二维码再扫一次")
            self.qr_status.set("配对失败，请重新生成")
            self.after(0, lambda: self.qr_label.configure(image="", text="配对失败，请重新生成"))
            return

        self.log("扫码配对成功 —— 正在建立连接 ...")
        self.qr_status.set("配对成功")
        self.after(0, lambda: self.qr_label.configure(image="", text="配对成功 ✓"))
        # 二维码凭据里没有手机 IP, 直接从 mDNS 广播里找连接端口
        connected = self._auto_connect_by_mdns()
        if connected:
            self.log(f"连接成功: {connected}")
            self.after(0, lambda: messagebox.showinfo(
                "配对并连接成功", "手机已配对并连接，现在可以点「连接并投屏」"))
        else:
            self.log("配对成功但自动连接失败, 请点「刷新」")
            self.after(0, lambda: messagebox.showwarning(
                "需要手动连接",
                "配对已成功，但没能自动连上。\n\n"
                "请回到手机「无线调试」主页面查看显示的 IP 地址和端口，然后点「刷新」。"))
        self.refresh_devices()

    def stop_qr_pair(self):
        if self.qr_proc and self.qr_proc.poll() is None:
            try:
                self.qr_proc.kill()
            except Exception:
                pass
            self.log("已取消二维码配对")
        self.qr_proc = None
        self._qr_img_ref = None
        self.qr_status.set("已取消")
        self.qr_label.configure(image="", text="点「生成二维码」后显示", width=32, height=10)

    def _pair_worker(self, addr, code):
        self.log(f"正在配对 {addr} ...")
        out = run([ADB, "pair", addr, code], timeout=40)
        self.log(out.strip())

        if "Successfully paired" not in out:
            if "protocol fault" in out:
                self.log("配对失败：这个端口已经失效或配对码过期")
                self.log("   -> 请重新在手机上点「使用配对码配对设备」，")
                self.log("      把【最新】显示的 IP:端口 和 配对码填进来")
            else:
                self.log("配对失败，请确认配对码和地址是否最新（配对码每次打开都不同）")
            return

        self.log("配对成功 —— 正在建立连接 ...")

        # 关键: adb pair 只交换密钥, 不会建立连接, 必须再 connect 一次
        ip = addr.split(":")[0]
        connected = self._auto_connect(ip)

        if connected:
            self.log(f"连接成功: {connected}")
            self.after(0, lambda: messagebox.showinfo(
                "配对并连接成功", "手机已连接，现在可以点「连接并投屏」"))
        else:
            self.log("配对成功但自动连接失败")
            self.log("   -> 请点「刷新」, 或手动在手机「无线调试」主页面查看 IP:端口")
            self.after(0, lambda: messagebox.showwarning(
                "需要手动连接",
                "配对已成功，但没能自动连上。\n\n"
                "请回到手机「无线调试」主页面（不是配对弹窗），\n"
                "查看那里显示的 IP 地址和端口，然后点「刷新」。"))
        self.refresh_devices()

    def _auto_connect_by_mdns(self):
        """不指定 IP, 直接从 mDNS 广播里找 _adb-tls-connect 端口并连接"""
        for _ in range(5):
            out = run([ADB, "mdns", "services"], timeout=20)
            targets = []
            for line in out.splitlines():
                if "_adb-tls-connect._tcp" in line:
                    m = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)\s*$", line.strip())
                    if m:
                        targets.append(m.group(1))
            for tgt in targets:
                r = run([ADB, "connect", tgt], timeout=25)
                if "connected to" in r:
                    time.sleep(1.5)
                    for ln in run([ADB, "devices"]).splitlines()[1:]:
                        if tgt in ln and "\tdevice" in ln:
                            return tgt
            time.sleep(2)
        return None

    def _auto_connect(self, ip):
        """配对后自动找连接端口并 connect, 返回成功的串号或 None"""
        # 1. 先用 mDNS 发现的连接服务 (配对服务端口与连接端口不同)
        for attempt in range(4):
            out = run([ADB, "mdns", "services"], timeout=20)
            ports = set()
            for line in out.splitlines():
                if "_adb-tls-connect._tcp" in line and ip in line:
                    m = re.search(r":(\d+)\s*$", line.strip())
                    if m:
                        ports.add(m.group(1))
            for port in ports:
                tgt = f"{ip}:{port}"
                r = run([ADB, "connect", tgt], timeout=25)
                if "connected to" in r:
                    # 确认真的在线 device 状态
                    time.sleep(1.5)
                    for line in run([ADB, "devices"]).splitlines()[1:]:
                        if tgt in line and "\tdevice" in line:
                            self.log(f"   mDNS 连接成功: {tgt}")
                            return tgt
            time.sleep(2)

        # 2. mDNS 拿不到就用 adb devices 里出现的任何本机设备
        for line in run([ADB, "devices"]).splitlines()[1:]:
            if ip in line and "\tdevice" in line:
                return line.split("\t")[0].strip()
        return None

    # ---------- 连接 ----------
    def connect(self):
        if not self.serial:
            # 分情况给提示, 别让用户干瞪眼
            present, desc = usb_device_present()
            if self.link_mode.get() in ("auto", "usb") and present:
                messagebox.showwarning(
                    "有线设备未就绪",
                    "检测到手机插在 USB 上，但 adb 还没连上。\n\n"
                    "请按顺序检查：\n"
                    "1. 手机「开发者选项」→ 打开「USB调试」\n"
                    "2. 手机弹出「允许USB调试吗？」→ 点「允许」（建议勾选\"一律允许\"）\n"
                    "3. 数据线插好（要能传数据的线，不是只能充电的线）\n"
                    "4. 老系统还要选「USB连接方式」→「传输文件(MTP)」\n\n"
                    f"当前检测: {desc}\n\n"
                    "弄好后点「刷新」重新扫描。")
            else:
                messagebox.showwarning(
                    "没有设备",
                    "未发现设备。\n\n"
                    "· 有线投屏：用数据线连电脑，手机开「USB调试」并允许授权\n"
                    "· 无线投屏：手机开「无线调试」，新手机先在下方完成配对")
            return
        if self.scrcpy_proc and self.scrcpy_proc.poll() is None:
            messagebox.showinfo("已在运行", "投屏已在运行中")
            return
        s = self.serial
        threading.Thread(target=self._connect_worker, args=(s,), daemon=True).start()

    def _connect_worker(self, serial):
        self.log("=" * 46)
        usb = is_usb_serial(serial)
        self.log(f"开始连接流程 ({'USB 有线' if usb else 'Wi-Fi 无线'})")

        # 1. 蓝牙关闭 (否则 A2DP 抢音频路由)
        adb(serial, "shell", "svc bluetooth disable")
        time.sleep(1)
        bt = adb(serial, "shell", "settings get global bluetooth_on").strip()
        self.log(f"[1/4] 蓝牙: {'已关闭' if bt == '0' else '仍开启(可能影响声音)'}")

        # 2. 带宽实测 -> 定码率
        self.log("[2/4] 实测链路带宽 ...")
        bitrate, why = pick_video_bitrate(serial, usb)
        self.log(f"     {why}")
        self.log(f"     目标: 60fps / {bitrate} Mbps")

        # 3. scrcpy
        self.log("[3/4] 启动 scrcpy ...")
        run(["taskkill", "/F", "/IM", "scrcpy.exe", "/T"])
        time.sleep(1.5)
        # 不再 --turn-screen-off: 面板断电时安全界面(锁屏/密码)不会渲染进视频,
        # 触摸事件也会被系统丢弃, 导致"看得见却点不了"。改成亮屏 + --keep-active。
        argline = (f'-s "{serial}" --keep-active '
                   f'--audio-source=output --audio-buffer=200 --audio-output-buffer=50 '
                   f'--max-fps=60 --video-bit-rate={bitrate}M')
        logf = os.path.join(BASE, "scrcpy_run.log")
        errf = os.path.join(BASE, "scrcpy_run.err")
        try:
            self.scrcpy_proc = subprocess.Popen(
                f'"{SCRCPY}" {argline}',
                cwd=SCRCPY_DIR,
                stdout=open(logf, "w", encoding="utf-8", errors="ignore"),
                stderr=open(errf, "w", encoding="utf-8", errors="ignore"),
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            self.log(f"scrcpy 启动失败: {e}")
            return
        time.sleep(6)
        if self.scrcpy_proc.poll() is not None:
            self.log("scrcpy 已退出，错误信息:")
            try:
                with open(errf, encoding="utf-8", errors="ignore") as f:
                    for ln in f.readlines()[:6]:
                        self.log("   " + ln.rstrip())
            except Exception:
                pass
            return
        self.log(f"      scrcpy PID={self.scrcpy_proc.pid} 运行中")
        self.screen_on = True
        self._set_screen_label()
        self._start_anchor()

        # 4. 测试音
        self.log("[4/4] 播放测试音 ...")
        self._ping(serial)
        self.log("连接完成 —— 手机保持亮屏，锁屏/密码界面可见可点")
        self.log("提示: 小米若锁屏界面点不动，需在手机「开发者选项」打开")
        self.log("      「USB调试（安全设置）」，否则系统禁止模拟点击安全界面")

    # ---------- 测试音 ----------
    def ping_sound(self):
        if not self.serial:
            messagebox.showwarning("未连接", "请先连接设备")
            return
        threading.Thread(target=self._ping, args=(self.serial,), daemon=True).start()

    def _ping(self, serial):
        out = adb(serial, "shell", "dumpsys audio | grep -m 1 'Ringer mode'")
        m = re.search(r"Ringer mode:\s*(\d+)", out)
        before = m.group(1) if m else None
        adb(serial, "shell", "cmd audio set-ringer-mode SILENT")
        time.sleep(0.9)
        adb(serial, "shell", "cmd audio set-ringer-mode NORMAL")
        time.sleep(0.5)
        restore = {"0": "SILENT", "1": "VIBRATE", "2": "NORMAL"}.get(before or "")
        if restore and restore != "NORMAL":
            adb(serial, "shell", f"cmd audio set-ringer-mode {restore}")
        self.log("已播放测试提示音")

    # ---------- 贴图 ----------
    def toggle_overlay(self):
        """贴图已打包进 exe, 用 --overlay 模式启动自身"""
        exe = sys.executable if getattr(sys, "frozen", False) else None
        overlay_src = os.path.join(BASE, "scrcpy_hotkey_overlay.py")

        # 先看是否已在运行 -> 关闭。
        # 注意: 不能用 CommandLine -like '*--overlay*', 那会把"包含这段查询文本的
        # PowerShell 自身"也匹配上, 导致刚启动就被自己关掉 (实测踩过)。
        pids = []
        if self.overlay_proc and self.overlay_proc.poll() is None:
            pids.append(str(self.overlay_proc.pid))
        if getattr(sys, "frozen", False):
            exe_name = os.path.basename(sys.executable)
            ps = (
                f"$n = '{exe_name}'; "
                "Get-CimInstance Win32_Process | "
                "Where-Object { $_.Name -eq $n -and $_.CommandLine -match '\\s--overlay(\\s|$)' } | "
                "Select-Object -ExpandProperty ProcessId"
            )
            out = run(["powershell", "-NoProfile", "-Command", ps], timeout=20)
            pids += [p.strip() for p in out.splitlines() if p.strip().isdigit()]
        if pids:
            for pid in set(pids):
                run(["taskkill", "/F", "/PID", pid])
            self.overlay_proc = None
            self.log("已关闭快捷键贴图")
            return

        try:
            if exe:
                # 打包后: 用自己再启动一个 --overlay 实例
                self.overlay_proc = subprocess.Popen([exe, "--overlay"],
                                                     creationflags=CREATE_NO_WINDOW)
            else:
                # 源码运行: 直接调 python
                self.overlay_proc = subprocess.Popen(
                    [sys.executable, os.path.join(BASE, "overlay_main.py")],
                    creationflags=CREATE_NO_WINDOW)
            self.log("已启动快捷键贴图")
        except Exception as e:
            self.log(f"贴图启动失败: {e}")

    # ---------- 断开 ----------
    def disconnect_all(self):
        run(["taskkill", "/F", "/IM", "scrcpy.exe", "/T"])
        self._anchor_stop.set()
        self.scrcpy_proc = None
        self.screen_on = True
        self._set_screen_label()
        self.log("已断开投屏")


if __name__ == "__main__":
    # --overlay: 以贴图模式运行 (由主窗口「快捷键贴图」按钮拉起)
    if "--overlay" in sys.argv:
        try:
            import overlay_main
            overlay_main.main()
        except Exception:
            pass
        sys.exit(0)

    if not os.path.exists(ADB):
        # 没有 tk 时也要能给出提示
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("缺少文件", f"找不到 adb.exe\n期望位置:\n{ADB}")
            root.destroy()
        except Exception:
            print(f"找不到 adb.exe: {ADB}")
        sys.exit(1)

    App().mainloop()
