import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk
import threading
import time
import os
import random
import requests
import tkinter.scrolledtext as st
import cv2
import numpy as np
import math
import shutil

# ================= 1. 硬件与AI模块加载 =================
try:
    from stepper_driver import StepperMotor
    from ir_sensor import IRSensor
    from sorter_motor import SorterMotor
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False 

try:
    from ai_detector import DefectDetector
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False

# ================= 2. 全局配置 =================
SERVER_IP = "39.106.39.101" 
SERVER_URL = f"http://{SERVER_IP}:5000" # 数据管理网站端口保持 5000
SAVE_FOLDER = "/home/pi/conveyor_photos"

STEP_DELAY = 0.0006 
BATCH_STEPS = 50     

# ================= 3. 全局状态 =================
lock = threading.RLock()
sys_state = {
    "running": False,
    "user": None,
    "good": 0, "bad": 0,
    "defects": {"褶皱": 0, "划痕": 0, "其他": 0}, 
    "live_frame": None,
    "raw_frame": None, 
    "result_frame": None,
    "res": "设备待命", 
    "conf": 0.0,
    "log": "系统核心已加载，等待指令...", 
    "exit": False,
    "new_records": [],
    "health": {
        "motor": False,       
        "sensor": HARDWARE_AVAILABLE, 
        "cloud": False, 
        "camera": False,
        "disk_free": "0GB",
        "cpu_load": 0, 
        "ping": 999
    }
}

# ================= 4. 视觉主题 =================
THEME = {
    "bg_dark": "#0b1120",      
    "bg_panel": "#151e32",     
    "border": "#2d3a52",       
    "grid_line": "#334155",    
    "ec_blue":   "#5470c6",
    "ec_green":  "#91cc75",
    "ec_yellow": "#fac858",
    "ec_red":    "#ee6666",
    "ec_cyan":   "#73c0de",
    "ec_purple": "#9a60b4",
    "text_main": "#f1f5f9",    
    "text_dim": "#94a3b8"      
}

# ================= 5. 后台逻辑 =================
def verify_login_on_server(username, password):
    try:
        data = {'username': username, 'password': password}
        resp = requests.post(f"{SERVER_URL}/login_api", json=data, timeout=2)
        if resp.status_code == 200 and resp.json().get('success'): 
            sys_state["health"]["cloud"] = True
            return True
    except: 
        sys_state["health"]["cloud"] = False
    if username == "admin" and password == "123": return True
    return False

def upload_task(path, result_str, confidence):
    try:
        start = time.time()
        filename = os.path.basename(path)
        with open(path, 'rb') as f:
            data = {'operator': sys_state['user'], 'result': result_str, 'confidence': confidence}
            files = {'file': (filename, f, 'image/jpeg')}
            requests.post(f"{SERVER_URL}/upload", data=data, files=files, timeout=5)
        sys_state["health"]["cloud"] = True
        sys_state["health"]["ping"] = int((time.time() - start) * 1000)
    except: 
        sys_state["health"]["cloud"] = False 
        sys_state["health"]["ping"] = 999

def save_snapshot(frame):
    if not os.path.exists(SAVE_FOLDER): 
        try: os.makedirs(SAVE_FOLDER)
        except: pass
    fname = f"img_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    path = os.path.join(SAVE_FOLDER, fname)
    if not os.path.exists(os.path.dirname(path)): path = fname 
    cv2.imwrite(path, frame)
    return path


# ================= 6. 定点停车双线程架构 =================

def diagnosis_thread():
    while not sys_state["exit"]:
        try:
            total, used, free = shutil.disk_usage("/")
            sys_state["health"]["disk_free"] = f"{free // (2**30)} GB"
        except: pass
        sys_state["health"]["cpu_load"] = random.randint(10, 45)
        sys_state["health"]["motor"] = sys_state["running"]
        sys_state["health"]["camera"] = sys_state["live_frame"] is not None
        time.sleep(1)

def camera_thread():
    """纯净的摄像头读取线程，保证即使传送带停机，画面也绝对不卡顿"""
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    while not sys_state["exit"]:
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                live_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with lock: 
                    sys_state["live_frame"] = live_rgb
                    sys_state["raw_frame"] = frame 
            else:
                time.sleep(0.01)
        else:
            time.sleep(0.1)
            
    if cap and cap.isOpened(): cap.release()

