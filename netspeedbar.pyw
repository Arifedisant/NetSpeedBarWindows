"""Net Speed Bar — upload / download speed in the Windows 11 taskbar.

Sister of AIUsageBar (~/AIUsageBar): same look, same tricks. A borderless
always-on-top Tk window whose pixels are drawn by Pillow; the background is
the taskbar colour sampled just above the window, so it reads as transparent
while every pixel stays clickable (a real transparent key would make empty
pixels click-through, and the widget could no longer be hovered or dragged).

    pythonw.exe C:\\Users\\<you>\\NetSpeedBar\\netspeedbar.pyw

Click = detail panel (60 s graph, peaks, today's totals). Drag = move.
Right-click = units, opacity, autostart, exit.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import collections
import datetime
import json
import os
import sys
import time
import tkinter as tk
import webbrowser
from pathlib import Path

import psutil
from PIL import Image, ImageDraw, ImageFont, ImageTk

APP_NAME = "NetSpeedBar"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
CONFIG_PATH = DATA_DIR / "config.json"
AIUSAGE_CONFIG = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "AIUsageBar" / "config.json"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

TICK_MS = 1000              # speed sample interval
TOPMOST_MS = 700            # z-order check
RELAYOUT_MS = 30000         # dock re-measure
SAVE_EVERY = 60             # seconds between writes of today's totals
PANEL_CLOSE_MS = 1200
HISTORY = 60                # seconds of graph

# Tunnels and virtual switches carry traffic that also crosses the physical
# adapter; counting them too would double the figure (ZeroTier is on this PC).
SKIP_WORDS = ("loopback", "zerotier", "vethernet", "virtualbox", "vmware",
              "hyper-v", "bluetooth", "local area connection*", "npcap",
              "tap-", "wireguard", "tailscale", "isatap", "teredo", "pseudo")

# -- drawing ----------------------------------------------------------------

KEY = (1, 2, 3)
KEY_HEX = "#010203"
SS = 3

STRIP_W = 80
STRIP_H = 32                # same box as the Claude widget

PANEL_BG = (21, 27, 38)
PANEL_EDGE = (39, 49, 66)
TEXT = (240, 244, 249)
MUTED = (150, 165, 184)
DIM = (98, 112, 130)
FAINT = (74, 86, 102)
GRID = (32, 41, 55)

DOWN = (77, 208, 155)       # green
UP = (245, 173, 66)         # amber
OFFLINE = (86, 98, 114)

FONT_FILES = {
    "regular": "C:/Windows/Fonts/segoeui.ttf",
    "semibold": "C:/Windows/Fonts/seguisb.ttf",
    "bold": "C:/Windows/Fonts/segoeuib.ttf",
    "symbol": "C:/Windows/Fonts/seguisym.ttf",
}
_fonts: dict = {}


def font(weight, size):
    key = (weight, round(size))
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(FONT_FILES[weight], round(size))
        except OSError:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Canvas:
    """Shapes on a 3x layer (smooth at any DPI), text afterwards at final size."""

    def __init__(self, width, height, scale, bg=KEY):
        self.width = round(width * scale)
        self.height = round(height * scale)
        self.k = scale * SS
        self.layer = Image.new("RGB", (self.width * SS, self.height * SS), bg)
        self.shapes = ImageDraw.Draw(self.layer)

    def rounded(self, box, radius, fill=None, outline=None, thickness=1):
        x0, y0, x1, y1 = (round(v * self.k) for v in box)
        self.shapes.rounded_rectangle((x0, y0, x1 - 1, y1 - 1),
                                      radius=round(radius * self.k), fill=fill,
                                      outline=outline,
                                      width=max(1, round(thickness * self.k)))

    def line(self, box, fill):
        x0, y0, x1, y1 = (round(v * self.k) for v in box)
        self.shapes.rectangle((x0, y0, max(x0, x1 - 1), max(y0, y1 - 1)), fill=fill)

    def circle(self, cx, cy, r, fill):
        cx, cy, r = cx * self.k, cy * self.k, r * self.k
        self.shapes.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)

    def graph(self, x, y, w, h, values, peak, colour, fill_area, bg):
        """Area (fill_area) or line chart of `values`, oldest on the left."""
        if not values:
            return
        k = self.k
        step = w / max(1, HISTORY - 1)
        start = x + w - step * (len(values) - 1)
        pts = [((start + i * step) * k,
                (y + h - h * min(1.0, v / peak if peak else 0)) * k)
               for i, v in enumerate(values)]
        if fill_area:
            area = [(pts[0][0], (y + h) * k)] + pts + [(pts[-1][0], (y + h) * k)]
            self.shapes.polygon(area, fill=mix(bg, colour, 0.28))
        if len(pts) > 1:
            self.shapes.line(pts, fill=colour, width=max(1, round(1.4 * k)),
                             joint="curve")

    def finish(self):
        self.image = self.layer.resize((self.width, self.height), Image.LANCZOS)
        return ImageDraw.Draw(self.image)


# -- numbers ----------------------------------------------------------------

def speed_text(bytes_per_s, bits):
    if bits:
        value, units, base = bytes_per_s * 8, ("b/s", "Kb/s", "Mb/s", "Gb/s"), 1000.0
    else:
        value, units, base = bytes_per_s, ("B/s", "KB/s", "MB/s", "GB/s"), 1024.0
    i = 0
    while value >= base * 0.995 and i < len(units) - 1:   # 999.6 -> "1.00 M"
        value /= base
        i += 1
    if i == 0:
        # Below 1 K the bar would flicker between "0 b/s" and "312 b/s";
        # a steady "0.3 Kb/s" is calmer to look at.
        value /= base
        i = 1
    return ("%.1f" if value < 100 else "%.0f") % value, units[i]


def size_text(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.0f %s" if unit in ("B", "KB") else "%.2f %s") % (n, unit)
        n /= 1024.0


# -- Windows plumbing (same as AIUsageBar) -----------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.FindWindowW.restype = wintypes.HWND
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
ctypes.windll.gdi32.GetPixel.restype = wintypes.DWORD
ctypes.windll.gdi32.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT]
kernel32.CreateMutexW.restype = wintypes.HANDLE

HWND_TOP, HWND_TOPMOST = 0, -1
SWP_FLAGS = 0x0002 | 0x0001 | 0x0010        # NOMOVE | NOSIZE | NOACTIVATE
GWL_EXSTYLE, WS_EX_TOOLWINDOW, GA_ROOT = -20, 0x80, 2


def make_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def already_running():
    kernel32.CreateMutexW(None, False, "Local\\NetSpeedBar-single")
    return kernel32.GetLastError() == 183       # ERROR_ALREADY_EXISTS


def taskbar_rect():
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def display_scale():
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    dpi = 0
    for probe in (lambda: user32.GetDpiForWindow(hwnd) if hwnd else 0,
                  lambda: user32.GetDpiForSystem()):
        try:
            dpi = probe()
        except Exception:
            dpi = 0
        if dpi:
            break
    return (dpi or 96) / 96.0


def window_pid(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_taskbar(hwnd):
    """On Win11 the bar painting over us is a CoreWindow owned by explorer's
    taskbar process, so ownership (not class name) is the test."""
    try:
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
        tray = user32.FindWindowW("Shell_TrayWnd", None)
        return bool(tray) and window_pid(hwnd) == window_pid(tray) != 0
    except Exception:
        return False


def covering_window(hwnd, rect):
    try:
        point = wintypes.POINT((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        top = user32.WindowFromPoint(point)
        if not top or top == hwnd:
            return None
        return user32.GetAncestor(top, GA_ROOT) or top
    except Exception:
        return None


def pythonw():
    exe = Path(sys.executable)
    candidate = exe.with_name("pythonw.exe")
    return str(candidate if candidate.exists() else exe)


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, '"%s" "%s"' % (
                pythonw(), APP_DIR / "netspeedbar.pyw"))
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except OSError:
                pass


# -- config -----------------------------------------------------------------

DEFAULT_CONFIG = {"x": None, "y": None, "opacity": 100, "bits": True,
                  "today": {"date": "", "down": 0, "up": 0}}


def load_config():
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            config.update(json.load(fh))
    except (OSError, ValueError):
        pass
    return config


def save_config(config):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except OSError:
        pass


# -- the meter --------------------------------------------------------------

class Meter:
    """Bytes/s over the physical adapters, from psutil's per-NIC counters."""

    def __init__(self):
        self.prev = None
        self.prev_t = None
        self.down = self.up = 0.0
        self.names: list = []
        self.hist_down = collections.deque(maxlen=HISTORY)
        self.hist_up = collections.deque(maxlen=HISTORY)
        self.session_down = self.session_up = 0

    @staticmethod
    def _wanted(name, stats):
        low = name.lower()
        if any(word in low for word in SKIP_WORDS):
            return False
        st = stats.get(name)
        return st is None or st.isup

    def sample(self):
        """Returns (delta_down, delta_up) in bytes since the last sample."""
        now = time.monotonic()
        try:
            counters = psutil.net_io_counters(pernic=True)
            stats = psutil.net_if_stats()
        except Exception:
            return 0, 0
        current = {n: (c.bytes_recv, c.bytes_sent) for n, c in counters.items()
                   if self._wanted(n, stats)}
        self.names = sorted(n for n, (r, s) in current.items() if r or s)

        d_down = d_up = 0
        if self.prev is not None:
            for name, (recv, sent) in current.items():
                old = self.prev.get(name)
                if not old:
                    continue
                # A reconnect resets an adapter's counters; a negative delta
                # is that reset, not traffic.
                d_down += max(0, recv - old[0])
                d_up += max(0, sent - old[1])
            dt = max(0.2, now - self.prev_t)
            # Sleep/resume leaves a huge gap; spreading it over one tick
            # would show an absurd spike.
            if now - self.prev_t > 10:
                d_down = d_up = 0
            self.down, self.up = d_down / dt, d_up / dt
            self.hist_down.append(self.down)
            self.hist_up.append(self.up)
            self.session_down += d_down
            self.session_up += d_up
        self.prev, self.prev_t = current, now
        return d_down, d_up


