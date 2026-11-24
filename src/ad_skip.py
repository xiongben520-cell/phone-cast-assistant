# -*- coding: utf-8 -*-
"""红果免费短剧 —— 广告自动上滑跳过（投屏助手内置模块）

设计要点:
  * 不依赖外部 mobile-mcp / mobilecli，只用本程序自带的 adb.exe
  * OCR 走 src/ocr_helper.ps1（Windows 原生 Windows.Media.Ocr），零 pip 依赖
  * 广告判定与倒计时判定都基于右上角按钮文字（实测两种形态）:
      - "上滑继续观看剧集"        -> 无倒计时，立即上滑
      - "Xs后可继续上滑观看"      -> 有倒计时，等结束再上滑
  * 启停由 GUI 的「跳广告」按钮控制；stop() 后最多一个轮询周期退出
"""
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

try:
    from PIL import Image
except Exception:  # pragma: no cover - PIL 随包分发，正常不会走到
    Image = None

CREATE_NO_WINDOW = 0x08000000
POLL_SEC = 1.2          # 常规轮询间隔
COUNTDOWN_POLL = 0.8    # 倒计时阶段间隔
MAX_AD_SEC = 180        # 单条广告最长等待，超时强制上滑
MAX_WAIT_AFTER_HINT = 15  # “后可继续上滑”长时间读不出数字时的兜底
AFTER_SWIPE_SEC = 1.6   # 上滑后复核等待
COOLDOWN_SEC = 1.5      # 跳过后的冷却

# 横屏 3200x1440：中间偏左空白处上滑，避开右上角广告按钮与底部下载按钮
SWIPE = ("900", "1150", "900", "350")
SWIPE_DURATION = "320"


def _normalize(text):
    """OCR 常见误识：空格/全角/l→1/O→0"""
    t = (text or "").replace(" ", "").replace("　", "")
    t = t.translate(str.maketrans({
        "０": "0", "１": "1", "２": "2", "３": "3", "４": "4", "５": "5",
        "６": "6", "７": "7", "８": "8", "９": "9",
        "l": "1", "I": "1", "|": "1", "i": "1", "O": "0", "ｏ": "0",
        "Ｓ": "S", "ｓ": "s",
    }))
    return t


