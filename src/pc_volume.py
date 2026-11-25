# -*- coding: utf-8 -*-
"""投屏窗口（scrcpy.exe）音量控制 —— 只改这个进程的音量，不动系统主音量。

用 pycaw（comtypes）枚举 Windows 音频会话，按 PID 找到 scrcpy 的
ISimpleAudioVolume。手机媒体音量 0..150 映射到该会话音量 0.0..1.0。

依赖：pycaw + comtypes（均已加入 build.ps1 的 --collect-all）。

重要：COM 必须在调用线程里初始化。同步跑在子线程，所以每个线程都要 CoInitialize。
"""
import ctypes

try:
    from pycaw.pycaw import AudioUtilities
except Exception:
    AudioUtilities = None


def init_com():
    """在本线程初始化 COM（子线程必须调用，否则 GetAllSessions 返回空/设置失败）"""
    try:
        ctypes.windll.ole32.CoInitialize(None)
    except Exception:
        pass


class ScrcpyVolume:
    def __init__(self, pid):
        self.pid = int(pid)
        self._sav = None

    def _ensure(self):
        """找到 scrcpy 音频会话；会话可能晚于进程创建，所以每次都尝试刷新。"""
        if self._sav is not None:
            try:
                self._sav.GetMasterVolume()
                return True
            except Exception:
                self._sav = None
        if AudioUtilities is None:
            return False
        try:
            for s in AudioUtilities.GetAllSessions():
                if s.Process and s.Process.pid == self.pid:
                    self._sav = s.SimpleAudioVolume
                    return True
        except Exception:
            pass
        return False

    @property
    def ok(self):
        return self._ensure()

    def get(self):
        """投屏窗口音量 0.0..1.0，失败返回 None"""
        if not self._ensure():
            return None
        try:
            return self._sav.GetMasterVolume()
        except Exception:
            return None

    def set(self, level):
        """设置投屏窗口音量 0.0..1.0，返回是否成功"""
        if not self._ensure():
            return False
        try:
            level = max(0.0, min(1.0, float(level)))
            self._sav.SetMasterVolume(level, None)
            return True
        except Exception:
            return False


if __name__ == "__main__":
    import sys
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    v = ScrcpyVolume(pid)
    print("ok=", v.ok)
    if v.ok:
        print("before=", v.get())
        v.set(0.5)
        print("after=", v.get())