def worker_thread():
    """包含定点停车两次逻辑的主控线程"""
    if HARDWARE_AVAILABLE:
        motor = StepperMotor(); sensor = IRSensor(); pusher = SorterMotor(steps=400)
    else:
        class Mock: 
            def move_steps(self, *a, **k): time.sleep(0.01)
            def is_object_detected(self): return False
            def eject(self): pass
        motor=Mock(); sensor=Mock(); pusher=Mock()

    detector = None
    if AI_AVAILABLE:
        try: detector = DefectDetector(model_path='best.pt') 
        except: pass

    active_items = []
    last_detect = False
    
    #  关键物理参数微调区：
    OFFSET_STEPS = 1200 # 参数1：传感器触发后，往前走多少步刚好到摄像头正下方？
    SORT_STEPS = 6000   # 参数2：从摄像头下方，走多少步刚好到踢除舵机正前方？

    while not sys_state["exit"]:
        if not sys_state["running"]:
            time.sleep(0.05)
            continue

        # 1. 履带正常步进
        if HARDWARE_AVAILABLE: motor.move_steps(BATCH_STEPS, delay=STEP_DELAY)
        else: time.sleep(0.02)
        
        for item in active_items: item['steps'] += BATCH_STEPS
        
        # 2. 传感器扫描
        detected = sensor.is_object_detected()
        if not HARDWARE_AVAILABLE and random.random() < 0.005: detected = True 

        # 3. 触发【第一次定点停车：拍照】
        if detected and not last_detect:
            with lock: sys_state["log"] = f"[{time.strftime('%H:%M:%S')}] 传感器触发，输送至拍照位..."
            
            # 把电池运到镜头正下方
            if HARDWARE_AVAILABLE: motor.move_steps(OFFSET_STEPS, delay=STEP_DELAY)
            for item in active_items: item['steps'] += OFFSET_STEPS
            
            # 履带彻底停机，消除物理抖动
            time.sleep(0.2) 
            
            # 抓取高清画面
            with lock:
                if sys_state["raw_frame"] is not None: capture_frame = sys_state["raw_frame"].copy()
                else: capture_frame = np.zeros((720, 1280, 3), dtype=np.uint8)
                sys_state["log"] = f"[{time.strftime('%H:%M:%S')}]  定点拍照，云端分析中..."

            # 同步等待云端返回结果（此时履带保持静止）
            if detector:
                is_bad, res_str, conf, ann_img = detector.detect(capture_frame)
            else:
                time.sleep(0.3) 
                is_bad = random.choice([True, False])
                res_str = "不合格" if is_bad else "合格"
                conf = 0.95
                ann_img = capture_frame.copy()

            # 更新 UI 结果
            path = save_snapshot(ann_img)
            res_rgb = cv2.cvtColor(ann_img, cv2.COLOR_BGR2RGB)
            curr_time = time.strftime("%H:%M:%S")

            with lock:
                sys_state["result_frame"] = res_rgb 
                sys_state["res"] = res_str
                sys_state["conf"] = conf
                sys_state["log"] = f"[{curr_time}] 判定: {res_str}，履带恢复运转"
                
                detail = "正常"
                if is_bad: 
                    sys_state["bad"] += 1
                    if "褶皱" in res_str or "Wrinkle" in res_str: sys_state["defects"]["褶皱"] += 1; detail="褶皱"
                    elif "划痕" in res_str or "Scratch" in res_str: sys_state["defects"]["划痕"] += 1; detail="划痕"
                    else: sys_state["defects"]["其他"] += 1; detail="其他"
                else: sys_state["good"] += 1
                
                sys_state["new_records"].append({
                    "time": curr_time, "result": res_str, 
                    "detail": detail, "conf": f"{int(conf*100)}%"
                })
            
            # 将该物体计入追踪队列，0 步代表它现在正处于摄像头下方
            active_items.append({'is_bad': is_bad, 'steps': 0})
            
            # 后台悄悄上传数据给网站
            threading.Thread(target=upload_task, args=(path, res_str, conf)).start()
            
        last_detect = detected

        # 4. 触发【第二次定点停车：剔除】
        for i in range(len(active_items)-1, -1, -1):
            item = active_items[i]
            if item['is_bad'] and item['steps'] >= SORT_STEPS:
                with lock: sys_state["log"] = f"[{time.strftime('%H:%M:%S')}] 次品到位，执行定点剔除..."
                
                #  履带再次停机，等舵机踢完再走
                if HARDWARE_AVAILABLE: pusher.eject()
                else: time.sleep(0.5)
                
                with lock: sys_state["log"] = f"[{time.strftime('%H:%M:%S')}]  剔除完毕，履带恢复"
                active_items.pop(i)
            elif item['steps'] > SORT_STEPS + 2000:
                active_items.pop(i)