def countdown_of(text):
    """返回倒计时秒数；无倒计时返回 None。"""
    t = _normalize(text)
    if not t:
        return None
    m = re.search(r"(\d{1,3})\s*(?:s|S|秒)?后", t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    m = re.search(r"(\d{1,3})\s*(?:s|S|秒)", t)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    # 出现“后可继续上滑”但数字没读出来 -> 视为还剩 1 秒，多等一轮
    if "后可继续" in t or "后可上滑" in t or "倒计时" in t:
        return 1
    return None


def is_ad_text(text):
    """右上角 OCR 文本是否来自广告界面"""
    t = _normalize(text)
    if "上滑" not in t and "继续观看" not in t and "观看剧集" not in t:
        return False
    return ("广告" in t or "后可继续" in t or "继续观看" in t)


class AdSkipWatcher:
    def __init__(self, adb, base, log_cb, status_cb=None):
        self.adb = adb
        self.base = base
        self.log_cb = log_cb or (lambda s: None)
        self.status_cb = status_cb or (lambda s: None)
        # PyInstaller 6 onedir 把 --add-data 的文件放进 _internal/（sys._MEIPASS），
        # 而 exe 根目录是 base。两处都找，兼容打包版与源码运行。
        meipass = getattr(sys, "_MEIPASS", "") or ""
        bundled = os.path.join(meipass, "ocr_helper.ps1") if meipass else ""
        self.ocr_ps = bundled if bundled and os.path.exists(bundled) \
            else os.path.join(base, "ocr_helper.ps1")
        self.tmp_dir = os.path.join(base, "adskip_tmp")
        self.log_file = os.path.join(base, "ad_skip.log")
        self._stop = threading.Event()
        self._thread = None
        self._serial = None

    # ---------- 对外接口 ----------
    def start(self):
        if self.is_running():
            return True
        if not os.path.exists(self.adb):
            self._emit("启动失败：找不到 adb.exe")
            return False
        if not os.path.exists(self.ocr_ps):
            self._emit("启动失败：找不到 ocr_helper.ps1")
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._emit("已启动：检测到广告自动上滑（有倒计时会等待结束）")
        return True

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None
        self._emit("已停止")

    def is_running(self):
        return bool(self._thread and self._thread.is_alive())

    # ---------- 内部 ----------
    def _emit(self, msg, status=None):
        line = "[" + datetime.now().strftime("%H:%M:%S") + "] " + msg
        try:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass
        try:
            self.log_cb("[跳广告] " + msg)
        except Exception:
            pass
        if status is not None:
            try:
                self.status_cb(status)
            except Exception:
                pass

    def _get_serial(self):
        try:
            with open(os.path.join(self.base, "device_state.json"), encoding="utf-8") as f:
                s = (json.load(f).get("serial") or "").strip()
            if s:
                return s
        except Exception:
            pass
        try:
            p = subprocess.run([self.adb, "devices"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=10,
                               creationflags=CREATE_NO_WINDOW)
            for line in p.stdout.decode("utf-8", "ignore").splitlines()[1:]:
                if "\tdevice" in line:
                    return line.split("\t")[0].strip()
        except Exception:
            pass
        return ""

    def _adb(self, args, timeout=15, binary=False):
        cmd = [self.adb]
        if self._serial:
            cmd += ["-s", self._serial]
        cmd += list(args)
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=timeout, creationflags=CREATE_NO_WINDOW)
            return p.stdout if binary else p.stdout.decode("utf-8", "ignore")
        except Exception:
            return b"" if binary else ""

    def _ocr(self, png_path):
        if not os.path.exists(self.ocr_ps):
            return ""
        result = os.path.join(self.tmp_dir, "ocr_result.txt")
        try:
            if os.path.exists(result):
                os.remove(result)
            subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", self.ocr_ps, png_path, result],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=20, creationflags=CREATE_NO_WINDOW)
            if os.path.exists(result):
                with open(result, encoding="utf-8") as f:
                    t = f.read().strip()
                return "" if t.startswith("ERR:") else t
        except Exception:
            pass
        return ""

    def _read_top_right(self):
        """截屏并 OCR 右上角按钮区域，返回 OCR 文本"""
        if Image is None:
            return ""
        try:
            os.makedirs(self.tmp_dir, exist_ok=True)
            raw = os.path.join(self.tmp_dir, "screen.png")
            data = self._adb(["exec-out", "screencap", "-p"], timeout=15, binary=True)
            if len(data) < 1000:
                return ""
            im = Image.open(io.BytesIO(data)).convert("RGB")
            w, h = im.size
            crop = im.crop((int(w * 0.60), 0, w, int(h * 0.26)))
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            crop.save(raw)
            return self._ocr(raw)
        except Exception:
            return ""

    def _swipe(self):
        return self._adb(["shell", "input", "swipe"] + list(SWIPE) + [SWIPE_DURATION])

    def _loop(self):
        ad_start = None
        wait_since = None
        last_text = ""
        self._serial = self._get_serial()
        if not self._serial:
            self._emit("未发现设备：请先在投屏助手里连接手机")
        while not self._stop.is_set():
            if not self._serial:
                time.sleep(3)
                self._serial = self._get_serial()
                continue
            text = self._read_top_right()
            ad = is_ad_text(text)
            if not ad:
                if ad_start is not None:
                    self._emit("广告自然结束（未干预）", "空闲")
                    ad_start = None
                    wait_since = None
                time.sleep(POLL_SEC)
                continue

            if ad_start is None:
                ad_start = time.time()
                wait_since = None
                self._emit("检测到广告：" + (text or "(OCR 无文本)"), "发现广告")
                last_text = ""

            cd = countdown_of(text)
            if text != last_text:
                self._emit("按钮文字：%r  倒计时=%s" % (text, cd))
                last_text = text

            elapsed = time.time() - ad_start
            if cd is not None and cd > 0:
                if wait_since is None:
                    wait_since = time.time()
                # OCR 长时间读不出结果时兜底放行，避免卡死
                if time.time() - wait_since > MAX_WAIT_AFTER_HINT:
                    reason = "倒计时等待超时"
                elif elapsed > MAX_AD_SEC:
                    reason = "广告超过 %ds" % MAX_AD_SEC
                else:
                    self._stop.wait(COUNTDOWN_POLL)
                    continue
            else:
                reason = "倒计时结束" if cd == 0 else "无倒计时"

            self._emit("执行上滑（%s）" % reason, "上滑中")
            self._swipe()
            self._stop.wait(AFTER_SWIPE_SEC)
            # 复核：仍在广告则重试，最多 3 次
            retry = 0
            while retry < 3 and is_ad_text(self._read_top_right()):
                retry += 1
                self._emit("上滑后仍在广告，重试 %d/3" % retry)
                self._swipe()
                self._stop.wait(AFTER_SWIPE_SEC)
            if is_ad_text(self._read_top_right()):
                self._emit("多次上滑仍未跳过，暂停本条广告", "需人工接管")
                ad_start = None
                self._stop.wait(5)
            else:
                self._emit("已跳过广告，回到正片", "空闲")
                ad_start = None
                wait_since = None
                last_text = ""
                self._stop.wait(COOLDOWN_SEC)
