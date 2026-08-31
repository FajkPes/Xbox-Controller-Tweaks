"""
Xbox Controller Tweaks v7
=========================
Python packages: vgamepad, pygame-ce, customtkinter, pystray, pillow
System driver: ViGEmBus; HidHide is optional for preventing double input.
"""

import customtkinter as ctk
from tkinter import messagebox, filedialog
import tkinter as tk
import threading, time, sys, json, copy, ctypes, os
from datetime import datetime
from dataclasses import dataclass, field

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False

try:
    import vgamepad as vg
except ImportError:
    print("ERROR: run Setup.bat to install vgamepad"); sys.exit(1)
except Exception as exc:
    print(f"ERROR: ViGEmBus is unavailable or not working: {exc}"); sys.exit(1)

try:
    import pygame
    import pygame.joystick
except ImportError:
    print("ERROR: run Setup.bat to install pygame-ce"); sys.exit(1)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ─── Konstanty ────────────────────────────────────────────────────────────────

LINEAR_CURVE  = [[0.0, 0.0], [1.0, 1.0]]
MULT_MIN, MULT_MAX = 0.1, 3.0
PROFILE_SLOTS = ["Slot 1", "Slot 2", "Slot 3", "Slot 4"]
PROFILES_FILE     = os.path.join(os.path.expanduser("~"), ".xbox_remapper_profiles.json")
APP_SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".xbox_remapper_settings.json")

_LUT_SIZE          = 256
_LOG_AXIS_INTERVAL = 0.5   # throttle axis logů [s]

# Tolerance pro passthrough detekci
_PT_DZ_MAX   = 0.001
_PT_MULT_EPS = 0.001

# pygame axis indexy pro Xbox controller (standardní mapping)
_PG_AXIS_LX  = 0
_PG_AXIS_LY  = 1
_PG_AXIS_RX  = 2
_PG_AXIS_RY  = 3
_PG_AXIS_LT  = 4   # Windows/XInput: separátní osa, -1=uvolněno, +1=stisknuto
_PG_AXIS_RT  = 5

# pygame button indexy (Xbox layout)
_PG_BTN_MAP = {
    0:  vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    1:  vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    2:  vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    3:  vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
    4:  vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    5:  vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    6:  vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    7:  vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    8:  vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    9:  vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    10: vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
}
_PG_BTN_LABEL = {
    0:"A", 1:"B", 2:"X", 3:"Y",
    4:"LB", 5:"RB", 6:"Back", 7:"Start",
    8:"L3", 9:"R3", 10:"Guide",
}
_BTN_CODE_TO_LABEL = {
    "btn_0":"A","btn_1":"B","btn_2":"X","btn_3":"Y",
    "btn_4":"LB","btn_5":"RB","btn_6":"Back","btn_7":"Start",
    "btn_8":"L3","btn_9":"R3",
    "lt_pressed":"LT","rt_pressed":"RT",
}

# ─── pygame inicializace ──────────────────────────────────────────────────────

def _pg_init():
    if not pygame.get_init():
        pygame.init()
    if not pygame.joystick.get_init():
        pygame.joystick.init()

_pg_init()

# ─── XInput rumble ────────────────────────────────────────────────────────────

class _XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [("wLeftMotorSpeed", ctypes.c_ushort), ("wRightMotorSpeed", ctypes.c_ushort)]

def _find_xinput_index(preferred: int = 0) -> int:
    if _XINPUT is None: return preferred
    try:
        class _S(ctypes.Structure):
            _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", ctypes.c_byte * 16)]
        for i in range(4):
            s = _S()
            if _XINPUT.XInputGetState(i, ctypes.byref(s)) == 0: return i
    except Exception: pass
    return preferred

def _load_xinput():
    for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            lib = ctypes.windll.LoadLibrary(dll)
            lib.XInputSetState.argtypes = [ctypes.c_uint, ctypes.POINTER(_XINPUT_VIBRATION)]
            lib.XInputSetState.restype  = ctypes.c_uint
            lib.XInputGetState.argtypes = [ctypes.c_uint, ctypes.c_void_p]
            lib.XInputGetState.restype  = ctypes.c_uint
            return lib
        except OSError: continue
    return None

_XINPUT = _load_xinput()

# ─── Křivka + LUT ────────────────────────────────────────────────────────────

def eval_curve(points: list, x: float) -> float:
    if x <= points[0][0]:  return points[0][1]
    if x >= points[-1][0]: return points[-1][1]
    for i in range(len(points) - 1):
        x0, y0 = points[i]; x1, y1 = points[i + 1]
        if x0 <= x <= x1:
            t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + t * (y1 - y0)
    return x

def build_lut(points: list, mode: str, mult: float, n: int = _LUT_SIZE) -> tuple:
    pts = sorted(points, key=lambda p: p[0])
    lut = []
    for i in range(n):
        x = i / (n - 1)
        lut.append(eval_curve(pts, x) if mode == "curve" else min(1.0, x * mult))
    return tuple(lut)

def lut_lookup(lut: tuple, x: float) -> float:
    idx = int(x * (_LUT_SIZE - 1))
    if idx <= 0:             return lut[0]
    if idx >= _LUT_SIZE - 1: return lut[-1]
    return lut[idx]

def _is_passthrough(mode: str, mult: float, dz: float, invert: bool = False) -> bool:
    return (
        mode != "curve" and
        abs(mult - 1.0) < _PT_MULT_EPS and
        dz < _PT_DZ_MAX and
        not invert
    )

def _is_trigger_passthrough(mode: str, mult: float, dz: float) -> bool:
    return mode != "curve" and abs(mult - 1.0) < _PT_MULT_EPS and dz < _PT_DZ_MAX

# ─── Config dataclass ─────────────────────────────────────────────────────────

@dataclass
class ConfigSnapshot:
    left_x_mult:      float = 1.0
    left_y_mult:      float = 1.0
    right_x_mult:     float = 1.0
    right_y_mult:     float = 1.0
    trigger_mult:     float = 1.0
    left_deadzone:    float = 0.02
    right_deadzone:   float = 0.02
    trigger_deadzone: float = 0.01
    invert_left_y:    bool  = False
    invert_right_y:   bool  = False
    rumble_enabled:   bool  = True
    rumble_mult:      float = 1.0
    lut_left_x:  tuple = field(default_factory=tuple)
    lut_left_y:  tuple = field(default_factory=tuple)
    lut_right_x: tuple = field(default_factory=tuple)
    lut_right_y: tuple = field(default_factory=tuple)
    lut_lt:      tuple = field(default_factory=tuple)
    lut_rt:      tuple = field(default_factory=tuple)
    pt_left_x:  bool = True
    pt_left_y:  bool = True
    pt_right_x: bool = True
    pt_right_y: bool = True
    pt_lt:      bool = True
    pt_rt:      bool = True


def default_config() -> dict:
    return {
        "left_x_mult": 1.0, "left_y_mult": 1.0,
        "right_x_mult": 1.0, "right_y_mult": 1.0,
        "trigger_mult": 1.0,
        "left_deadzone": 0.02, "right_deadzone": 0.02, "trigger_deadzone": 0.01,
        "invert_left_y": False, "invert_right_y": False,
        "curve_left_x":  copy.deepcopy(LINEAR_CURVE),
        "curve_left_y":  copy.deepcopy(LINEAR_CURVE),
        "curve_right_x": copy.deepcopy(LINEAR_CURVE),
        "curve_right_y": copy.deepcopy(LINEAR_CURVE),
        "curve_lt":      copy.deepcopy(LINEAR_CURVE),
        "curve_rt":      copy.deepcopy(LINEAR_CURVE),
        "mode_left_x": "mult", "mode_left_y": "mult",
        "mode_right_x": "mult", "mode_right_y": "mult",
        "mode_lt": "mult", "mode_rt": "mult",
        "rumble_enabled": True, "rumble_mult": 1.0,
    }


def config_to_snapshot(cfg: dict) -> ConfigSnapshot:
    def make_lut(ck, mk, vk):
        return build_lut(cfg[ck], cfg[mk], cfg[vk])
    return ConfigSnapshot(
        left_x_mult      = cfg["left_x_mult"],
        left_y_mult      = cfg["left_y_mult"],
        right_x_mult     = cfg["right_x_mult"],
        right_y_mult     = cfg["right_y_mult"],
        trigger_mult     = cfg["trigger_mult"],
        left_deadzone    = cfg["left_deadzone"],
        right_deadzone   = cfg["right_deadzone"],
        trigger_deadzone = cfg["trigger_deadzone"],
        invert_left_y    = cfg["invert_left_y"],
        invert_right_y   = cfg["invert_right_y"],
        rumble_enabled   = cfg["rumble_enabled"],
        rumble_mult      = cfg["rumble_mult"],
        lut_left_x  = make_lut("curve_left_x",  "mode_left_x",  "left_x_mult"),
        lut_left_y  = make_lut("curve_left_y",  "mode_left_y",  "left_y_mult"),
        lut_right_x = make_lut("curve_right_x", "mode_right_x", "right_x_mult"),
        lut_right_y = make_lut("curve_right_y", "mode_right_y", "right_y_mult"),
        lut_lt      = make_lut("curve_lt",       "mode_lt",      "trigger_mult"),
        lut_rt      = make_lut("curve_rt",       "mode_rt",      "trigger_mult"),
        pt_left_x  = _is_passthrough(cfg["mode_left_x"],  cfg["left_x_mult"],  cfg["left_deadzone"],  False),
        pt_left_y  = _is_passthrough(cfg["mode_left_y"],  cfg["left_y_mult"],  cfg["left_deadzone"],  cfg["invert_left_y"]),
        pt_right_x = _is_passthrough(cfg["mode_right_x"], cfg["right_x_mult"], cfg["right_deadzone"], False),
        pt_right_y = _is_passthrough(cfg["mode_right_y"], cfg["right_y_mult"], cfg["right_deadzone"], cfg["invert_right_y"]),
        pt_lt      = _is_trigger_passthrough(cfg["mode_lt"], cfg["trigger_mult"], cfg["trigger_deadzone"]),
        pt_rt      = _is_trigger_passthrough(cfg["mode_rt"], cfg["trigger_mult"], cfg["trigger_deadzone"]),
    )