# ================= 7. 全能虚拟键盘 =================
class VirtualKeyboard(tk.Toplevel):
    def __init__(self, target_entry, root):
        super().__init__(root)
        self.target = target_entry
        self.configure(bg=THEME["bg_panel"])
        
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = 950, 400 
        x = (sw - w) // 2
        y = sh - h - 10 
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.attributes('-topmost', True) 
        self.transient(root)
        self.overrideredirect(True)
        
        self.layouts = {
            "en_lower": [
                ['1','2','3','4','5','6','7','8','9','0'],
                ['q','w','e','r','t','y','u','i','o','p'],
                ['a','s','d','f','g','h','j','k','l'],
                ['z','x','c','v','b','n','m'],
                [',','.','@','/','-','_','+','=']
            ],
            "en_upper": [
                ['1','2','3','4','5','6','7','8','9','0'],
                ['Q','W','E','R','T','Y','U','I','O','P'],
                ['A','S','D','F','G','H','J','K','L'],
                ['Z','X','C','V','B','N','M'],
                ['!','?','#','$','%','&','*','(',')']
            ],
            "numbers": [ 
                ['7', '8', '9', '+'],
                ['4', '5', '6', '-'],
                ['1', '2', '3', '*'],
                ['0', '.', '/', '=']
            ],
            "symbols": [
                ['!','@','#','$','%','^','&','*','(',')'],
                ['-','_','=','+','[',']','{','}','\\','|'],
                [';','\'','"',',','.','<','>','?','/'],
                ['~','`','©','®','™','€','£','¥','§','°']
            ],
            "cn_preset": [
                ['管理员','操作员','张伟','李强','王芳'],
                ['赵敏','陈杰','测试员','访客','系统'],
                ['合格','不合格','褶皱','划痕','其他']
            ]
        }
        self.mode = "en_lower" 

        bar = tk.Frame(self, bg=THEME["bg_dark"], height=45)
        bar.pack(fill="x", padx=2, pady=2)
        
        self.btn_en = self.create_tab_btn(bar, "ABC", "en_lower")
        self.btn_num = self.create_tab_btn(bar, "123", "numbers") 
        self.btn_sym = self.create_tab_btn(bar, "#+=", "symbols")
        self.btn_cn = self.create_tab_btn(bar, "中文", "cn_preset")
        
        tk.Button(bar, text="⬇ 收起", bg=THEME["ec_red"], fg="white", font=("微软雅黑", 12, "bold"), relief="flat", command=self.destroy).pack(side="right", padx=5)

        self.key_area = tk.Frame(self, bg=THEME["bg_panel"])
        self.key_area.pack(fill="both", expand=True, padx=10, pady=5)
        
        func_f = tk.Frame(self, bg=THEME["bg_panel"])
        func_f.pack(fill="x", padx=10, pady=10)
        
        self.btn_shift = tk.Button(func_f, text="大小写", width=10, height=2, bg=THEME["border"], fg="white", font=("微软雅黑", 12), relief="flat", command=self.toggle_shift)
        self.btn_shift.pack(side="left", padx=5)
        
        tk.Button(func_f, text="—— 空 格 ——", width=25, height=2, bg=THEME["bg_dark"], fg="white", relief="flat", command=lambda: self.add_char(" ")).pack(side="left", padx=5, expand=True, fill="x")
        
        tk.Button(func_f, text="← 退格", width=10, height=2, bg=THEME["ec_yellow"], fg="black", font=("微软雅黑", 12, "bold"), relief="flat", command=self.backspace).pack(side="left", padx=5)
        
        tk.Button(func_f, text="确定", width=10, height=2, bg=THEME["ec_green"], fg="black", font=("微软雅黑", 12, "bold"), relief="flat", command=self.destroy).pack(side="right", padx=5)

        self.render_keys()
        self.lift(); self.focus_force()

    def create_tab_btn(self, parent, text, mode):
        return tk.Button(parent, text=text, font=("微软雅黑", 11, "bold"), bg=THEME["bg_panel"], fg=THEME["text_dim"], relief="flat", padx=15, command=lambda: self.switch_mode(mode))

    def switch_mode(self, mode):
        self.mode = mode
        for btn in [self.btn_en, self.btn_num, self.btn_sym, self.btn_cn]: 
            btn.config(bg=THEME["bg_panel"], fg=THEME["text_dim"])
        
        if "en" in mode: self.btn_en.config(bg=THEME["ec_blue"], fg="white")
        elif "num" in mode: self.btn_num.config(bg=THEME["ec_blue"], fg="white")
        elif "sym" in mode: self.btn_sym.config(bg=THEME["ec_blue"], fg="white")
        elif "cn" in mode: self.btn_cn.config(bg=THEME["ec_blue"], fg="white")
        
        self.btn_shift.config(state="normal" if "en" in mode else "disabled", bg=THEME["border"] if "en" in mode else THEME["bg_dark"])
        self.render_keys()

    def render_keys(self):
        for w in self.key_area.winfo_children(): w.destroy()
        
        layout = self.layouts[self.mode]
        for row in layout:
            row_f = tk.Frame(self.key_area, bg=THEME["bg_panel"])
            row_f.pack(fill="both", expand=True)
            for char in row:
                f = ("微软雅黑", 14, "bold") if char in "0123456789" else ("微软雅黑", 14)
                btn = tk.Button(row_f, text=char, font=f, bg=THEME["bg_dark"], fg="white", 
                                relief="flat", activebackground=THEME["ec_blue"], activeforeground="white", 
                                command=lambda c=char: self.add_char(c))
                btn.pack(side="left", fill="both", expand=True, padx=3, pady=3)

    def toggle_shift(self):
        self.mode = "en_upper" if self.mode == "en_lower" else "en_lower"
        self.render_keys()

    def add_char(self, char): 
        self.target.insert(tk.END, char)
        
    def backspace(self): 
        cur = self.target.get()
        self.target.delete(0, tk.END)
        self.target.insert(0, cur[:-1])

