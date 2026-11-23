# -*- coding: utf-8 -*-
"""投屏窗口相关的 Win32 小工具（主程序与快捷键贴图共用）

只做四件事:
  1. 按进程名找 scrcpy 的顶层窗口
  2. 读窗口矩形 / 最小化 / 最大化状态
  3. 窗口置顶开关、按中心点重定位
  4. 把 scrcpy 快捷键发给投屏窗口（黑屏 / 亮屏）
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

_H = ctypes.c_void_p
EWP = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x0008

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
VK_SHIFT = 0x10
VK_MENU = 0x12
VK_O = 0x4F
KEYEVENTF_KEYUP = 0x0002

user32.IsWindowVisible.argtypes = [_H]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [_H]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [_H, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowRect.argtypes = [_H, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsIconic.argtypes = [_H]
user32.IsIconic.restype = wintypes.BOOL
user32.IsZoomed.argtypes = [_H]
user32.IsZoomed.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [_H, _H, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [_H, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = _H
user32.SetForegroundWindow.argtypes = [_H]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [_H, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_uint, ctypes.c_void_p]
user32.keybd_event.restype = None
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                 ctypes.c_wchar_p, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL


def proc_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return buf.value.split("\\")[-1].lower() if ok else ""


def find_window(exe_name="scrcpy.exe"):
    """按进程名找第一个可见的顶层窗口, 找不到返回 0"""
    want = exe_name.lower()
    found = []
    # 同一个进程会挂多个窗口, 按 pid 缓存进程名, 避免每个窗口都 OpenProcess 一次
    names = {}

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = names.get(pid.value)
        if name is None:
            name = proc_name(pid.value)
            names[pid.value] = name
        if name == want:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(EWP(cb), 0)
    return found[0] if found else 0


def rect(hwnd):
    r = wintypes.RECT()
    if not hwnd or not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return None
    return (r.left, r.top, r.right - r.left, r.bottom - r.top)


def is_minimized(hwnd):
    return bool(user32.IsIconic(hwnd)) if hwnd else False


def is_maximized(hwnd):
    return bool(user32.IsZoomed(hwnd)) if hwnd else False


def is_topmost(hwnd):
    if not hwnd:
        return False
    return bool(user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST)


def set_topmost(hwnd, on):
    if not hwnd:
        return False
    return bool(user32.SetWindowPos(
        hwnd, HWND_TOPMOST if on else HWND_NOTOPMOST,
        0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE))


def set_pos(hwnd, x, y):
    if not hwnd:
        return False
    return bool(user32.SetWindowPos(
        hwnd, 0, int(x), int(y), 0, 0,
        SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE))


def work_area():
    """返回 (left, top, right, bottom), 已扣掉任务栏"""
    ra = wintypes.RECT()
    # SPI_GETWORKAREA = 0x0030
    if user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(ra), 0):
        return (ra.left, ra.top, ra.right, ra.bottom)
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def _post_key(hwnd, vk, down, syskey=False):
    """不抢焦点的按键投递（备用方案, SDL 是否接受取决于版本）"""
    scan = user32.MapVirtualKeyW(vk, 0)
    msg = (WM_SYSKEYDOWN if down else WM_SYSKEYUP) if syskey \
        else (WM_KEYDOWN if down else WM_KEYUP)
    lp = (1 | (scan << 16)) if down else (1 | (scan << 16) | 0xC0000000)
    user32.PostMessageW(hwnd, msg, vk, lp)


def send_screen_key(screen_off):
    """向 scrcpy 窗口发送 MOD+O(黑屏) / MOD+Shift+O(亮屏)。

    scrcpy 的这两个快捷键走它自己的 setScreenPowerMode, 只关显示面板、
    不触发 keyguard, 正好满足"黑屏但继续投屏"和"亮屏能点密码"两个需求。

    优先用「临时抢焦点 + 真实键盘事件」(SDL 一定收得到), 发完立刻把焦点还回去。
    如果 SetForegroundWindow 被系统拒绝, 退回 PostMessage。
    """
    hwnd = find_window()
    if not hwnd:
        return False, "未找到投屏窗口"

    keys = ([VK_MENU, VK_O] if screen_off else [VK_MENU, VK_SHIFT, VK_O])
    prev = user32.GetForegroundWindow()
    got_focus = False
    try:
        got_focus = bool(user32.SetForegroundWindow(hwnd))
    except Exception:
        pass

    if got_focus:
        time.sleep(0.15)
        for vk in keys:
            user32.keybd_event(vk, 0, 0, None)
            time.sleep(0.03)
        for vk in reversed(keys):
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, None)
            time.sleep(0.03)
        time.sleep(0.15)
        if prev and prev != hwnd:
            try:
                user32.SetForegroundWindow(prev)
            except Exception:
                pass
        return True, "已发送「%s」快捷键" % ("熄屏(仍投屏)" if screen_off else "点亮屏幕")
    else:
        for vk in keys:
            _post_key(hwnd, vk, True, syskey=(vk == VK_MENU))
        for vk in reversed(keys):
            _post_key(hwnd, vk, False, syskey=(vk == VK_MENU))
        return True, "已投递「%s」快捷键（无焦点模式）" % (
            "熄屏(仍投屏)" if screen_off else "点亮屏幕")