# ─── Profily ──────────────────────────────────────────────────────────────────

def load_app_settings() -> dict:
    try:
        with open(APP_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return {"tray_minimize": False, **json.load(f)}
    except Exception:
        return {"tray_minimize": False}

def save_app_settings(s: dict):
    try:
        with open(APP_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Could not save settings: {e}")

def load_all_profiles() -> dict:
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        base = default_config()
        return {k: {**base, **v} for k, v in data.items()}
    except Exception:
        return {}

def save_all_profiles(profiles: dict):
    try:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(profiles, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Could not save profiles: {e}")

# ─── pygame gamepad discovery ─────────────────────────────────────────────────

def get_gamepads():
    _pg_init()
    pygame.joystick.quit(); pygame.joystick.init()
    result = []
    for i in range(pygame.joystick.get_count()):
        try:
            j = pygame.joystick.Joystick(i)
            result.append((i, j.get_name()))
        except Exception:
            result.append((i, f"Controller {i}"))
    return result

# ─── Pomocné ─────────────────────────────────────────────────────────────────

def apply_deadzone(v: float, dz: float) -> float:
    """Cutoff only: values inside the deadzone become 0; all others stay unchanged."""
    if abs(v) < dz: return 0.0
    return v

def clamp1(v: float) -> float:
    if v != v: return 0.0
    return 1.0 if v > 1.0 else (-1.0 if v < -1.0 else v)

def _nan_safe(v: float, fallback: float = 0.0) -> float:
    return fallback if (v != v) else v

def to_vg_axis(v: float) -> int:   return int(_nan_safe(v) * 32767)
def to_vg_trigger(v: float) -> int: return int(_nan_safe(max(0.0, min(1.0, v))) * 255)

# ─── CurveEditor ─────────────────────────────────────────────────────────────

class CurveEditor(tk.Canvas):
    W, H, PAD, R = 200, 200, 16, 6
    _CURVE_STEPS = 65

    def __init__(self, parent, points, on_change=None):
        super().__init__(parent, width=self.W, height=self.H,
                         bg="#1a1a2a", highlightthickness=1, highlightbackground="#3a3a5a")
        self.points    = sorted([[p[0], p[1]] for p in points], key=lambda p: p[0])
        self.on_change = on_change
        self._drag_idx = None
        self._static_drawn = False
        self._curve_line   = None
        self._fill_poly    = None
        self._dot_items    = []
        self.bind("<Double-Button-1>", self._add_point)
        self.bind("<Button-3>",        self._remove_point)
        self.bind("<ButtonPress-1>",   self._start_drag)
        self.bind("<B1-Motion>",       self._drag)
        self.bind("<ButtonRelease-1>", self._end_drag)
        self.draw()

    def _tc(self, x, y):
        return self.PAD + x*(self.W-2*self.PAD), self.H-self.PAD-y*(self.H-2*self.PAD)

    def _fc(self, cx, cy):
        x = (cx-self.PAD)/(self.W-2*self.PAD)
        y = (self.H-self.PAD-cy)/(self.H-2*self.PAD)
        return max(0.0,min(1.0,x)), max(0.0,min(1.0,y))

    def _build_static(self):
        W,H,P = self.W,self.H,self.PAD
        for i in range(1,4):
            t=i/4; cx,_=self._tc(t,0); _,cy=self._tc(0,t)
            self.create_line(cx,P,cx,H-P,fill="#2a2a4a",dash=(3,4),tags="static")
            self.create_line(P,cy,W-P,cy,fill="#2a2a4a",dash=(3,4),tags="static")
        x0,y0=self._tc(0,0); x1,y1=self._tc(1,1)
        self.create_line(x0,y0,x1,y1,fill="#3a3a5a",dash=(2,4),tags="static")
        self.create_rectangle(P,P,W-P,H-P,outline="#3a3a5a",tags="static")
        self._static_drawn = True

    def _curve_coords(self, pts_sorted):
        coords=[]
        for i in range(self._CURVE_STEPS):
            xv=i/(self._CURVE_STEPS-1); yv=eval_curve(pts_sorted,xv)
            cx,cy=self._tc(xv,yv); coords.append(cx); coords.append(cy)
        return coords

    def draw(self):
        if not self._static_drawn: self._build_static()
        pts=sorted(self.points,key=lambda p:p[0])
        coords=self._curve_coords(pts)
        bx1,by1=self._tc(1,0); bx0,by0=self._tc(0,0)
        fc=coords+[bx1,by1,bx0,by0]
        if self._fill_poly is None:
            self._fill_poly=self.create_polygon(*fc,fill="#1e3a5f",outline="",tags="dyn")
        else: self.coords(self._fill_poly,*fc)
        if len(coords)>=4:
            if self._curve_line is None:
                self._curve_line=self.create_line(*coords,fill="#4a9eff",width=2,tags="dyn")
            else: self.coords(self._curve_line,*coords)
            self.tag_raise(self._curve_line)
        while len(self._dot_items)<len(pts):
            self._dot_items.append(self.create_oval(0,0,1,1,fill="#50fa7b",outline="#1a1a2a",width=2,tags="dyn"))
        while len(self._dot_items)>len(pts): self.delete(self._dot_items.pop())
        for i,(px,py) in enumerate(pts):
            cx,cy=self._tc(px,py)
            col="#ff5252" if (i==0 or i==len(pts)-1) else "#50fa7b"
            self.coords(self._dot_items[i],cx-self.R,cy-self.R,cx+self.R,cy+self.R)
            self.itemconfig(self._dot_items[i],fill=col); self.tag_raise(self._dot_items[i])

    def _nearest(self,cx,cy,thr=14):
        for i,(px,py) in enumerate(self.points):
            ecx,ecy=self._tc(px,py)
            if abs(ecx-cx)<thr and abs(ecy-cy)<thr: return i
        return None

    def _add_point(self,e):
        x,y=self._fc(e.x,e.y)
        for px,_ in self.points:
            if abs(px-x)<0.03: return
        self.points.append([x,y]); self.points.sort(key=lambda p:p[0])
        self.draw()
        if self.on_change: self.on_change(self.points)

    def _remove_point(self,e):
        idx=self._nearest(e.x,e.y)
        if idx is None or idx==0 or idx==len(self.points)-1: return
        self.points.pop(idx); self.draw()
        if self.on_change: self.on_change(self.points)

    def _start_drag(self,e): self._drag_idx=self._nearest(e.x,e.y)
    def _drag(self,e):
        if self._drag_idx is None: return
        x,y=self._fc(e.x,e.y); i=self._drag_idx
        if i==0: self.points[i]=[0.0,0.0]
        elif i==len(self.points)-1: self.points[i]=[1.0,1.0]
        else:
            px=max(self.points[i-1][0]+0.02,min(self.points[i+1][0]-0.02,x))
            self.points[i]=[px,y]
        self.draw()
        if self.on_change: self.on_change(self.points)
    def _end_drag(self,e): self._drag_idx=None
    def set_points(self,pts):
        self.points=sorted([[p[0],p[1]] for p in pts],key=lambda p:p[0]); self.draw()
    def get_points(self): return copy.deepcopy(self.points)
    def copy_cb(self): CurveEditor._cb=copy.deepcopy(self.points)
    def paste_cb(self):
        if hasattr(CurveEditor,'_cb'):
            self.set_points(CurveEditor._cb)
            if self.on_change: self.on_change(self.points)
    def reset_linear(self):
        self.set_points(copy.deepcopy(LINEAR_CURVE))
        if self.on_change: self.on_change(self.points)

# ─── CurvePanel ───────────────────────────────────────────────────────────────

class CurvePanel(ctk.CTkFrame):
    def __init__(self,parent,label,key_curve,key_mode,key_mult,config,on_change):
        super().__init__(parent,corner_radius=8)
        self.key_curve=key_curve; self.key_mode=key_mode
        self.key_mult=key_mult;   self.on_change=on_change
        ctk.CTkLabel(self,text=label,font=ctk.CTkFont(size=12,weight="bold")).pack(anchor="w",padx=12,pady=(10,4))
        self.mode_var=ctk.StringVar(value="Multiplier" if config[key_mode]=="mult" else "Curve")
        ctk.CTkSegmentedButton(self,values=["Multiplier","Curve"],
                               variable=self.mode_var,command=self._mode_changed).pack(padx=12,pady=(0,8),fill="x")
        self.mult_frame=ctk.CTkFrame(self,fg_color="transparent")
        self.mult_var=ctk.DoubleVar(value=config[key_mult])
        self.mult_lbl=ctk.CTkLabel(self.mult_frame,text=f"{config[key_mult]:.2f}",
                                   width=40,font=ctk.CTkFont(family="Consolas",size=11))
        ctk.CTkLabel(self.mult_frame,text="Value",width=70,anchor="w").pack(side="left",padx=(12,4))
        ctk.CTkSlider(self.mult_frame,from_=MULT_MIN,to=MULT_MAX,variable=self.mult_var,
                      command=self._mult_changed).pack(side="left",fill="x",expand=True,padx=4)
        self.mult_lbl.pack(side="left",padx=(0,12))
        self.curve_frame=ctk.CTkFrame(self,fg_color="transparent")
        self.editor=CurveEditor(self.curve_frame,copy.deepcopy(config[key_curve]),
                                on_change=lambda pts:on_change(key_curve,pts))
        self.editor.pack(padx=12,pady=(4,4))
        br=ctk.CTkFrame(self.curve_frame,fg_color="transparent"); br.pack(fill="x",padx=12,pady=(0,8))
        ctk.CTkButton(br,text="Copy",width=80,height=26,command=self.editor.copy_cb).pack(side="left",padx=(0,4))
        ctk.CTkButton(br,text="Paste",width=70,height=26,
                      command=lambda:(self.editor.paste_cb(),on_change(key_curve,self.editor.get_points()))
                      ).pack(side="left")
        ctk.CTkButton(br,text="↺",width=36,height=26,fg_color="transparent",border_width=1,
                      command=lambda:(self.editor.reset_linear(),on_change(key_curve,self.editor.get_points()))
                      ).pack(side="right")
        self._update_visibility()

    def _mode_changed(self,val):
        self.on_change(self.key_mode,"mult" if val=="Multiplier" else "curve")
        self._update_visibility()
    def _mult_changed(self,val):
        fval=round(float(val),2); self.mult_lbl.configure(text=f"{fval:.2f}"); self.on_change(self.key_mult,fval)
    def _update_visibility(self):
        if self.mode_var.get()=="Multiplier":
            self.curve_frame.pack_forget(); self.mult_frame.pack(fill="x",pady=(0,8))
        else:
            self.mult_frame.pack_forget(); self.curve_frame.pack(fill="x")
    def reset_to_default(self):
        self.mode_var.set("Multiplier"); self.mult_var.set(1.0); self.mult_lbl.configure(text="1.00")
        self.editor.reset_linear(); self._update_visibility()
        self.on_change(self.key_mode,"mult"); self.on_change(self.key_mult,1.0)
        self.on_change(self.key_curve,copy.deepcopy(LINEAR_CURVE))
    def load_from_config(self,cfg):
        self.mode_var.set("Multiplier" if cfg[self.key_mode]=="mult" else "Curve")
        self.mult_var.set(cfg[self.key_mult]); self.mult_lbl.configure(text=f"{cfg[self.key_mult]:.2f}")
        self.editor.set_points(cfg[self.key_curve]); self._update_visibility()

# ─── InputVisualizer ──────────────────────────────────────────────────────────

class InputVisualizer(ctk.CTkFrame):
    STICK_SIZE  = 90
    TRIG_W, TRIG_H = 44, 90   # širší a vyšší pro lepší čitelnost

    def __init__(self,parent):
        super().__init__(parent,corner_radius=8,fg_color="transparent")
        self._lx=self._ly=self._rx=self._ry=self._lt=self._rt=0.0
        self._raw_lx=self._raw_ly=self._raw_rx=self._raw_ry=self._raw_lt=self._raw_rt=0.0
        self._show_raw = False
        self._buttons: frozenset=frozenset()
        self._dz_left=self._dz_right=0.02; self._prev_dz=(0.02,0.02)
        self._left_items=self._right_items={}
        self._lt_items=self._rt_items={}
        self._lt_canvas=self._rt_canvas=None   # přímé reference na canvas
        self._build()

    def _build(self):
        mode_row=ctk.CTkFrame(self,fg_color="transparent"); mode_row.pack(fill="x",padx=8,pady=(4,0))
        self._mode_var=ctk.StringVar(value="Output")
        ctk.CTkSegmentedButton(mode_row,values=["Raw","Output"],variable=self._mode_var,
                               command=self._mode_toggle,width=160).pack(side="right",padx=4)
        row=ctk.CTkFrame(self,fg_color="transparent"); row.pack(fill="x",padx=8,pady=4)

        # LT trigger – úplně vlevo
        lt_wrap=ctk.CTkFrame(row,fg_color="transparent"); lt_wrap.pack(side="left",padx=(4,8))
        ctk.CTkLabel(lt_wrap,text="LT",font=ctk.CTkFont(size=10,weight="bold"),text_color="#aaaacc").pack()
        self._lt_canvas=tk.Canvas(lt_wrap,width=self.TRIG_W,height=self.TRIG_H,
                                  bg="#1a1a2a",highlightthickness=1,highlightbackground="#3a3a5a")
        self._lt_canvas.pack()
        self._lt_pct_lbl=ctk.CTkLabel(lt_wrap,text="0 %",font=ctk.CTkFont(family="Consolas",size=9),text_color="gray")
        self._lt_pct_lbl.pack()

        # Levý stick
        lf=ctk.CTkFrame(row,fg_color="transparent"); lf.pack(side="left",padx=8)
        ctk.CTkLabel(lf,text="L",font=ctk.CTkFont(size=10),text_color="gray").pack()
        self._left_canvas=self._make_stick_canvas(lf); self._left_canvas.pack()

        # Střed – tlačítka
        mid=ctk.CTkFrame(row,fg_color="transparent"); mid.pack(side="left",expand=True)
        self._btn_labels={}
        bg=ctk.CTkFrame(mid,fg_color="transparent"); bg.pack()
        for i,name in enumerate(["A","B","X","Y","LB","RB","LT","RT","Start","Back","L3","R3"]):
            lbl=ctk.CTkLabel(bg,text=name,width=36,height=22,font=ctk.CTkFont(size=9),
                             fg_color="#2a2a3a",corner_radius=4,text_color="gray")
            lbl.grid(row=i//4,column=i%4,padx=2,pady=2); self._btn_labels[name]=lbl

        # Pravý stick
        rf=ctk.CTkFrame(row,fg_color="transparent"); rf.pack(side="left",padx=8)
        ctk.CTkLabel(rf,text="R",font=ctk.CTkFont(size=10),text_color="gray").pack()
        self._right_canvas=self._make_stick_canvas(rf); self._right_canvas.pack()

        # RT trigger – úplně vpravo
        rt_wrap=ctk.CTkFrame(row,fg_color="transparent"); rt_wrap.pack(side="left",padx=(8,4))
        ctk.CTkLabel(rt_wrap,text="RT",font=ctk.CTkFont(size=10,weight="bold"),text_color="#aaaacc").pack()
        self._rt_canvas=tk.Canvas(rt_wrap,width=self.TRIG_W,height=self.TRIG_H,
                                  bg="#1a1a2a",highlightthickness=1,highlightbackground="#3a3a5a")
        self._rt_canvas.pack()
        self._rt_pct_lbl=ctk.CTkLabel(rt_wrap,text="0 %",font=ctk.CTkFont(family="Consolas",size=9),text_color="gray")
        self._rt_pct_lbl.pack()

        self.after(10,self._init_canvas_items)

    def _mode_toggle(self,val):
        self._show_raw = (val == "Raw")
        if self._show_raw:
            self._redraw_stick(self._left_canvas,  self._left_items,  self._raw_lx, self._raw_ly, self._dz_left,  invert_y=True)
            self._redraw_stick(self._right_canvas, self._right_items, self._raw_rx, self._raw_ry, self._dz_right, invert_y=True)
            self._redraw_trigger(self._lt_canvas, self._lt_items, self._lt_pct_lbl, self._raw_lt)
            self._redraw_trigger(self._rt_canvas, self._rt_items, self._rt_pct_lbl, self._raw_rt)
        else:
            self._redraw_stick(self._left_canvas,  self._left_items,  self._lx, self._ly, self._dz_left,  invert_y=True)
            self._redraw_stick(self._right_canvas, self._right_items, self._rx, self._ry, self._dz_right, invert_y=True)
            self._redraw_trigger(self._lt_canvas, self._lt_items, self._lt_pct_lbl, self._lt)
            self._redraw_trigger(self._rt_canvas, self._rt_items, self._rt_pct_lbl, self._rt)

    def _make_stick_canvas(self,parent):
        S=self.STICK_SIZE
        return tk.Canvas(parent,width=S,height=S,bg="#1a1a2a",highlightthickness=1,highlightbackground="#3a3a5a")

    def _init_canvas_items(self):
        self._left_items  = self._create_stick_items(self._left_canvas)
        self._right_items = self._create_stick_items(self._right_canvas)
        self._lt_items    = self._create_trigger_items(self._lt_canvas)
        self._rt_items    = self._create_trigger_items(self._rt_canvas)
        self._redraw_stick(self._left_canvas,  self._left_items,  0.0, 0.0, self._dz_left)
        self._redraw_stick(self._right_canvas, self._right_items, 0.0, 0.0, self._dz_right)
        self._redraw_trigger(self._lt_canvas, self._lt_items, self._lt_pct_lbl, 0.0)
        self._redraw_trigger(self._rt_canvas, self._rt_items, self._rt_pct_lbl, 0.0)

    def _create_stick_items(self,canvas):
        S=self.STICK_SIZE; R=S//2
        canvas.create_line(R,4,R,S-4,fill="#2a2a4a",width=1)
        canvas.create_line(4,R,S-4,R,fill="#2a2a4a",width=1)
        canvas.create_oval(4,4,S-4,S-4,outline="#3a3a5a",width=1)
        return {"dz":canvas.create_oval(R,R,R,R,outline="#5a5a88",dash=(2,3)),
                "line":canvas.create_line(R,R,R,R,fill="#4a9eff",width=2),
                "dot":canvas.create_oval(R-6,R-6,R+6,R+6,fill="#50fa7b",outline=""),
                "center":canvas.create_oval(R-3,R-3,R+3,R+3,fill="#3a3a5a",outline="")}
    def _create_trigger_items(self,canvas):
        W,H=self.TRIG_W,self.TRIG_H
        # Pozadí + rámeček
        canvas.create_rectangle(2,2,W-2,H-2,fill="#111120",outline="#3a3a5a",width=1)
        # Vertikální fill bar (plní se zdola nahoru)
        fill=canvas.create_rectangle(4,H-4,W-4,H-4,fill="#4a9eff",outline="",state="hidden")
        return {"fill": fill}

    def _redraw_stick(self,canvas,items,x,y,dz=0.02,invert_y=False):
        if not items or x != x or y != y: return
        S=self.STICK_SIZE; R=S//2
        dr=int((R-8)*dz*2)
        if dr>1: canvas.coords(items["dz"],R-dr,R-dr,R+dr,R+dr); canvas.itemconfig(items["dz"],state="normal")
        else:    canvas.itemconfig(items["dz"],state="hidden")
        px=int(R+x*(R-8)); py=int(R+(-y if invert_y else y)*(R-8))
        canvas.coords(items["line"],R,R,px,py)
        canvas.coords(items["dot"],px-6,py-6,px+6,py+6)
        canvas.itemconfig(items["dot"],fill="#ff5252" if (abs(x)>0.85 or abs(y)>0.85) else "#50fa7b")
        canvas.tag_raise(items["center"])
    def _redraw_trigger(self,canvas,items,pct_lbl,value):
        if not items or canvas is None or value != value: return
        W,H=self.TRIG_W,self.TRIG_H
        bar_top=4; bar_bot=H-4
        bar_h=bar_bot-bar_top       # maximální výška baru = H-8
        filled=int(value*bar_h)
        if filled>0:
            top=bar_bot-filled
            canvas.coords(items["fill"],4,top,W-4,bar_bot)
            color="#ff5252" if value>=0.9 else ("#ff9800" if value>=0.6 else "#4a9eff")
            canvas.itemconfig(items["fill"],fill=color,state="normal")
        else:
            canvas.itemconfig(items["fill"],state="hidden")
        pct=int(value*100)
        pct_lbl.configure(text=f"{pct} %",text_color="#ff5252" if value>=0.9 else ("#ff9800" if value>=0.6 else "gray"))

    def set_deadzone(self,dz_left,dz_right): self._dz_left=dz_left; self._dz_right=dz_right

    def update_state(self,state:dict)->bool:
        lx=state["lx"]; ly=state["ly"]; rx=state["rx"]; ry=state["ry"]
        lt=state["lt"]; rt=state["rt"]
        raw_lx=state.get("raw_lx",lx); raw_ly=state.get("raw_ly",ly)
        raw_rx=state.get("raw_rx",rx); raw_ry=state.get("raw_ry",ry)
        raw_lt=state.get("raw_lt",lt); raw_rt=state.get("raw_rt",rt)
        new_btns=state["buttons"] if isinstance(state["buttons"],frozenset) else frozenset(state["buttons"])
        self._lx=lx; self._ly=ly; self._rx=rx; self._ry=ry
        self._lt=lt; self._rt=rt
        self._raw_lx=raw_lx; self._raw_ly=raw_ly; self._raw_rx=raw_rx; self._raw_ry=raw_ry
        self._raw_lt=raw_lt; self._raw_rt=raw_rt
        if self._show_raw:
            self._redraw_stick(self._left_canvas,  self._left_items,  raw_lx, raw_ly, self._dz_left,  invert_y=True)
            self._redraw_stick(self._right_canvas, self._right_items, raw_rx, raw_ry, self._dz_right, invert_y=True)
            self._redraw_trigger(self._lt_canvas, self._lt_items, self._lt_pct_lbl, raw_lt)
            self._redraw_trigger(self._rt_canvas, self._rt_items, self._rt_pct_lbl, raw_rt)
        else:
            self._redraw_stick(self._left_canvas,  self._left_items,  lx, ly, self._dz_left,  invert_y=True)
            self._redraw_stick(self._right_canvas, self._right_items, rx, ry, self._dz_right, invert_y=True)
            self._redraw_trigger(self._lt_canvas, self._lt_items, self._lt_pct_lbl, lt)
            self._redraw_trigger(self._rt_canvas, self._rt_items, self._rt_pct_lbl, rt)
        changed=new_btns.symmetric_difference(self._buttons); self._buttons=new_btns
        for code in changed:
            label=_BTN_CODE_TO_LABEL.get(code)
            if label and label in self._btn_labels:
                active=code in new_btns
                self._btn_labels[label].configure(fg_color="#3a6fff" if active else "#2a2a3a",
                                                  text_color="white"  if active else "gray")
        return True

# ─── ControllerRemapper (pygame backend) ─────────────────────────────────────

class ControllerRemapper:
    _POLL_INTERVAL = 0.002   # 2 ms ≈ 500 Hz

    def __init__(self, config: dict, gamepad_index: int, log_callback=None):
        self.gamepad_index = gamepad_index
        self.log_callback  = log_callback
        self._snapshot     = config_to_snapshot(config)
        self._snap_lock    = threading.Lock()
        self._running      = False
        self._thread       = None
        self.gamepad       = vg.VX360Gamepad()
        self._xinput_index = _find_xinput_index(gamepad_index)
        self._viz_lock     = threading.Lock()
        self.viz_state     = {"lx":0.0,"ly":0.0,"rx":0.0,"ry":0.0,"lt":0.0,"rt":0.0,"buttons":frozenset(),
                              "raw_lx":0.0,"raw_ly":0.0,"raw_rx":0.0,"raw_ry":0.0,"raw_lt":0.0,"raw_rt":0.0}
        self._log_ts: dict = {}
        self._prev_lx = self._prev_ly = 0.0
        self._prev_rx = self._prev_ry = 0.0
        self._prev_lt = self._prev_rt = 0.0
        self._prev_btn_state: dict = {}

        self.gamepad.register_notification(self._vibration_callback)
        self.gamepad.update()

    def update_config(self, cfg: dict):
        snap = config_to_snapshot(cfg)
        with self._snap_lock: self._snapshot = snap

    def start(self):
        if self._running: return
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        threading.Thread(target=self._notification_loop, daemon=True).start()
        threading.Thread(target=self._detect_xinput_delayed, daemon=True).start()

    def _notification_loop(self):
        self._log("📳 Rumble notification thread started", "rumble")
        while self._running:
            try:
                self.gamepad._client.wait_for_notifications(timeout=0.1)
            except Exception:
                time.sleep(0.1)

    def _detect_xinput_delayed(self):
        time.sleep(1.5)
        if _XINPUT is None: return
        try:
            class _S(ctypes.Structure):
                class _G(ctypes.Structure):
                    _fields_=[("wButtons",ctypes.c_ushort),("bLeftTrigger",ctypes.c_ubyte),
                              ("bRightTrigger",ctypes.c_ubyte),("sThumbLX",ctypes.c_short),
                              ("sThumbLY",ctypes.c_short),("sThumbRX",ctypes.c_short),("sThumbRY",ctypes.c_short)]
                _fields_=[("dwPacketNumber",ctypes.c_uint),("Gamepad",_G)]
            connected=[i for i in range(4) if _XINPUT.XInputGetState(i,ctypes.byref(_S()))==0]
            if connected:
                self._xinput_index=self.gamepad_index if self.gamepad_index in connected else connected[0]
                self._log(f"🎮 XInput index: {self._xinput_index}  (connected slots: {connected})", "info")
            else: self._log("⚠️  XInput: no connected slot found", "warn")
        except Exception as e: self._log(f"⚠️  XInput detection: {e}", "warn")

    def stop(self):
        self._running = False
        try: self._send_rumble_to_physical(0.0, 0.0)
        except Exception: pass

    def _log(self, msg: str, tag: str = ""):
        if self.log_callback:
            self.log_callback(msg, tag)

    def _log_axis(self, code: str, msg: str):
        now = time.monotonic()
        if now - self._log_ts.get(code, 0.0) >= _LOG_AXIS_INTERVAL:
            self._log_ts[code] = now; self._log(msg, "axis")

    def _vibration_callback(self, client, target, large_motor, small_motor, led_number, user_data):
        try:
            with self._snap_lock: snap = self._snapshot
            if not snap.rumble_enabled:
                self._send_rumble_to_physical(0.0, 0.0); return
            left  = min(1.0, (large_motor / 65535.0) * snap.rumble_mult)
            right = min(1.0, (small_motor / 65535.0) * snap.rumble_mult)
            self._log(f"📳 Rumble  L={left:.2f}  R={right:.2f}", "rumble")
            self._send_rumble_to_physical(left, right)
        except Exception as e: self._log(f"⚠️  Rumble callback: {e}", "warn")

    def _send_rumble_to_physical(self, left: float, right: float):
        if _XINPUT is None: self._log("⚠️  XInput DLL not found", "warn"); return
        try:
            vib = _XINPUT_VIBRATION(int(max(0.0,min(1.0,left))*65535),
                                    int(max(0.0,min(1.0,right))*65535))
            _XINPUT.XInputSetState(self._xinput_index, ctypes.byref(vib))
        except Exception as e: self._log(f"⚠️  Rumble: {e}", "warn")

    # ── Axis processing helpers ───────────────────────────────────────────────

    @staticmethod
    def _process_stick_axis(raw: float, lut: tuple, dz: float, passthrough: bool) -> float:
        raw = _nan_safe(raw)
        if passthrough:
            return raw
        if abs(raw) < dz:
            return 0.0
        s = 1.0 if raw >= 0 else -1.0
        mapped = lut_lookup(lut, abs(raw))
        rescaled = (mapped - lut_lookup(lut, dz)) / max(1e-6, 1.0 - lut_lookup(lut, dz))
        return s * max(0.0, min(1.0, rescaled))

    @staticmethod
    def _process_trigger(v01: float, lut: tuple, dz: float, passthrough: bool) -> float:
        v01 = _nan_safe(v01)
        v = max(0.0, min(1.0, v01))
        if passthrough:
            return v
        if v < dz:
            return 0.0
        mapped = lut_lookup(lut, v)
        lut_at_dz = lut_lookup(lut, dz)
        rescaled = (mapped - lut_at_dz) / max(1e-6, 1.0 - lut_at_dz)
        return max(0.0, min(1.0, rescaled))

    # ── Hlavní smyčka ─────────────────────────────────────────────────────────

    def _loop(self):
        self._log(f"Started (pygame) – controller #{self.gamepad_index}", "info")
        gp = self.gamepad

        try:
            joy = pygame.joystick.Joystick(self.gamepad_index)
            joy.init()
            n_axes = joy.get_numaxes()
            n_btns = joy.get_numbuttons()
            n_hats = joy.get_numhats()
            self._log(f"🎮 {joy.get_name()}  axes={n_axes}  btns={n_btns}  hats={n_hats}", "info")
        except Exception as e:
            self._log(f"❌ Could not initialize joystick: {e}", "err"); return

        # Stick osy
        ax_lx = _PG_AXIS_LX if n_axes > _PG_AXIS_LX else 0
        ax_ly = _PG_AXIS_LY if n_axes > _PG_AXIS_LY else 1
        ax_rx = _PG_AXIS_RX if n_axes > _PG_AXIS_RX else 2
        ax_ry = _PG_AXIS_RY if n_axes > _PG_AXIS_RY else 3

        # ── Detekce trigger os ────────────────────────────────────────────────
        # pygame-ce na Windows s XInput driverem:
        #   n_axes == 6 → osy 0-3 = sticks, osa 4 = LT, osa 5 = RT
        #                  každá trigger osa: -1.0 (uvolněno) .. +1.0 (stisknuto)
        #
        # Starší SDL/DirectInput driver nebo Bluetooth bez XInput:
        #   triggery sdílejí jednu osu (typicky osa 2 nebo jinou)
        #   LT = záporná část, RT = kladná část
        #
        # Detekujeme podle počtu os:
        #   >= 6 os → separátní trigger osy (případ A)
        #   jinak   → sdílená osa (případ B)
        #
        # Navíc: při startu přečteme raw hodnotu trigger os v klidové poloze.
        # Separátní osa v klidu = -1.0 (nebo blízko toho).
        # Sdílená osa v klidu = 0.0.
        # Tímto rozlišujeme i edge case kdy driver hlásí 6 os ale chová se jako sdílená.

        # ── Trigger axis autodekekce + kalibrace ─────────────────────────────
        # Přístup: přečteme raw hodnotu každé osy v klidové poloze (bez stisku).
        # Tato hodnota = "nula" triggeru. Při stisku se osa pohybuje od této nuly
        # směrem k +1.0 nebo -1.0.
        #
        # Formáty které pygame-ce může vrátit:
        #   XInput separátní:  osa4=LT, osa5=RT, klid=-1.0, stisk=+1.0
        #   DirectInput shared: jedna osa, LT záporná část, RT kladná, klid=0.0
        #   Bluetooth shared:  jedna osa, klid=+1.0 (LT stisk jde do zápora)
        #
        # Kalibrace řeší všechno najednou: pokud víme klidovou hodnotu,
        # stačí zjistit JEN směr pohybu při stisku (+ nebo -) a normalizovat.

        # Počkej než pygame dostane první data od driveru
        for _ in range(5):
            pygame.event.pump()
            time.sleep(0.05)

        # Přečti klidové hodnoty os 4 a 5 (nebo co je dostupné)
        _ax4_rest = joy.get_axis(4) if n_axes > 4 else None
        _ax5_rest = joy.get_axis(5) if n_axes > 5 else None

        self._log(
            f"🎚️  Trigger init: n_axes={n_axes}  "
            f"ax4_rest={_ax4_rest}  ax5_rest={_ax5_rest}",
            "info"
        )

        # Rozhodnutí o modu:
        # Pokud ax4 a ax5 existují a mají RŮZNÉ klidové hodnoty → separátní osy (Formát A)
        # Pokud ax4 a ax5 existují ale mají STEJNÉ klidové hodnoty → je to sdílená osa (Formát C)
        # Pokud jen ax4 existuje (n_axes==5) → zkusíme sdílenou osu
        # Jinak hledáme jakoukoli non-stick osu

        if _ax4_rest is not None and _ax5_rest is not None:
            _mode = "separate"
            _lt_ax, _rt_ax = 4, 5
            _lt_rest, _rt_rest = _ax4_rest, _ax5_rest
        elif _ax4_rest is not None:
            # Jen jedna osa → sdílená (starší DirectInput driver)
            _mode = "shared"
            _lt_ax = _rt_ax = 4
            _lt_rest = _rt_rest = _ax4_rest
        else:
            # Fallback
            _mode = "shared"
            _lt_ax = _rt_ax = -1
            _lt_rest = _rt_rest = 0.0
            for c in [2, 3, 4, 5]:
                if c < n_axes:
                    _lt_ax = _rt_ax = c
                    _lt_rest = _rt_rest = joy.get_axis(c)
                    break

        self._log(f"🎚️  Trigger mode={_mode}  LT ax={_lt_ax} rest={_lt_rest:.3f}  RT ax={_rt_ax} rest={_rt_rest:.3f}", "info")

        def _read_raw(ax: int, rest: float, positive_direction: bool) -> float:
            """Normalize an axis to 0..1:
            - positive_direction=True: raw=rest→0, raw→+1→1
            - positive_direction=False: raw=rest→0, raw→-1→1
            """
            try:
                if ax < 0:
                    return 0.0
                raw = joy.get_axis(ax)
                if positive_direction:
                    denom = 1.0 - rest
                    return 0.0 if denom < 1e-6 else max(0.0, min(1.0, (raw - rest) / denom))
                else:
                    denom = rest + 1.0
                    return 0.0 if denom < 1e-6 else max(0.0, min(1.0, (rest - raw) / denom))
            except Exception as e:
                self._log(f"⚠️ _read_raw(ax={ax}): {e}", "warn")
                return 0.0

        def _read_lt() -> float:
            if _mode == "separate":
                return _read_raw(_lt_ax, _lt_rest, True)
            else:
                return _read_raw(_lt_ax, _lt_rest, False)

        def _read_rt() -> float:
            if _mode == "separate":
                return _read_raw(_rt_ax, _rt_rest, True)
            else:
                return _read_raw(_rt_ax, _rt_rest, True)

        # Inicializuj prev hodnoty na skutečný stav (zabrání falešnému prvnímu update)
        self._prev_lt = _read_lt()
        self._prev_rt = _read_rt()
        self.viz_state["lt"] = self._prev_lt
        self.viz_state["rt"] = self._prev_rt
        self.viz_state["raw_lt"] = self._prev_lt
        self.viz_state["raw_rt"] = self._prev_rt

        while self._running:
            try:
                pygame.event.pump()
            except Exception as e:
                self._log(f"⚠️ event.pump: {e}", "err")
                time.sleep(0.05)
                continue

            with self._snap_lock: snap = self._snapshot
            dirty = False

            # Inicializace proměnných na poslední známé hodnoty
            raw_lx = self._prev_lx; raw_ly = self._prev_ly
            raw_rx = self._prev_rx; raw_ry = self._prev_ry
            lt_raw = self._prev_lt; rt_raw = self._prev_rt
            xo = self._prev_lx; yo = self._prev_ly
            xo2 = self._prev_rx; yo2 = self._prev_ry
            lt_out = self._prev_lt; rt_out = self._prev_rt
            btns_set = self.viz_state["buttons"]

            try:
                # ── Batch read všech axis (jedno get_axis volání na osu) ──
                axes_raw = []
                for i in range(n_axes):
                    try:
                        axes_raw.append(joy.get_axis(i))
                    except Exception:
                        axes_raw.append(0.0)

                # ── Tlačítka ──────────────────────────────────────────────
                btns_set = self.viz_state["buttons"]
                for idx, vg_btn in _PG_BTN_MAP.items():
                    if idx >= n_btns: continue
                    try:
                        pressed = bool(joy.get_button(idx))
                    except Exception:
                        pressed = False
                    was = self._prev_btn_state.get(idx, False)
                    if pressed != was:
                        self._prev_btn_state[idx] = pressed
                        code = f"btn_{idx}"
                        btns_set = (btns_set | {code}) if pressed else (btns_set - {code})
                        (gp.press_button if pressed else gp.release_button)(button=vg_btn)
                        self._log(f"🔘 {_PG_BTN_LABEL.get(idx,str(idx))} {'▼' if pressed else '▲'}", "btn")
                        dirty = True

                # ── D-pad (hat) ───────────────────────────────────────────
                if n_hats > 0:
                    try:
                        hx, hy = joy.get_hat(0)
                    except Exception:
                        hx = hy = 0
                    prev_hx = self._prev_btn_state.get("hx", 0)
                    prev_hy = self._prev_btn_state.get("hy", 0)
                    if hx != prev_hx:
                        self._prev_btn_state["hx"] = hx
                        gp.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
                        gp.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
                        if hx < 0: gp.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
                        elif hx > 0: gp.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT)
                        dirty = True
                    if hy != prev_hy:
                        self._prev_btn_state["hy"] = hy
                        gp.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
                        gp.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
                        if hy > 0: gp.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP)
                        elif hy < 0: gp.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
                        dirty = True

                # ── Zpracování axis z batche ─────────────────────────────
                raw_lx = clamp1(axes_raw[ax_lx])
                raw_ly = -clamp1(axes_raw[ax_ly])
                raw_rx = clamp1(axes_raw[ax_rx])
                raw_ry = -clamp1(axes_raw[ax_ry])
                if _lt_ax >= 0 and _lt_ax < len(axes_raw):
                    raw_v = _nan_safe(axes_raw[_lt_ax])
                    if _mode == "separate":
                        denom = 1.0 - _lt_rest
                        lt_raw = 0.0 if denom < 1e-6 else max(0.0, min(1.0, (raw_v - _lt_rest) / denom))
                    else:
                        denom = _lt_rest + 1.0
                        lt_raw = 0.0 if denom < 1e-6 else max(0.0, min(1.0, (_lt_rest - raw_v) / denom))
                else:
                    lt_raw = 0.0
                if _rt_ax >= 0 and _rt_ax < len(axes_raw):
                    raw_v = _nan_safe(axes_raw[_rt_ax])
                    # RT always positive-direction from rest (shared: positive half, separate: 0→1)
                    denom = 1.0 - _rt_rest
                    rt_raw = 0.0 if denom < 1e-6 else max(0.0, min(1.0, (raw_v - _rt_rest) / denom))
                else:
                    rt_raw = 0.0

                # ── Levý stick ────────────────────────────────────────────
                if snap.pt_left_x and snap.pt_left_y:
                    xo, yo = raw_lx, raw_ly
                else:
                    xo = self._process_stick_axis(raw_lx, snap.lut_left_x, snap.left_deadzone, snap.pt_left_x)
                    yo = self._process_stick_axis(raw_ly, snap.lut_left_y, snap.left_deadzone, snap.pt_left_y)
                    if snap.invert_left_y: yo = -yo

                if abs(xo - self._prev_lx) > 5e-4 or abs(yo - self._prev_ly) > 5e-4:
                    self._prev_lx = xo; self._prev_ly = yo
                    try:
                        gp.left_joystick(x_value=to_vg_axis(xo), y_value=to_vg_axis(yo))
                    except Exception as e:
                        self._log(f"⚠️ L-stick gp: {e}", "err")
                    self._log_axis("lx", f"🕹️  L  X={xo:+.3f} Y={yo:+.3f}")
                    dirty = True

                # ── Pravý stick ───────────────────────────────────────────
                if snap.pt_right_x and snap.pt_right_y:
                    xo2, yo2 = raw_rx, raw_ry
                else:
                    xo2 = self._process_stick_axis(raw_rx, snap.lut_right_x, snap.right_deadzone, snap.pt_right_x)
                    yo2 = self._process_stick_axis(raw_ry, snap.lut_right_y, snap.right_deadzone, snap.pt_right_y)
                    if snap.invert_right_y: yo2 = -yo2

                if abs(xo2 - self._prev_rx) > 5e-4 or abs(yo2 - self._prev_ry) > 5e-4:
                    self._prev_rx = xo2; self._prev_ry = yo2
                    try:
                        gp.right_joystick(x_value=to_vg_axis(xo2), y_value=to_vg_axis(yo2))
                    except Exception as e:
                        self._log(f"⚠️ R-stick gp: {e}", "err")
                    self._log_axis("rx", f"🕹️  R  X={xo2:+.3f} Y={yo2:+.3f}")
                    dirty = True

                # ── Triggery ──────────────────────────────────────────────
                lt_out = self._process_trigger(lt_raw, snap.lut_lt, snap.trigger_deadzone, snap.pt_lt)
                rt_out = self._process_trigger(rt_raw, snap.lut_rt, snap.trigger_deadzone, snap.pt_rt)

                if abs(lt_out - self._prev_lt) > 5e-4:
                    self._prev_lt = lt_out
                    try:
                        gp.left_trigger(value=to_vg_trigger(lt_out))
                    except Exception as e:
                        self._log(f"⚠️ LT gp: {e}", "err")
                    self._log_axis("lt", f"🎚️  LT {lt_raw:.3f} → {lt_out:.3f}")
                    dirty = True

                if abs(rt_out - self._prev_rt) > 5e-4:
                    self._prev_rt = rt_out
                    try:
                        gp.right_trigger(value=to_vg_trigger(rt_out))
                    except Exception as e:
                        self._log(f"⚠️ RT gp: {e}", "err")
                    self._log_axis("rt", f"🎚️  RT {rt_raw:.3f} → {rt_out:.3f}")
                    dirty = True

            except Exception as e:
                self._log(f"⚠️ Loop error: {e}", "err")

            # ── Consolidated viz_state update (vždy, pod jedním zámkem) ─────
            with self._viz_lock:
                self.viz_state["lx"] = xo; self.viz_state["ly"] = yo
                self.viz_state["raw_lx"] = raw_lx; self.viz_state["raw_ly"] = raw_ly
                self.viz_state["rx"] = xo2; self.viz_state["ry"] = yo2
                self.viz_state["raw_rx"] = raw_rx; self.viz_state["raw_ry"] = raw_ry
                self.viz_state["lt"] = lt_out
                self.viz_state["rt"] = rt_out
                self.viz_state["raw_lt"] = lt_raw
                self.viz_state["raw_rt"] = rt_raw
                self.viz_state["buttons"] = btns_set

            # ── Heartbeat – stavový dump každou ~1s ──
            self._hb_count = getattr(self, "_hb_count", 0) + 1
            if self._hb_count >= 500:
                self._hb_count = 0
                with self._viz_lock:
                    hb = dict(self.viz_state)
                self._log(f"💓 ax4={hb['raw_lt']:.3f} ax5={hb['raw_rt']:.3f}  LT→{hb['lt']:.3f}  RT→{hb['rt']:.3f}  buttons={len(hb['buttons'])}", "info")

            # ── Force keepalive gp.update každých 200ms i když není dirty ──
            self._ka_count = getattr(self, "_ka_count", 0) + 1
            if self._ka_count >= 100:
                self._ka_count = 0
                if not dirty:
                    try: gp.update()
                    except Exception: pass

            if dirty:
                try:
                    gp.update()
                except Exception as e:
                    self._log(f"⚠️ gp.update: {e}", "err")

            time.sleep(self._POLL_INTERVAL)

# ─── Hlavní GUI ───────────────────────────────────────────────────────────────

class RemapperGUI(ctk.CTk):

    def __init__(self):
        super().__init__()
        self.title("Xbox Controller Tweaks")
        self.geometry("760x1060"); self.minsize(700, 850)
        self.remapper       = None
        self._debug_enabled = True
        self.config         = default_config()
        self.curve_panels   = {}
        self.dz_sliders     = {}
        self.dz_labels      = {}
        self._viz_running   = False
        self._viz_gen       = 0
        self._log_buffer    = []
        self._profiles      = load_all_profiles()
        self._active_slot   = PROFILE_SLOTS[0]
        _app_cfg            = load_app_settings()
        self._tray_icon     = None
        self._tray_minimize = _app_cfg.get("tray_minimize", False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._refresh_controllers()
        if _TRAY_AVAILABLE: self._start_tray()
        self._start_log_ticker()

    def _build_ui(self):
        hdr=ctk.CTkFrame(self,corner_radius=0,height=80)
        hdr.pack(fill="x"); hdr.pack_propagate(False)
        row1=ctk.CTkFrame(hdr,fg_color="transparent"); row1.pack(fill="x",padx=14,pady=(10,2))
        ctk.CTkLabel(row1,text="🎮  Xbox Controller Tweaks",font=ctk.CTkFont(size=16,weight="bold")).pack(side="left")
        hs=ctk.CTkFrame(row1,fg_color="transparent"); hs.pack(side="right")
        self.btn_start=ctk.CTkButton(hs,text="▶  Start",width=100,height=28,command=self.start); self.btn_start.pack(side="left",padx=(0,6))
        self.btn_stop=ctk.CTkButton(hs,text="⏹  Stop",width=88,height=28,fg_color="transparent",border_width=1,state="disabled",command=self.stop); self.btn_stop.pack(side="left")
        row2=ctk.CTkFrame(hdr,fg_color="transparent"); row2.pack(fill="x",padx=14,pady=(0,6))
        ctk.CTkLabel(row2,text="Profile:",font=ctk.CTkFont(size=11),text_color="gray").pack(side="left",padx=(0,6))
        self._slot_var=ctk.StringVar(value=self._active_slot)
        ctk.CTkSegmentedButton(row2,values=PROFILE_SLOTS,variable=self._slot_var,width=220,height=26,command=self._switch_slot).pack(side="left")
        self._save_btn=ctk.CTkButton(row2,text="💾",width=28,height=26,fg_color="transparent",border_width=1,command=self._save_slot); self._save_btn.pack(side="left",padx=(4,0))
        if _TRAY_AVAILABLE:
            self._tray_min_var=ctk.BooleanVar(value=self._tray_minimize)
            ctk.CTkSwitch(row2,text="Stay in tray",width=110,height=20,variable=self._tray_min_var,font=ctk.CTkFont(size=11),command=self._toggle_tray_behavior).pack(side="right",padx=(8,0))
        ctk.CTkLabel(row2,text="powered by ViGEmBus",font=ctk.CTkFont(size=10),text_color="gray").pack(side="right",padx=(0,8))

        self.scroll=ctk.CTkScrollableFrame(self,corner_radius=0); self.scroll.pack(fill="both",expand=True)
        M=self.scroll

        vc=ctk.CTkFrame(M,corner_radius=10); vc.pack(fill="x",padx=16,pady=(12,6))
        vh=ctk.CTkFrame(vc,fg_color="transparent"); vh.pack(fill="x",padx=14,pady=(10,4))
        ctk.CTkLabel(vh,text="📡  Input Visualizer",font=ctk.CTkFont(size=13,weight="bold")).pack(side="left")
        ctk.CTkLabel(vh,text="(active while running)",font=ctk.CTkFont(size=10),text_color="gray").pack(side="left",padx=8)
        self._visualizer=InputVisualizer(vc); self._visualizer.pack(fill="x",padx=6,pady=(0,10))

        cc=ctk.CTkFrame(M,corner_radius=10); cc.pack(fill="x",padx=16,pady=(12,6))
        ctk.CTkLabel(cc,text="Controller",font=ctk.CTkFont(size=13,weight="bold")).grid(row=0,column=0,columnspan=3,sticky="w",padx=14,pady=(10,4))
        ctk.CTkLabel(cc,text="Input device",width=130,anchor="w").grid(row=1,column=0,padx=14,pady=4)
        self.ctrl_var=ctk.StringVar(value="")
        self.ctrl_combo=ctk.CTkOptionMenu(cc,variable=self.ctrl_var,values=["– searching –"],width=320); self.ctrl_combo.grid(row=1,column=1,padx=6,pady=4)
        ctk.CTkButton(cc,text="↻",width=36,command=self._refresh_controllers).grid(row=1,column=2,padx=(0,14))
        ctk.CTkLabel(cc,text="Output device",width=130,anchor="w").grid(row=2,column=0,padx=14,pady=(0,10))
        ctk.CTkLabel(cc,text="● Xbox 360 Virtual  (ViGEmBus)",text_color="#3fb950",font=ctk.CTkFont(size=11)).grid(row=2,column=1,sticky="w",padx=6,pady=(0,10))

        dz=ctk.CTkFrame(M,corner_radius=10); dz.pack(fill="x",padx=16,pady=6)
        dzh=ctk.CTkFrame(dz,fg_color="transparent"); dzh.pack(fill="x",padx=14,pady=(10,4))
        ctk.CTkLabel(dzh,text="Deadzone",font=ctk.CTkFont(size=13,weight="bold")).pack(side="left")
        ctk.CTkButton(dzh,text="↺ Reset",width=80,height=28,fg_color="transparent",border_width=1,command=self._reset_deadzone).pack(side="right")
        for label,key,default in [("Left stick","left_deadzone",0.02),("Right stick","right_deadzone",0.02),("LT/RT triggers","trigger_deadzone",0.01)]:
            r=ctk.CTkFrame(dz,fg_color="transparent"); r.pack(fill="x",padx=14,pady=3)
            ctk.CTkLabel(r,text=label,width=120,anchor="w").pack(side="left")
            var=ctk.DoubleVar(value=default)
            lbl=ctk.CTkLabel(r,text=f"{default:.3f}",width=50,font=ctk.CTkFont(family="Consolas",size=11))
            ctk.CTkSlider(r,from_=0.0,to=0.3,variable=var,
                          command=lambda v,k=key,l=lbl:(l.configure(text=f"{round(float(v),4):.3f}"),self._cfg_set(k,round(float(v),4)))
                          ).pack(side="left",fill="x",expand=True,padx=6)
            lbl.pack(side="left"); self.dz_sliders[key]=var; self.dz_labels[key]=lbl
        ctk.CTkFrame(dz,fg_color="transparent",height=6).pack()

        rc=ctk.CTkFrame(M,corner_radius=10); rc.pack(fill="x",padx=16,pady=6)
        rh=ctk.CTkFrame(rc,fg_color="transparent"); rh.pack(fill="x",padx=14,pady=(10,6))
        ctk.CTkLabel(rh,text="📳  Rumble",font=ctk.CTkFont(size=13,weight="bold")).pack(side="left")
        ctk.CTkButton(rh,text="↺ Reset",width=80,height=28,fg_color="transparent",border_width=1,command=self._reset_rumble).pack(side="right")
        rt2=ctk.CTkFrame(rc,fg_color="transparent"); rt2.pack(fill="x",padx=14,pady=(0,4))
        ctk.CTkLabel(rt2,text="Rumble forwarding",width=160,anchor="w").pack(side="left")
        self.rumble_enabled_var=ctk.BooleanVar(value=True)
        ctk.CTkSwitch(rt2,text="",variable=self.rumble_enabled_var,onvalue=True,offvalue=False,
                      command=lambda:self._cfg_set("rumble_enabled",self.rumble_enabled_var.get())).pack(side="left")
        ctk.CTkLabel(rt2,text="Game → virtual Xbox → physical controller",font=ctk.CTkFont(size=10),text_color="gray").pack(side="left",padx=12)
        rm=ctk.CTkFrame(rc,fg_color="transparent"); rm.pack(fill="x",padx=14,pady=(0,12))
        ctk.CTkLabel(rm,text="Intensity",width=160,anchor="w").pack(side="left")
        self.rumble_mult_var=ctk.DoubleVar(value=1.0)
        self.rumble_mult_lbl=ctk.CTkLabel(rm,text="1.00",width=40,font=ctk.CTkFont(family="Consolas",size=11))
        ctk.CTkSlider(rm,from_=0.0,to=2.0,variable=self.rumble_mult_var,command=self._rumble_mult_changed).pack(side="left",fill="x",expand=True,padx=6)
        self.rumble_mult_lbl.pack(side="left")
        dl=ctk.CTkFrame(rc,fg_color="transparent"); dl.pack(fill="x",padx=14,pady=(0,10))
        ctk.CTkLabel(dl,text="● XInput available" if _XINPUT else "⚠️  XInput DLL not found",
                     text_color="#3fb950" if _XINPUT else "#ff9800",font=ctk.CTkFont(size=10)).pack(side="left")
        ctk.CTkButton(dl,text="📳 Test",width=80,height=26,command=self._test_rumble).pack(side="right")

        for sec_title,sec_key,axes,inv_key,inv_attr in [
            ("Left stick","left",[("X axis","curve_left_x","mode_left_x","left_x_mult"),("Y axis","curve_left_y","mode_left_y","left_y_mult")],"invert_left_y","inv_left_var"),
            ("Right stick","right",[("X axis","curve_right_x","mode_right_x","right_x_mult"),("Y axis","curve_right_y","mode_right_y","right_y_mult")],"invert_right_y","inv_right_var"),
            ("Triggers","trigger",[("LT (left)","curve_lt","mode_lt","trigger_mult"),("RT (right)","curve_rt","mode_rt","trigger_mult")],None,None),
        ]:
            card=ctk.CTkFrame(M,corner_radius=10); card.pack(fill="x",padx=16,pady=6)
            hr=ctk.CTkFrame(card,fg_color="transparent"); hr.pack(fill="x",padx=14,pady=(10,6))
            ctk.CTkLabel(hr,text=sec_title,font=ctk.CTkFont(size=13,weight="bold")).pack(side="left")
            ctk.CTkButton(hr,text="↺ Reset",width=80,height=28,fg_color="transparent",border_width=1,command=lambda s=sec_key:self._reset_section(s)).pack(side="right")
            pr=ctk.CTkFrame(card,fg_color="transparent"); pr.pack(fill="x",padx=10,pady=(0,4))
            for ax_label,ck,cm,cmult in axes:
                panel=CurvePanel(pr,ax_label,ck,cm,cmult,self.config,self._cfg_set)
                panel.pack(side="left",padx=4,pady=4,fill="y"); self.curve_panels[ck]=panel
            if inv_key:
                ir=ctk.CTkFrame(card,fg_color="transparent"); ir.pack(fill="x",padx=14,pady=(0,10))
                ctk.CTkLabel(ir,text="Invert Y axis").pack(side="left",padx=(0,10))
                var=ctk.BooleanVar(value=self.config[inv_key]); setattr(self,inv_attr,var)
                ctk.CTkSwitch(ir,text="",variable=var,onvalue=True,offvalue=False,
                              command=lambda k=inv_key,v=var:self._cfg_set(k,v.get())).pack(side="left")
            else: ctk.CTkFrame(card,fg_color="transparent",height=10).pack()

        io=ctk.CTkFrame(M,fg_color="transparent"); io.pack(fill="x",padx=16,pady=6)
        ctk.CTkButton(io,text="💾  Export profile",width=150,command=self._export).pack(side="left",padx=(0,8))
        ctk.CTkButton(io,text="📂  Import profile",width=150,command=self._import).pack(side="left")
        ctk.CTkButton(io,text="↺  Reset all",width=120,fg_color="transparent",border_width=1,hover_color="#3a1a1a",command=self._reset_all).pack(side="right")

        sc=ctk.CTkFrame(M,corner_radius=10); sc.pack(fill="x",padx=16,pady=6)
        si=ctk.CTkFrame(sc,fg_color="transparent"); si.pack(fill="x",padx=14,pady=10)
        self.status_dot=ctk.CTkLabel(si,text="●",font=ctk.CTkFont(size=16),text_color="gray",width=20); self.status_dot.pack(side="left")
        self.status_var=ctk.StringVar(value="Stopped")
        ctk.CTkLabel(si,textvariable=self.status_var,font=ctk.CTkFont(size=12)).pack(side="left",padx=8)

        dc=ctk.CTkFrame(M,corner_radius=10); dc.pack(fill="x",padx=16,pady=(6,16))
        dh=ctk.CTkFrame(dc,fg_color="transparent"); dh.pack(fill="x",padx=14,pady=(10,6))
        ctk.CTkLabel(dh,text="Log",font=ctk.CTkFont(size=13,weight="bold")).pack(side="left")
        self.dbg_var=ctk.BooleanVar(value=True)
        ctk.CTkSwitch(dh,text="Axis",variable=self.dbg_var,width=80,
                      command=lambda:setattr(self,'_debug_enabled',self.dbg_var.get())).pack(side="left",padx=16)
        ctk.CTkButton(dh,text="🗑",width=36,height=28,fg_color="transparent",border_width=1,
                      command=self._clear_log).pack(side="right")
        self.log_box=tk.Text(dc,height=10,font=("Consolas",10),
                             bg="#0d0d1a",fg="#c0c0d0",insertbackground="white",
                             relief="flat",bd=0,state="disabled",wrap="none")
        self.log_box.pack(fill="x",padx=14,pady=(0,14))
        # Barevné tagy
        self.log_box.tag_config("axis",  foreground="#4a6a8a")   # tmavě modré – axis spam
        self.log_box.tag_config("btn",   foreground="#50fa7b")   # zelená – tlačítka
        self.log_box.tag_config("info",  foreground="#8be9fd")   # cyan – info
        self.log_box.tag_config("ok",    foreground="#50fa7b")   # zelená – OK
        self.log_box.tag_config("warn",  foreground="#ffb86c")   # oranžová – varování
        self.log_box.tag_config("err",   foreground="#ff5555")   # červená – chyba
        self.log_box.tag_config("rumble",foreground="#bd93f9")   # fialová – vibrace
        self.log_box.tag_config("ts",    foreground="#44475a")   # šedá – timestamp

    def _cfg_set(self,key,value):
        self.config[key]=value
        if self.remapper: self.remapper.update_config(self.config)

    def _refresh_controllers(self):
        if self.remapper:
            # Při běhu remapperu jen aktualizujeme label – joystick subsystem nesmíme reinicializovat
            self._log("ℹ️  Controllers cannot be refreshed while running – stop the remapper first", "warn")
            return
        pads=get_gamepads()
        if pads:
            names=[f"#{i}  {n}" for i,n in pads]
            self.ctrl_combo.configure(values=names); self.ctrl_var.set(names[0])
            self._log(f"Found {len(pads)} controller(s)")
        else:
            self.ctrl_combo.configure(values=["– no controller –"]); self.ctrl_var.set("– no controller –")
            self._log("⚠️  No controller found")

    def _selected_index(self):
        val=self.ctrl_var.get()
        try: return int(val.split()[0].replace("#",""))
        except: return 0

    def start(self):
        pads=get_gamepads()
        if not pads: messagebox.showerror("Error","No controller found"); return
        try:
            idx=self._selected_index(); name=pads[idx][1] if idx<len(pads) else "?"
            self.remapper=ControllerRemapper(self.config,idx,log_callback=self._log)
            self.remapper.start()
            self.status_var.set(f"Running  ·  #{idx} {name}  →  Xbox 360 Virtual")
            self.status_dot.configure(text_color="#3fb950")
            self.btn_start.configure(state="disabled"); self.btn_stop.configure(state="normal")
            self.ctrl_combo.configure(state="disabled")
            self._log(f"✅ Start – #{idx} {name} → Xbox 360 Virtual")
            self._tray_update(True); self._start_viz_ticker()
        except Exception as e: messagebox.showerror("Error",str(e)); self._log(f"❌ {e}")

    def stop(self):
        if self.remapper: self.remapper.stop(); self.remapper=None
        self._viz_gen+=1   # ukončí starý _tick loop
        self._viz_running=False
        self.status_var.set("Stopped"); self.status_dot.configure(text_color="gray")
        self.btn_start.configure(state="normal"); self.btn_stop.configure(state="disabled")
        self.ctrl_combo.configure(state="normal"); self._log("⏹ Stopped"); self._tray_update(False)

    def _reset_deadzone(self):
        d=default_config()
        for k in ("left_deadzone","right_deadzone","trigger_deadzone"):
            self.dz_sliders[k].set(d[k]); self.dz_labels[k].configure(text=f"{d[k]:.3f}"); self._cfg_set(k,d[k])
        self._log("↺ Deadzone reset")

    def _reset_rumble(self):
        self.rumble_enabled_var.set(True); self.rumble_mult_var.set(1.0); self.rumble_mult_lbl.configure(text="1.00")
        self._cfg_set("rumble_enabled",True); self._cfg_set("rumble_mult",1.0); self._log("↺ Rumble reset")

    def _test_rumble(self):
        if _XINPUT is None: self._log("⚠️  XInput DLL not found"); return
        idx=self.remapper._xinput_index if self.remapper else _find_xinput_index(0)
        mult=min(2.0, self.rumble_mult_var.get())
        strength=int(min(1.0, 0.5 * mult) * 65535)
        self._log(f"📳 Rumble test → XInput slot {idx}  (mult={mult:.2f}, strength={strength})")
        try:
            _XINPUT.XInputSetState(idx,ctypes.byref(_XINPUT_VIBRATION(strength,strength)))
            self.after(600,lambda:(_XINPUT.XInputSetState(idx,ctypes.byref(_XINPUT_VIBRATION(0,0))),self._log("📳 Test complete")))
        except Exception as e: self._log(f"⚠️  {e}")

    def _reset_section(self,section):
        if section=="left":
            for k in ("curve_left_x","curve_left_y"): self.curve_panels[k].reset_to_default()
            self.inv_left_var.set(False); self._cfg_set("invert_left_y",False)
        elif section=="right":
            for k in ("curve_right_x","curve_right_y"): self.curve_panels[k].reset_to_default()
            self.inv_right_var.set(False); self._cfg_set("invert_right_y",False)
        elif section=="trigger":
            for k in ("curve_lt","curve_rt"): self.curve_panels[k].reset_to_default()
        self._log(f"↺ Section '{section}' reset")

    def _reset_all(self):
        self.config=default_config(); self._reset_deadzone(); self._reset_rumble()
        for s in ("left","right","trigger"): self._reset_section(s)
        if self.remapper: self.remapper.update_config(self.config)
        self._log("↺ Everything reset")

    def _start_viz_ticker(self):
        self._viz_running=True
        self._viz_gen+=1
        gen=self._viz_gen
        self._tick(gen)

    def _tick(self,gen):
        if not self._viz_running or gen!=self._viz_gen: return
        try:
            if self.remapper:
                self._visualizer.set_deadzone(self.config["left_deadzone"],self.config["right_deadzone"])
                with self.remapper._viz_lock:
                    state = dict(self.remapper.viz_state)  # snapshot pod lockem
                self._visualizer.update_state(state)
        except Exception as e:
            self._log(f"⚠️ Viz ticker: {e}", "err")
        self.after(16,self._tick,gen)

    def _start_log_ticker(self):
        self._flush_log()

    def _flush_log(self):
        if self._log_buffer:
            buf,self._log_buffer=self._log_buffer[:],[]
            self.log_box.configure(state="normal")
            for ts,msg,tag in buf:
                self.log_box.insert("end",ts,"ts")
                self.log_box.insert("end",msg+"\n",tag)
            lines=int(self.log_box.index("end").split(".")[0])
            if lines>400: self.log_box.delete("1.0",f"{lines-400}.0")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.after(16,self._flush_log)

    def _save_slot(self):
        self._profiles[self._active_slot]=copy.deepcopy(self.config); save_all_profiles(self._profiles)
        self._log(f"💾 Profile saved to '{self._active_slot}'")
        self._save_btn.configure(fg_color="#1a5a1a",text="✓")
        self.after(600,lambda:self._save_btn.configure(fg_color="transparent",text="💾"))

    def _load_config_into_ui(self):
        for k in ("left_deadzone","right_deadzone","trigger_deadzone"):
            self.dz_sliders[k].set(self.config[k]); self.dz_labels[k].configure(text=f"{self.config[k]:.3f}")
        for k,p in self.curve_panels.items(): p.load_from_config(self.config)
        self.inv_left_var.set(self.config["invert_left_y"]); self.inv_right_var.set(self.config["invert_right_y"])
        self.rumble_enabled_var.set(self.config.get("rumble_enabled",True))
        self.rumble_mult_var.set(self.config.get("rumble_mult",1.0))
        self.rumble_mult_lbl.configure(text=f"{self.config.get('rumble_mult',1.0):.2f}")

    def _switch_slot(self,slot):
        self._active_slot=slot
        if slot in self._profiles:
            base=default_config(); base.update(self._profiles[slot]); self.config=base
            self._load_config_into_ui()
            if self.remapper: self.remapper.update_config(self.config)
            self._log(f"📂 Switched to '{slot}'")
        else:
            self.config=default_config()
            self._load_config_into_ui()
            if self.remapper: self.remapper.update_config(self.config)
            self._log(f"ℹ️  Slot '{slot}' is empty – loaded the default profile")

    def _make_tray_image(self,active=False):
        img=Image.new("RGBA",(64,64),(0,0,0,0)); d=ImageDraw.Draw(img)
        d.ellipse([8,8,56,56],fill=(63,185,80) if active else (120,120,120)); d.ellipse([20,20,44,44],fill=(20,20,30)); return img

    def _start_tray(self):
        menu=pystray.Menu(pystray.MenuItem("Show",self._tray_show,default=True),
                          pystray.MenuItem("Start remapper",self._tray_start),
                          pystray.MenuItem("Stop remapper",self._tray_stop),
                          pystray.Menu.SEPARATOR,pystray.MenuItem("Exit",self._tray_quit))
        self._tray_icon=pystray.Icon("xbox_remapper",self._make_tray_image(False),"Xbox Controller Tweaks – stopped",menu)
        threading.Thread(target=self._tray_icon.run,daemon=True).start()

    def _tray_show(self,*_): self.after(0,self.deiconify); self.after(0,self.lift)
    def _tray_start(self,*_): self.after(0,self.start)
    def _tray_stop(self,*_): self.after(0,self.stop)
    def _tray_quit(self,*_): self.after(0,self._quit_app)

    def _tray_update(self,active:bool):
        if not _TRAY_AVAILABLE or not self._tray_icon: return
        self._tray_icon.icon=self._make_tray_image(active)
        self._tray_icon.title=f"Xbox Controller Tweaks – {'running ▶' if active else 'stopped'}"

    def _toggle_tray_behavior(self):
        self._tray_minimize=self._tray_min_var.get(); save_app_settings({"tray_minimize":self._tray_minimize})
        self._log(f"🔔 Close behavior: {'minimize to tray' if self._tray_minimize else 'exit'}")

    def _on_close(self):
        if _TRAY_AVAILABLE and self._tray_icon and self._tray_minimize: self.withdraw(); self._log("🔽 Systray")
        else: self._quit_app()

    def _quit_app(self):
        self.stop()
        if self._tray_icon:
            try: self._tray_icon.stop()
            except Exception: pass
        self.destroy()

    def _rumble_mult_changed(self,val):
        fval=round(float(val),2); self.rumble_mult_lbl.configure(text=f"{fval:.2f}"); self._cfg_set("rumble_mult",fval)

    def _export(self):
        path=filedialog.asksaveasfilename(defaultextension=".json",filetypes=[("JSON profile","*.json"),("All files","*.*")],title="Save profile")
        if not path: return
        try:
            with open(path,"w",encoding="utf-8") as f: json.dump(self.config,f,indent=2,ensure_ascii=False)
            self._log(f"💾 Saved: {path}")
        except Exception as e: messagebox.showerror("Export",str(e))

    def _import(self):
        path=filedialog.askopenfilename(filetypes=[("JSON profile","*.json"),("All files","*.*")],title="Load profile")
        if not path: return
        try:
            with open(path,"r",encoding="utf-8") as f: loaded=json.load(f)
            base=default_config(); base.update(loaded); self.config=base
            self._load_config_into_ui()
            if self.remapper: self.remapper.update_config(self.config)
            self._log(f"📂 Loaded: {path}")
        except Exception as e: messagebox.showerror("Import",str(e))

    def _log(self, msg: str, tag: str = ""):
        # Automatická detekce tagu pokud není explicitně zadán
        if not tag:
            m = msg
            if any(x in m for x in ("🕹️", "🎚️  LT", "🎚️  RT", "L  X", "R  X")):
                tag = "axis"
            elif any(x in m for x in ("🔘", "▼", "▲")):
                tag = "btn"
            elif any(x in m for x in ("✅", "↺", "💾", "📂", "ℹ️")):
                tag = "ok"
            elif any(x in m for x in ("⚠️",)):
                tag = "warn"
            elif any(x in m for x in ("❌",)):
                tag = "err"
            elif any(x in m for x in ("📳", "Rumble")):
                tag = "rumble"
            else:
                tag = "info"
        # Axis zprávy filtrujeme pokud je debug vypnutý
        if tag == "axis" and not self._debug_enabled:
            return
        ts = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] "
        self._log_buffer.append((ts, msg, tag))

    def _clear_log(self):
        self.log_box.configure(state="normal"); self.log_box.delete("1.0","end"); self.log_box.configure(state="disabled")


if __name__=="__main__":
    app=RemapperGUI()
    app.mainloop()

# Created by FajkPes with assistance from ChatGPT Codex and Claude Code.