# -- the widget -------------------------------------------------------------

class NetSpeedBar:

    def __init__(self):
        self.config = load_config()
        self.scale = display_scale()
        self.meter = Meter()
        self.bg = None
        self.hover = False
        self.panel_open = False
        self._panel_close_job = None
        self._drag = None
        self._last_save = time.time()
        self.panel_hitboxes = []

        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("Net Speed Bar")
        self.root.overrideredirect(True)
        self.root.configure(bg=KEY_HEX)
        self.root.attributes("-topmost", True)
        self.root.wm_attributes("-transparentcolor", KEY_HEX)
        self.label = tk.Label(self.root, bd=0, highlightthickness=0, bg=KEY_HEX)
        self.label.pack()

        self.panel = tk.Toplevel(self.root)
        self.panel.withdraw()
        self.panel.overrideredirect(True)
        self.panel.configure(bg=KEY_HEX)
        self.panel.attributes("-topmost", True)
        self.panel.wm_attributes("-transparentcolor", KEY_HEX)
        self.panel_label = tk.Label(self.panel, bd=0, highlightthickness=0,
                                    bg=KEY_HEX)
        self.panel_label.pack()

        self._build_menu()
        self._bind()

        self.root.deiconify()
        self.root.update_idletasks()
        for window in (self.root, self.panel):
            hwnd = self._hwnd(window)
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        self._apply_opacity()
        self._place()

        self.meter.sample()
        self._draw_strip()
        self.root.after(TICK_MS, self._tick)
        self.root.after(TOPMOST_MS, self._keep_on_top)
        # At sign-in this can be up before the taskbar is.
        for delay in (800, 2000, 5000, 12000, 25000):
            self.root.after(delay, self._relayout)
        self.root.after(RELAYOUT_MS, self._relayout_loop)

    # -- geometry -----------------------------------------------------------

    def _hwnd(self, window):
        inner = ctypes.c_void_p(window.winfo_id()).value
        return user32.GetAncestor(inner, GA_ROOT) or inner

    def _size(self):
        return round(STRIP_W * self.scale), round(STRIP_H * self.scale)

    def _clamp(self, x, y, w, h):
        return (max(0, min(x, self.root.winfo_screenwidth() - w)),
                max(0, min(y, self.root.winfo_screenheight() - h)))

    def _default_position(self):
        """Just left of the Claude usage widget, else before the tray."""
        w, h = self._size()
        rect = taskbar_rect()
        if rect and 24 < rect[3] - rect[1] < 200:
            y = rect[1] + (rect[3] - rect[1] - h) // 2
            # Default spot (chosen 17 Oct 2026): 426 logical px (639 px at
            # 150 %) left of the taskbar's right edge.
            x = rect[2] - round(426 * self.scale)
            try:
                with open(AIUSAGE_CONFIG, "r", encoding="utf-8") as fh:
                    other_x = json.load(fh).get("x")
                if other_x is not None:
                    # 85.33 = this strip (STRIP_W 80) + the 8 px gap at 150 %
                    x = int(other_x) - round(85.33 * self.scale)
            except (OSError, ValueError, TypeError):
                pass
        else:
            x = self.root.winfo_screenwidth() - round(426 * self.scale)
            y = self.root.winfo_screenheight() - h - round(8 * self.scale)
        return self._clamp(x, y, w, h)

    def _place(self):
        w, h = self._size()
        if self.config.get("x") is None or self.config.get("y") is None:
            x, y = self._default_position()
        else:
            x, y = self._clamp(int(self.config["x"]), int(self.config["y"]), w, h)
            rect = taskbar_rect()
            if rect and rect[1] - h <= y <= rect[3]:
                y = rect[1] + (rect[3] - rect[1] - h) // 2   # snap to centre
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _relayout(self):
        scale = display_scale()
        if abs(scale - self.scale) > 0.01:
            self.scale = scale
            self._place()
            self._draw_strip()
        elif self.config.get("x") is None:
            self._place()

    def _relayout_loop(self):
        self._relayout()
        self.root.after(RELAYOUT_MS, self._relayout_loop)

    def _apply_opacity(self):
        alpha = max(35, min(100, int(self.config.get("opacity", 100)))) / 100.0
        self.root.attributes("-alpha", alpha)
        self.panel.attributes("-alpha", alpha)

    def _sample_bg(self):
        rect = taskbar_rect()
        if not rect:
            return None
        x0, w = self.root.winfo_rootx(), self.root.winfo_width()
        y = max(rect[1] + 2, self.root.winfo_rooty() - 4)
        if y >= self.root.winfo_rooty():
            return self.bg
        dc = user32.GetDC(0)
        try:
            px = [ctypes.windll.gdi32.GetPixel(dc, x, y)
                  for x in (x0 + 4, x0 + w // 2, x0 + w - 4)]
        finally:
            user32.ReleaseDC(0, dc)
        px = [p for p in px if p != 0xFFFFFFFF]
        if not px:
            return self.bg
        rgb = sorted((p & 0xFF, (p >> 8) & 0xFF, (p >> 16) & 0xFF) for p in px)
        return rgb[len(rgb) // 2]

    # -- drawing ------------------------------------------------------------

    def _draw_strip(self):
        s = self.scale
        self.bg = self._sample_bg() or self.bg or (32, 32, 32)
        canvas = Canvas(STRIP_W, STRIP_H, s, bg=self.bg)
        if self.hover or self.panel_open:
            canvas.rounded((0, 0, STRIP_W, STRIP_H), 6,
                           fill=mix(self.bg, (255, 255, 255), 0.07))
        draw = canvas.finish()

        online = bool(self.meter.names)
        bits = self.config.get("bits", True)
        f_arrow = font("bold", 12 * s)
        f_num = font("semibold", 11.5 * s)
        f_unit = font("regular", 9 * s)
        right = (STRIP_W - 5) * s
        unit_w = max(draw.textlength(u, font=f_unit)
                     for u in (("Kb/s", "Mb/s") if bits else ("KB/s", "MB/s")))

        for value, arrow, colour, cy in ((self.meter.up, "↑", UP, 9.5),
                                         (self.meter.down, "↓", DOWN, 23.5)):
            y = cy * s
            if online:
                number, unit = speed_text(value, bits)
            else:
                number, unit = "—", ""
            draw.text((4 * s, y), arrow, font=f_arrow,
                      fill=colour if online else OFFLINE, anchor="lm")
            draw.text((right, y), unit, font=f_unit, fill=MUTED, anchor="rm")
            draw.text((right - unit_w - 3 * s, y), number, font=f_num,
                      fill=TEXT if online else OFFLINE, anchor="rm")

        self._strip_photo = ImageTk.PhotoImage(canvas.image)
        self.label.configure(image=self._strip_photo)

    def _draw_panel(self):
        s = self.scale
        W, pad = 300, 16
        graph_h = 64
        H = pad + 18 + 12 + 46 + 10 + graph_h + 8 + 16 + 3 * 18 + 10 + 28 + pad
        canvas = Canvas(W, H, s)
        bits = self.config.get("bits", True)
        m = self.meter
        online = bool(m.names)

        canvas.rounded((0, 0, W, H), 14, fill=PANEL_BG)
        canvas.rounded((0, 0, W, H), 14, outline=PANEL_EDGE, thickness=1)
        canvas.line((14, 0, W - 14, 1), (58, 71, 94))
        canvas.circle(pad + 4, pad + 8, 4, DOWN if online else OFFLINE)

        y = pad + 18 + 12
        big_y = y
        y += 46 + 10
        graph_y = y
        peak = max(list(m.hist_down) + list(m.hist_up) + [1.0])
        for frac in (0.0, 0.5, 1.0):
            gy = graph_y + graph_h * frac
            canvas.line((pad, gy, W - pad, gy + 0.6), GRID)
        canvas.graph(pad, graph_y, W - pad * 2, graph_h, list(m.hist_down),
                     peak * 1.1, DOWN, True, PANEL_BG)
        canvas.graph(pad, graph_y, W - pad * 2, graph_h, list(m.hist_up),
                     peak * 1.1, UP, False, PANEL_BG)
        y += graph_h + 8
        caption_y = y
        y += 16
        rows_y = y
        y += 3 * 18 + 10

        buttons = [("speedtest", "Tes kecepatan"), ("settings", "Pengaturan Wi-Fi")]
        bw = (W - pad * 2 - 8) / 2
        self.panel_hitboxes = []
        laid = []
        for i, (name, label) in enumerate(buttons):
            bx = pad + i * (bw + 8)
            box = (bx, y, bx + bw, y + 28)
            canvas.rounded(box, 8, fill=(28, 36, 48))
            canvas.rounded(box, 8, outline=(48, 60, 78), thickness=1)
            self.panel_hitboxes.append((name, round(box[0] * s), round(box[1] * s),
                                        round(box[2] * s), round(box[3] * s)))
            laid.append((label, box))

        draw = canvas.finish()
        draw.text(((pad + 14) * s, (pad + 8) * s), "Kecepatan Internet",
                  font=font("semibold", 13 * s), fill=TEXT, anchor="lm")
        adapter = ", ".join(m.names) if online else "tidak tersambung"
        draw.text(((W - pad) * s, (pad + 8) * s), adapter.upper()[:22],
                  font=font("semibold", 9 * s),
                  fill=DOWN if online else OFFLINE, anchor="rm")

        half = (W - pad * 2) / 2
        for i, (label, value, colour) in enumerate(
                (("↓ Unduh", m.down, DOWN), ("↑ Unggah", m.up, UP))):
            x = (pad + i * half) * s
            draw.text((x, (big_y + 8) * s), label, font=font("symbol", 10 * s),
                      fill=MUTED, anchor="lm")
            number, unit = speed_text(value, bits)
            draw.text((x, (big_y + 34) * s), number, font=font("bold", 22 * s),
                      fill=colour, anchor="ls")
            nx = x + draw.textlength(number, font=font("bold", 22 * s)) + 4 * s
            draw.text((nx, (big_y + 34) * s), unit, font=font("semibold", 10 * s),
                      fill=MUTED, anchor="ls")

        draw.text((pad * s, caption_y * s), "%d detik terakhir · puncak %s" % (
            HISTORY, " ".join(speed_text(peak, bits))),
            font=font("regular", 9.5 * s), fill=DIM, anchor="lm")

        today = self.config.get("today") or {}
        rows = (("Hari ini", today.get("down", 0), today.get("up", 0)),
                ("Sejak widget jalan", m.session_down, m.session_up))
        f_row = font("regular", 10 * s)
        f_val = font("semibold", 10 * s)
        for i, (label, d, u) in enumerate(rows):
            ry = (rows_y + 4 + i * 18) * s
            draw.text((pad * s, ry), label, font=f_row, fill=MUTED, anchor="lm")
            draw.text(((W - pad) * s, ry), "↓ %s   ↑ %s" % (size_text(d), size_text(u)),
                      font=font("symbol", 10 * s), fill=TEXT, anchor="rm")
        draw.text((pad * s, (rows_y + 4 + 2 * 18) * s),
                  "klik kanan bar: satuan bit/byte, transparansi, autostart",
                  font=font("regular", 9 * s), fill=FAINT, anchor="lm")

        for label, box in laid:
            draw.text((((box[0] + box[2]) / 2) * s, ((box[1] + box[3]) / 2) * s),
                      label, font=font("semibold", 10.5 * s), fill=TEXT, anchor="mm")

        img = canvas.image
        self._panel_photo = ImageTk.PhotoImage(img)
        self.panel_label.configure(image=self._panel_photo)
        x = max(4, min(self.root.winfo_rootx() + self.root.winfo_width() // 2
                       - img.width // 2,
                       self.root.winfo_screenwidth() - img.width - 4))
        y = self.root.winfo_rooty() - img.height - round(8 * s)
        if y < 4:
            y = self.root.winfo_rooty() + self.root.winfo_height() + 8
        self.panel.geometry("%dx%d+%d+%d" % (img.width, img.height, x, y))

    # -- loop ---------------------------------------------------------------

    def _tick(self):
        d_down, d_up = self.meter.sample()
        stamp = datetime.date.today().isoformat()
        today = self.config.setdefault("today", {"date": stamp, "down": 0, "up": 0})
        if today.get("date") != stamp:
            today.update(date=stamp, down=0, up=0)
        today["down"] = today.get("down", 0) + d_down
        today["up"] = today.get("up", 0) + d_up
        if time.time() - self._last_save > SAVE_EVERY:
            self._save()
        self._draw_strip()
        if self.panel_open:
            self._draw_panel()
        self.root.after(TICK_MS, self._tick)

    def _save(self):
        self._last_save = time.time()
        save_config(self.config)

    def _keep_on_top(self):
        hwnd = self._hwnd(self.root)
        rect = (self.root.winfo_rootx(), self.root.winfo_rooty(),
                self.root.winfo_rootx() + self.root.winfo_width(),
                self.root.winfo_rooty() + self.root.winfo_height())
        covered = covering_window(hwnd, rect)
        # Only fight the taskbar; Start menu and flyouts may cover us.
        if covered is not None and is_taskbar(covered):
            user32.SetWindowPos(hwnd, HWND_TOP, 0, 0, 0, 0, SWP_FLAGS)
            if self.panel_open:
                user32.SetWindowPos(self._hwnd(self.panel), HWND_TOP, 0, 0, 0, 0,
                                    SWP_FLAGS)
        self.root.after(TOPMOST_MS, self._keep_on_top)

    # -- interaction --------------------------------------------------------

    def _bind(self):
        self.label.bind("<Button-1>", self._on_press)
        self.label.bind("<B1-Motion>", self._on_drag)
        self.label.bind("<ButtonRelease-1>", self._on_release)
        self.label.bind("<Enter>", self._on_enter)
        self.label.bind("<Leave>", self._on_leave)
        self.label.bind("<Button-3>", self._on_menu)
        self.panel_label.bind("<Button-1>", self._on_panel_click)
        self.panel_label.bind("<Enter>", lambda _e: self._cancel_close())
        self.panel_label.bind("<Leave>", self._on_leave)

    def _on_enter(self, _e):
        self.hover = True
        self._cancel_close()
        self._draw_strip()

    def _on_leave(self, _e=None):
        if self.hover:
            self.hover = False
            self._draw_strip()
        if self.panel_open:
            self._cancel_close()
            self._panel_close_job = self.root.after(PANEL_CLOSE_MS, self._hide_panel)

    def _cancel_close(self):
        if self._panel_close_job is not None:
            try:
                self.root.after_cancel(self._panel_close_job)
            except Exception:
                pass
            self._panel_close_job = None

    def _on_press(self, e):
        self._drag = {"x": e.x_root, "y": e.y_root, "ox": self.root.winfo_x(),
                      "oy": self.root.winfo_y(), "moved": False}

    def _on_drag(self, e):
        if not self._drag:
            return
        dx, dy = e.x_root - self._drag["x"], e.y_root - self._drag["y"]
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag["moved"] = True
            self._hide_panel()
        if self._drag["moved"]:
            x, y = self._clamp(self._drag["ox"] + dx, self._drag["oy"] + dy,
                               self.root.winfo_width(), self.root.winfo_height())
            self.root.geometry("+%d+%d" % (x, y))

    def _on_release(self, _e):
        drag, self._drag = self._drag, None
        if not drag:
            return
        if drag["moved"]:
            self.config["x"], self.config["y"] = self.root.winfo_x(), self.root.winfo_y()
            self._save()
        elif self.panel_open:
            self._hide_panel()
        else:
            self.panel_open = True
            self._draw_panel()
            self.panel.deiconify()
            user32.SetWindowPos(self._hwnd(self.panel), HWND_TOP, 0, 0, 0, 0,
                                SWP_FLAGS)

    def _on_panel_click(self, e):
        for name, x0, y0, x1, y1 in self.panel_hitboxes:
            if x0 <= e.x < x1 and y0 <= e.y < y1:
                self._hide_panel()
                if name == "speedtest":
                    webbrowser.open("https://fast.com")
                elif name == "settings":
                    os.startfile("ms-settings:network-wifi")
                return

    def _hide_panel(self):
        self._cancel_close()
        if self.panel_open:
            self.panel_open = False
            self.panel.withdraw()
            self._draw_strip()

    # -- menu ---------------------------------------------------------------

    def _build_menu(self):
        style = {"tearoff": 0, "bg": "#171d28", "fg": "#e8edf4",
                 "activebackground": "#27324a", "activeforeground": "#ffffff",
                 "bd": 0, "relief": "flat"}
        self.menu = tk.Menu(self.root, **style)

        self.bits_var = tk.BooleanVar(value=bool(self.config.get("bits", True)))
        units = tk.Menu(self.menu, **style)
        units.add_radiobutton(label="bit/s  (Kb/s, Mb/s — seperti iklan ISP)",
                              variable=self.bits_var, value=True,
                              command=self._set_units, selectcolor="#4c8df6")
        units.add_radiobutton(label="byte/s  (KB/s, MB/s — seperti download)",
                              variable=self.bits_var, value=False,
                              command=self._set_units, selectcolor="#4c8df6")
        self.menu.add_cascade(label="Satuan", menu=units)

        opacity = tk.Menu(self.menu, **style)
        for value in (100, 90, 80, 70, 55):
            opacity.add_command(label="%d%%" % value,
                                command=lambda v=value: self._set_opacity(v))
        self.menu.add_cascade(label="Transparansi", menu=opacity)
        self.menu.add_command(label="Kembalikan ke posisi awal",
                              command=self._reset_position)
        self.menu.add_separator()
        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        self.menu.add_checkbutton(label="Jalan saat Windows menyala",
                                  variable=self.autostart_var,
                                  command=self._toggle_autostart,
                                  selectcolor="#4c8df6")
        self.menu.add_separator()
        self.menu.add_command(label="Keluar", command=self.quit)

    def _on_menu(self, e):
        self._hide_panel()
        try:
            self.menu.tk_popup(e.x_root, e.y_root)
        finally:
            self.menu.grab_release()

    def _set_units(self):
        self.config["bits"] = bool(self.bits_var.get())
        self._save()
        self._draw_strip()

    def _set_opacity(self, value):
        self.config["opacity"] = value
        self._save()
        self._apply_opacity()

    def _reset_position(self):
        self.config["x"] = self.config["y"] = None
        self._save()
        self._place()

    def _toggle_autostart(self):
        wanted = bool(self.autostart_var.get())
        try:
            set_autostart(wanted)
        except OSError:
            self.autostart_var.set(not wanted)

    def quit(self):
        self._save()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


def main():
    if already_running():
        return
    make_dpi_aware()
    NetSpeedBar().run()


if __name__ == "__main__":
    main()
