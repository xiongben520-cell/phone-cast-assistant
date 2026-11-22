# -*- coding: utf-8 -*-
"""
二维码定位 + 解码 (配合 adb pair --qr 的终端输出)

为什么需要它:
  adb pair --qr 会在终端里用 █ ▀ ▄ 画出一次性的配对二维码, 内容是
  "WIFI:T:ADB;S:<服务名>;P:<随机密码>;;"。服务名和密码每次运行都不同,
  无法自己伪造, 只能从 adb 的画里还原出来。

定位策略:
  adb 会在二维码外面再画一圈黑框, 所以边界不能靠"外圈是白"来判断。
  这里用穷举 + 解码验证: 遍历 (偏移, 尺寸) 组合, 交给 opencv 判定。
  尺寸只试合法 QR 版本 (21 + 4k), 偏移只试前 12 像素, 组合数很小,
  且只对通过定位块粗筛的候选做真正解码, 实测耗时 < 1 秒。
"""
import numpy as np
import cv2


def _locator_ok(m, r0, c0, sz):
    """粗筛: 三个定位图案的角必须为黑"""
    if r0 + sz > len(m) or c0 + sz > len(m[0]):
        return False
    return (m[r0][c0] == 1
            and m[r0][c0 + sz - 1] == 1
            and m[r0 + sz - 1][c0] == 1)


def _try_decode(m, top, left, sz, scale=8, quiet=4):
    core = np.array([[0 if m[top + r][left + c] else 255 for c in range(sz)]
                     for r in range(sz)], dtype=np.uint8)
    canvas = np.full((sz + 2 * quiet, sz + 2 * quiet), 255, dtype=np.uint8)
    canvas[quiet:quiet + sz, quiet:quiet + sz] = core
    big = np.repeat(np.repeat(canvas, scale, axis=0), scale, axis=1)
    det = cv2.QRCodeDetector()
    for im in (big, 255 - big):
        try:
            d, _, _ = det.detectAndDecode(cv2.cvtColor(im, cv2.COLOR_GRAY2BGR))
        except Exception:
            d = ""
        if d:
            return d
    return ""


def decode(rows):
    """rows: list[list[int]] 1=黑。返回解码字符串, 失败返回 ''"""
    if not rows:
        return ""
    m = rows
    h, w = len(m), len(m[0])
    sizes = [s for s in (21, 25, 29, 33, 37, 41, 45) if s <= h and s <= w]
    # 尺寸从大到小试: 真正二维码通常带外框, 尺寸偏大更容易命中
    for sz in reversed(sizes):
        for top in range(0, min(12, h - sz + 1)):
            for left in range(0, min(12, w - sz + 1)):
                if not _locator_ok(m, top, left, sz):
                    continue
                d = _try_decode(m, top, left, sz)
                if d:
                    return d
    return ""