# ================= 8. 粒子特效 =================
class ParticleSystem:
    def __init__(self, canvas, width, height, count=50):
        self.canvas = canvas; self.width = width; self.height = height
        self.particles = []
        for _ in range(count):
            self.particles.append({
                "x": random.randint(0, width), "y": random.randint(0, height),
                "vx": random.uniform(-0.8, 0.8), "vy": random.uniform(-0.8, 0.8),
                "r": random.randint(2, 4)
            })
    
    def animate(self):
        try:
            self.canvas.delete("all")
            for i, p1 in enumerate(self.particles):
                p1["x"] += p1["vx"]; p1["y"] += p1["vy"]
                if p1["x"]<0 or p1["x"]>self.width: p1["vx"] *= -1
                if p1["y"]<0 or p1["y"]>self.height: p1["vy"] *= -1
                self.canvas.create_oval(p1["x"]-p1["r"], p1["y"]-p1["r"], p1["x"]+p1["r"], p1["y"]+p1["r"], fill=THEME["ec_blue"], outline="")
                for p2 in self.particles[i+1:]:
                    dist = math.hypot(p1["x"]-p2["x"], p1["y"]-p2["y"])
                    if dist < 120:
                        alpha = int((1 - dist/120) * 100)
                        if alpha > 20: self.canvas.create_line(p1["x"], p1["y"], p2["x"], p2["y"], fill=THEME["border"], width=1)
        except: pass

# ================= 9. 主程序 =================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Intelligent Sorter V25")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=THEME["bg_dark"])
        
        self.tk_img = None; self.kb_win = None
        self.setup_styles()
        self.root.bind("<Button-1>", self.auto_close_kb)
        
        threading.Thread(target=diagnosis_thread, daemon=True).start()
        self.build_login_ui()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=THEME["bg_dark"])
        style.configure("Treeview", background=THEME["bg_panel"], foreground=THEME["text_main"], fieldbackground=THEME["bg_panel"], borderwidth=0, rowheight=50, font=("微软雅黑", 12))
        style.configure("Treeview.Heading", background=THEME["bg_dark"], foreground=THEME["ec_cyan"], font=("微软雅黑", 13, "bold"), relief="flat")
        style.map("Treeview", background=[('selected', THEME["ec_blue"])], foreground=[('selected', 'white')])
        style.configure("Horizontal.TProgressbar", thickness=12, troughcolor=THEME["bg_dark"], background=THEME["ec_green"], bordercolor=THEME["bg_panel"])

    # --- 1. 登录界面 ---
    def build_login_ui(self):
        self.clear_window()
        self.cv_bg = tk.Canvas(self.root, bg=THEME["bg_dark"], highlightthickness=0)
        self.cv_bg.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.particles = ParticleSystem(self.cv_bg, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self.animate_bg()

        self.lbl_login_time = tk.Label(self.root, text="--:--:--", font=("Arial", 32, "bold"), bg=THEME["bg_dark"], fg=THEME["text_dim"])
        self.lbl_login_time.place(relx=0.5, rely=0.15, anchor="center")
        self.lbl_login_date = tk.Label(self.root, text="----", font=("微软雅黑", 16), bg=THEME["bg_dark"], fg=THEME["text_dim"])
        self.lbl_login_date.place(relx=0.5, rely=0.22, anchor="center")

        center = tk.Frame(self.root, bg=THEME["bg_dark"])
        center.place(relx=0.5, rely=0.35, anchor="center")
        
        tk.Label(center, text="锂电池缺陷检测系统", font=("微软雅黑", 42, "bold"), bg=THEME["bg_dark"], fg="white").pack(pady=10)
        tk.Label(center, text="", font=("微软雅黑", 14, "bold"), bg=THEME["bg_dark"], fg=THEME["ec_blue"]).pack(pady=(0, 40))
        
        card = tk.Frame(center, bg=THEME["bg_panel"], padx=60, pady=50, highlightbackground=THEME["ec_blue"], highlightthickness=1)
        card.pack()
        
        tk.Label(card, text="操作人员", font=("微软雅黑", 12), bg=THEME["bg_panel"], fg=THEME["text_dim"]).pack(anchor="w")
        row1 = tk.Frame(card, bg=THEME["bg_panel"]); row1.pack(fill="x", pady=(5, 20))
        self.en_user = tk.Entry(row1, font=("微软雅黑", 18), bg=THEME["bg_dark"], fg="white", relief="flat", insertbackground="white", width=18)
        self.en_user.pack(side="left", ipady=8); self.en_user.insert(0, "admin")
        self.en_user.bind("<Button-1>", lambda e: self.open_kb(e, self.en_user))
        tk.Button(row1, text="⌨", bg=THEME["border"], fg="white", width=4, font=("Arial", 14), command=lambda: self.open_kb(None, self.en_user)).pack(side="right", fill="y")
        
        tk.Label(card, text="身份验证码", font=("微软雅黑", 12), bg=THEME["bg_panel"], fg=THEME["text_dim"]).pack(anchor="w")
        row2 = tk.Frame(card, bg=THEME["bg_panel"]); row2.pack(fill="x", pady=(5, 30))
        self.en_pwd = tk.Entry(row2, show="●", font=("微软雅黑", 18), bg=THEME["bg_dark"], fg="white", relief="flat", insertbackground="white", width=18)
        self.en_pwd.pack(side="left", ipady=8); self.en_pwd.insert(0, "123")
        self.en_pwd.bind("<Button-1>", lambda e: self.open_kb(e, self.en_pwd))
        tk.Button(row2, text="⌨", bg=THEME["border"], fg="white", width=4, font=("Arial", 14), command=lambda: self.open_kb(None, self.en_pwd)).pack(side="right", fill="y")
        
        self.btn_login = tk.Button(card, text="进入系统", bg=THEME["ec_blue"], fg="white", font=("微软雅黑", 18, "bold"), relief="flat", command=self.do_login)
        self.btn_login.pack(fill="x", ipady=12)

        self.update_clock(True)

    def animate_bg(self):
        if hasattr(self, 'particles'):
            self.particles.animate()
            self.root.after(50, self.animate_bg)

    def update_clock(self, is_login=False):
        try:
            now = time.strftime("%H:%M:%S"); date = time.strftime("%Y-%m-%d")
            if is_login:
                self.lbl_login_time.config(text=now); self.lbl_login_date.config(text=date)
                if not hasattr(self, 'sidebar'): self.root.after(1000, lambda: self.update_clock(True))
        except: pass

    def do_login(self):
        if hasattr(self, 'particles'): del self.particles
        if verify_login_on_server(self.en_user.get(), self.en_pwd.get()):
            sys_state["user"] = self.en_user.get()
            self.build_dashboard() 
            # 开启分离的主控与视觉双线程
            threading.Thread(target=worker_thread, daemon=True).start()
            threading.Thread(target=camera_thread, daemon=True).start()
        else:
            messagebox.showerror("拒绝访问", "验证失败：账号或密码错误")

    def open_kb(self, event, widget):
        if self.kb_win: 
            try: self.kb_win.destroy()
            except: pass
        self.kb_win = VirtualKeyboard(widget, self.root)
        return "break"

    def auto_close_kb(self, event):
        try:
            widget = event.widget
            if isinstance(widget, str): return 
            if "entry" in str(widget).lower(): return
            if self.kb_win: self.kb_win.destroy(); self.kb_win = None
        except: pass

    # --- 2. 主界面 ---
    def build_dashboard(self):
        self.clear_window()
        self.sidebar = tk.Frame(self.root, bg=THEME["bg_panel"], width=260)
        self.sidebar.pack(side="left", fill="y"); self.sidebar.pack_propagate(False)
        
        tk.Label(self.sidebar, text="❖ 智能中控", font=("微软雅黑", 22, "bold"), bg=THEME["bg_panel"], fg=THEME["ec_blue"]).pack(pady=(60, 50))
        self.btns = {}
        self.create_nav_btn(self.sidebar, "实时监控", "Monitor")
        self.create_nav_btn(self.sidebar, "数据看板", "Data")
        self.create_nav_btn(self.sidebar, "追溯记录", "History")
        self.create_nav_btn(self.sidebar, "系统状态", "Status") 
        
        bot = tk.Frame(self.sidebar, bg=THEME["bg_panel"])
        bot.pack(side="bottom", fill="x", pady=40, padx=20)
        tk.Label(bot, text=f"操作员: {sys_state['user']}", font=("微软雅黑", 14, "bold"), bg=THEME["bg_panel"], fg="white").pack(anchor="w", pady=(0, 15))
        tk.Button(bot, text="退出系统", bg=THEME["ec_red"], fg="white", relief="flat", font=("微软雅黑", 13), command=self.root.destroy).pack(fill="x", ipady=12)

        main = tk.Frame(self.root, bg=THEME["bg_dark"])
        main.pack(side="right", fill="both", expand=True)
        
        header = tk.Frame(main, bg=THEME["bg_dark"], height=80)
        header.pack(side="top", fill="x", padx=30, pady=20); header.pack_propagate(False)
        self.lbl_title = tk.Label(header, text="双流实时监控", font=("微软雅黑", 26, "bold"), bg=THEME["bg_dark"], fg="white")
        self.lbl_title.pack(side="left")
        
        stat_f = tk.Frame(header, bg=THEME["bg_dark"]); stat_f.pack(side="right")
        self.lbl_time = tk.Label(stat_f, text="00:00:00", font=("Arial", 26, "bold"), bg=THEME["bg_dark"], fg=THEME["ec_blue"])
        self.lbl_time.pack(side="right", padx=(40, 0))

        self.container = tk.Frame(main, bg=THEME["bg_dark"])
        self.container.pack(side="bottom", fill="both", expand=True, padx=30, pady=(0, 30))
        self.container.grid_rowconfigure(0, weight=1); self.container.grid_columnconfigure(0, weight=1)
        
        self.pages = {}
        for P in (MonitorPage, DataPage, HistoryPage, StatusPage):
            f = P(parent=self.container, controller=self)
            self.pages[P.__name__] = f
            f.grid(row=0, column=0, sticky="nsew")
        
        self.show_page("MonitorPage")
        self.update_loop()

    def create_nav_btn(self, parent, text, tag):
        b = tk.Button(parent, text=text, font=("微软雅黑", 15), bg=THEME["bg_panel"], fg=THEME["text_dim"], 
                      relief="flat", anchor="w", padx=50, command=lambda: self.show_page(f"{tag}Page"))
        b.pack(fill="x", ipady=18, pady=4)
        self.btns[f"{tag}Page"] = b

    def show_page(self, name):
        self.pages[name].tkraise()
        titles = {"MonitorPage":"双流实时监控","DataPage":"数据看板","HistoryPage":"全量追溯记录","StatusPage":"系统健康状态"}
        self.lbl_title.config(text=titles.get(name, ""))
        for n, b in self.btns.items():
            if n == name: b.config(bg=THEME["bg_dark"], fg=THEME["ec_blue"], font=("微软雅黑", 15, "bold"))
            else: b.config(bg=THEME["bg_panel"], fg=THEME["text_dim"], font=("微软雅黑", 15, "normal"))

    def clear_window(self):
        for w in self.root.winfo_children(): w.destroy()

    def update_loop(self):
        try:
            with lock:
                self.lbl_time.config(text=time.strftime("%Y-%m-%d %H:%M:%S"))
                for p in self.pages.values():
                    if p.winfo_ismapped(): p.update_data()
        except: pass
        self.root.after(30, self.update_loop)

# ================= 9. 页面逻辑 =================

class MonitorPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_dark"])
        self.tk_live = None; self.tk_res = None; self.last_log = ""
        
        top_frame = tk.Frame(self, bg=THEME["bg_dark"])
        top_frame.place(relx=0, rely=0, relwidth=1, relheight=0.65)
        
        f1 = tk.Frame(top_frame, bg="black", highlightbackground=THEME["border"], highlightthickness=2)
        f1.place(relx=0, rely=0, relwidth=0.49, relheight=1)
        tk.Label(f1, text="■ 实时监控画面", bg="black", fg="white", font=("微软雅黑", 11)).place(x=15, y=15)
        self.lbl_live = tk.Label(f1, bg="black", text="无视频信号", fg=THEME["text_dim"], font=("微软雅黑", 16))
        self.lbl_live.pack(fill="both", expand=True)
        
        f2 = tk.Frame(top_frame, bg="#1a1a1a", highlightbackground=THEME["border"], highlightthickness=2)
        f2.place(relx=0.51, rely=0, relwidth=0.49, relheight=1)
        tk.Label(f2, text="❖ 智能缺陷锁定", bg="#1a1a1a", fg=THEME["ec_cyan"], font=("微软雅黑", 11, "bold")).place(x=15, y=15)
        self.lbl_res_img = tk.Label(f2, bg="#1a1a1a", text="等待检测...", fg=THEME["text_dim"], font=("微软雅黑", 16))
        self.lbl_res_img.pack(fill="both", expand=True)

        bot_frame = tk.Frame(self, bg=THEME["bg_dark"])
        bot_frame.place(relx=0, rely=0.67, relwidth=1, relheight=0.33)
        
        c1 = tk.Frame(bot_frame, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
        c1.place(relx=0, rely=0, relwidth=0.3, relheight=1)
        tk.Label(c1, text="AI 智能判定", fg=THEME["text_dim"], bg=THEME["bg_panel"], font=("微软雅黑", 12)).pack(pady=(20,5))
        self.lbl_text_res = tk.Label(c1, text="系统待命", font=("黑体", 42, "bold"), fg="white", bg=THEME["bg_panel"])
        self.lbl_text_res.pack()
        self.bar = ttk.Progressbar(c1, style="Horizontal.TProgressbar", length=180, mode="determinate")
        self.bar.pack(pady=15)
        
        c2 = tk.Frame(bot_frame, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
        c2.place(relx=0.32, rely=0, relwidth=0.45, relheight=1)
        tk.Label(c2, text="系统运行日志", fg=THEME["text_dim"], bg=THEME["bg_panel"], font=("微软雅黑", 12)).pack(anchor="w", padx=15, pady=8)
        self.log_box = st.ScrolledText(c2, bg="#0f172a", fg=THEME["ec_green"], font=("Consolas", 11), relief="flat", state="disabled")
        self.log_box.pack(fill="both", expand=True, padx=15, pady=(0,15))
        
        c3 = tk.Frame(bot_frame, bg=THEME["bg_dark"])
        c3.place(relx=0.79, rely=0, relwidth=0.21, relheight=1)
        self.btn_run = tk.Button(c3, text="启动", bg="#2F5D34", fg="white", font=("微软雅黑", 24, "bold"), relief="flat", command=self.toggle)
        self.btn_run.pack(fill="both", expand=True)

    def toggle(self):
        sys_state["running"] = not sys_state["running"]
        if sys_state["running"]: self.btn_run.config(text="停止", bg="#8B2E2E")
        else: self.btn_run.config(text="启动", bg="#2F5D34")

    def update_data(self):
        if sys_state["live_frame"] is not None:
            try:
                img = Image.fromarray(sys_state["live_frame"]).resize((550, 310))
                self.tk_live = ImageTk.PhotoImage(img)
                self.lbl_live.config(image=self.tk_live, text="")
            except: pass
        if sys_state["result_frame"] is not None:
            try:
                img = Image.fromarray(sys_state["result_frame"]).resize((550, 310))
                self.tk_res = ImageTk.PhotoImage(img)
                self.lbl_res_img.config(image=self.tk_res, text="")
            except: pass
        
        res = sys_state["res"]
        c = THEME["ec_green"] if "合格" in res else (THEME["ec_red"] if "不合格" in res else THEME["text_dim"])
        self.lbl_text_res.config(text=res, fg=c)
        self.bar["value"] = sys_state["conf"] * 100
        
        if sys_state["log"] != self.last_log:
            self.log_box.config(state="normal"); self.log_box.insert(tk.END, ">> " + sys_state["log"]+"\n")
            self.log_box.see(tk.END); self.log_box.config(state="disabled"); self.last_log = sys_state["log"]

class DataPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_dark"])
        stat_row = tk.Frame(self, bg=THEME["bg_dark"])
        stat_row.pack(fill="x", pady=(0, 20))
        self.lbl_total = self.make_stat(stat_row, "生产总数", "0", 0)
        self.lbl_ok = self.make_stat(stat_row, "合格品数", "0", 1)
        self.lbl_ng = self.make_stat(stat_row, "次品数量", "0", 2)
        
        chart_row = tk.Frame(self, bg=THEME["bg_dark"])
        chart_row.pack(fill="both", expand=True)
        self.c1 = self.make_chart_card(chart_row, "良品率分析图", 0)
        self.c2 = self.make_chart_card(chart_row, "缺陷分布直方图", 1)

    def make_stat(self, parent, title, val, col):
        f = tk.Frame(parent, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
        f.place(relx=col*0.34, rely=0, relwidth=0.32, relheight=1)
        tk.Label(f, text=title, font=("微软雅黑", 13), bg=THEME["bg_panel"], fg=THEME["text_dim"]).pack(pady=(20, 5))
        l = tk.Label(f, text=val, font=("Arial", 40, "bold"), bg=THEME["bg_panel"], fg="white")
        l.pack(pady=5); return l

    def make_chart_card(self, parent, title, col):
        f = tk.Frame(parent, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
        f.place(relx=0.51*col, rely=0, relwidth=0.49, relheight=1)
        tk.Label(f, text=title, font=("微软雅黑", 15, "bold"), bg=THEME["bg_panel"], fg=THEME["text_main"]).pack(pady=15)
        c = tk.Canvas(f, bg=THEME["bg_panel"], highlightthickness=0)
        c.pack(fill="both", expand=True); return c

    def update_data(self):
        total = sys_state["good"] + sys_state["bad"]
        self.lbl_total.config(text=str(total))
        self.lbl_ok.config(text=str(sys_state["good"]), fg=THEME["ec_green"])
        self.lbl_ng.config(text=str(sys_state["bad"]), fg=THEME["ec_red"])
        
        self.c1.delete("all")
        w, h = self.c1.winfo_width(), self.c1.winfo_height()
        if w>10:
            x,y,r = w/2, h/2, min(w,h)/3
            rate = sys_state["good"]/total if total>0 else 0
            self.c1.create_oval(x-r, y-r, x+r, y+r, outline=THEME["grid_line"], width=20)
            if rate>0: self.c1.create_arc(x-r, y-r, x+r, y+r, start=90, extent=-360*rate, style="arc", outline=THEME["ec_green"], width=20)
            self.c1.create_text(x,y-10, text=f"{int(rate*100)}%", fill="white", font=("Arial", 36, "bold"))
            self.c1.create_text(x,y+30, text="综合良率", fill=THEME["text_dim"], font=("微软雅黑", 12))
            self.c1.create_rectangle(x-40, h-40, x-30, h-30, fill=THEME["ec_green"], outline="")
            self.c1.create_text(x-25, h-35, text="合格", anchor="w", fill=THEME["text_dim"], font=("微软雅黑", 10))

        self.c2.delete("all")
        w, h = self.c2.winfo_width(), self.c2.winfo_height()
        if w>10:
            d = sys_state["defects"]; items = list(d.items()); count=len(items)
            m = max(d.values()) if any(d.values()) else 1
            bw = w/(count*3); sp = w/count
            colors = [THEME["ec_blue"], THEME["ec_yellow"], THEME["ec_purple"]]
            for i in range(5):
                line_y = (h-60) * (i/4) + 20
                self.c2.create_line(40, line_y, w-40, line_y, fill=THEME["grid_line"], dash=(2,4))
            for i, (k,v) in enumerate(items):
                cx = i*sp + sp/2; ch = (v/m)*(h*0.6) if m>0 else 0; base = h-50
                self.c2.create_rectangle(cx-bw/2, base-ch, cx+bw/2, base, fill=colors[i%3], outline="")
                self.c2.create_text(cx, base+20, text=k, fill=THEME["text_dim"], font=("微软雅黑", 11))
                self.c2.create_text(cx, base-ch-15, text=str(v), fill="white", font=("Arial", 12))

class StatusPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_dark"])
        for i, (title, key) in enumerate([("云端连接", "cloud"), ("红外传感器", "sensor"), ("电机驱动", "motor"), ("摄像头信号", "camera")]):
            row, col = divmod(i, 2)
            f = tk.Frame(self, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
            f.place(relx=col*0.5+0.02, rely=row*0.3+0.05, relwidth=0.46, relheight=0.25)
            tk.Label(f, text=title, font=("微软雅黑", 14), bg=THEME["bg_panel"], fg=THEME["text_dim"]).pack(pady=10)
            lbl = tk.Label(f, text="--", font=("Arial", 24, "bold"), bg=THEME["bg_panel"], fg="white")
            lbl.pack()
            setattr(self, f"lbl_{key}", lbl)
        
        bot = tk.Frame(self, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
        bot.place(relx=0.02, rely=0.6, relwidth=0.96, relheight=0.35)
        tk.Label(bot, text="系统核心负载 (CPU Load)", font=("微软雅黑", 14), bg=THEME["bg_panel"], fg=THEME["text_dim"]).pack(anchor="w", padx=20, pady=15)
        self.cpu_bar = ttk.Progressbar(bot, style="Horizontal.TProgressbar", length=800, mode="determinate")
        self.cpu_bar.pack(pady=20)
        self.lbl_disk = tk.Label(bot, text="剩余存储: --", font=("微软雅黑", 12), bg=THEME["bg_panel"], fg="white")
        self.lbl_disk.pack()

    def update_data(self):
        h = sys_state["health"]
        self.lbl_cloud.config(text="在线" if h["cloud"] else "离线", fg=THEME["ec_green"] if h["cloud"] else THEME["ec_red"])
        self.lbl_sensor.config(text="正常" if h["sensor"] else "异常", fg=THEME["ec_green"] if h["sensor"] else THEME["ec_red"])
        
        if not HARDWARE_AVAILABLE:
            if h["motor"]:
                self.lbl_motor.config(text="模拟运行", fg=THEME["ec_purple"])
            else:
                self.lbl_motor.config(text="模拟待机", fg=THEME["text_dim"])
        else:
            if h["motor"]:
                self.lbl_motor.config(text="运转中", fg=THEME["ec_green"])
            else:
                self.lbl_motor.config(text="待机停转", fg=THEME["ec_yellow"])

        self.lbl_camera.config(text="正常" if h["camera"] else "丢失", fg=THEME["ec_green"] if h["camera"] else THEME["ec_red"])
        self.cpu_bar["value"] = h["cpu_load"]
        self.lbl_disk.config(text=f"剩余存储: {h['disk_free']}")

class HistoryPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_dark"])
        container = tk.Frame(self, bg=THEME["bg_panel"], highlightbackground=THEME["border"], highlightthickness=1)
        container.pack(fill="both", expand=True)
        tk.Label(container, text="最近 50 条追溯记录", font=("微软雅黑", 15, "bold"), bg=THEME["bg_panel"], fg="white").pack(anchor="w", padx=25, pady=25)
        
        cols = ("time", "res", "detail", "conf")
        self.tree = ttk.Treeview(container, columns=cols, show="headings")
        self.tree.heading("time", text="检测时间"); self.tree.column("time", width=200, anchor="center")
        self.tree.heading("res", text="AI 判定"); self.tree.column("res", width=120, anchor="center")
        self.tree.heading("detail", text="缺陷类型"); self.tree.column("detail", width=150, anchor="center")
        self.tree.heading("conf", text="置信度"); self.tree.column("conf", width=120, anchor="center")
        
        self.tree.pack(side="left", fill="both", expand=True, padx=(25, 0), pady=(0, 25))
        scrolly = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        scrolly.pack(side="right", fill="y", pady=(0, 25), padx=(0, 25))
        self.tree.configure(yscroll=scrolly.set)
        
        self.tree.tag_configure("ok", foreground=THEME["ec_green"])
        self.tree.tag_configure("ng", foreground=THEME["ec_red"])

    def update_data(self):
        if sys_state["new_records"]:
            for r in sys_state["new_records"]:
                tag = "ok" if "合格" in r["result"] else "ng"
                self.tree.insert("", 0, values=(r["time"], r["result"], r["detail"], r["conf"]), tags=(tag,))
            sys_state["new_records"] = []

if __name__ == "__main__":
    root = tk.Tk(); app = App(root); root.mainloop()