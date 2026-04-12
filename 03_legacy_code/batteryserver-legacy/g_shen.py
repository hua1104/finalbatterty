import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk, ImageDraw, ImageFont
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
SERVER_URL = f"http://{SERVER_IP}:5000"
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
    "result_frame": None,
    "last_frame_ts": time.time(),
    "res": "系统就绪", 
    "conf": 0.0,
    "log": "指挥系统核心已加载...", 
    "exit": False,
    "new_records": [],
    "health": {
        "motor": False, "sensor": HARDWARE_AVAILABLE, "cloud": False, 
        "camera": False, "disk_free": "0GB", "cpu_load": 0, "ping": 999
    },
    "alerts": []
}

# ================= 4. 视觉主题 (赛博指挥官) =================
THEME = {
    "bg_base":   "#09090b",    # 极致深黑
    "bg_card":   "#18181b",    # 卡片黑灰
    "bg_input":  "#27272a",    # 输入框背景
    
    # 霓虹配色
    "neon_blue": "#06b6d4",    # 赛博蓝
    "neon_green":"#10b981",    # 荧光绿
    "neon_red":  "#f43f5e",    # 故障红
    "neon_gold": "#f59e0b",    # 警告金
    
    "border":    "#3f3f46",    # 边框
    "text_main": "#ffffff",    # 纯白
    "text_sub":  "#a1a1aa"     # 银灰
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

# ================= 6. 线程逻辑 =================
def diagnosis_thread():
    while not sys_state["exit"]:
        try:
            total, used, free = shutil.disk_usage("/")
            sys_state["health"]["disk_free"] = f"{free // (2**30)} GB"
        except: pass
        sys_state["health"]["cpu_load"] = random.randint(10, 45) # 模拟波动
        
        if time.time() - sys_state["last_frame_ts"] > 3.0:
            sys_state["health"]["camera"] = False
            with lock: sys_state["alerts"] = ["严重警告: 视频流信号丢失"]
        else:
            sys_state["health"]["camera"] = True
            with lock: sys_state["alerts"] = []
        time.sleep(1.5)

def worker_thread():
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

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    active_items = []
    last_detect = False

    while not sys_state["exit"]:
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                live_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with lock: 
                    sys_state["live_frame"] = live_rgb
                    sys_state["last_frame_ts"] = time.time()
            else: frame = None
        else: frame = None

        if not sys_state["running"]:
            time.sleep(0.05); continue

        if HARDWARE_AVAILABLE: motor.move_steps(BATCH_STEPS, delay=STEP_DELAY)
        else: time.sleep(0.02)
        sys_state["health"]["motor"] = True
        
        for item in active_items: item['steps'] += BATCH_STEPS
        
        detected = sensor.is_object_detected()
        if not HARDWARE_AVAILABLE and random.random() < 0.005: detected = True 

        if detected and not last_detect:
            with lock: sys_state["log"] = f"[{time.strftime('%H:%M:%S')}] 目标进入识别区..."
            time.sleep(0.8) 
            
            analyze_frame = frame.copy() if frame is not None else None
            if analyze_frame is None: analyze_frame = np.zeros((720, 1280, 3), dtype=np.uint8)

            if detector:
                is_bad, res_str, conf, ann_img = detector.detect(analyze_frame)
                display_img = ann_img
            else:
                is_bad = random.choice([True, False])
                res_str = "不合格" if is_bad else "合格"
                if is_bad and random.random() > 0.5: res_str += " (褶皱)"
                elif is_bad: res_str += " (划痕)"
                conf = round(random.uniform(0.85, 0.99), 4)
                display_img = analyze_frame.copy()
                color = (0, 0, 255) if is_bad else (0, 255, 0)
                cv2.rectangle(display_img, (400, 200), (800, 500), color, 4)

            path = save_snapshot(display_img)
            res_rgb = cv2.cvtColor(display_img, cv2.COLOR_BGR2RGB)

            curr_time = time.strftime("%H:%M:%S")
            with lock:
                sys_state["result_frame"] = res_rgb 
                sys_state["res"] = res_str
                sys_state["conf"] = conf
                sys_state["log"] = f"[{curr_time}] 智能判定: {res_str}"
                
                detail = "正常"
                if is_bad: 
                    sys_state["bad"] += 1
                    if "褶皱" in res_str: sys_state["defects"]["褶皱"] += 1; detail="褶皱"
                    elif "划痕" in res_str: sys_state["defects"]["划痕"] += 1; detail="划痕"
                    else: sys_state["defects"]["其他"] += 1; detail="其他"
                else: sys_state["good"] += 1
                
                sys_state["new_records"].append({"time": curr_time, "result": res_str, "detail": detail, "conf": f"{int(conf*100)}%"})
            
            threading.Thread(target=upload_task, args=(path, res_str, conf)).start()
            active_items.append({'is_bad': is_bad, 'steps': 0})
        
        last_detect = detected

        for i in range(len(active_items)-1, -1, -1):
            item = active_items[i]
            if item['is_bad'] and item['steps'] >= 6000:
                pusher.eject(); active_items.pop(i)
            elif not item['is_bad'] and item['steps'] > 8000:
                active_items.pop(i)
                
    if cap and cap.isOpened(): cap.release()

# ================= 7. 全能虚拟键盘 (暗黑版) =================
class VirtualKeyboard(tk.Toplevel):
    def __init__(self, target_entry, root):
        super().__init__(root)
        self.target = target_entry
        self.configure(bg=THEME["bg_card"])
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = 950, 320 
        x = (sw - w) // 2; y = sh - h - 10 
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.attributes('-topmost', True) 
        self.transient(root); self.overrideredirect(True)
        
        # 增加霓虹边框
        f_border = tk.Frame(self, bg=THEME["neon_blue"], padx=2, pady=2)
        f_border.pack(fill="both", expand=True)
        f_main = tk.Frame(f_border, bg=THEME["bg_base"])
        f_main.pack(fill="both", expand=True)

        self.layouts = {
            "en_lower": [['q','w','e','r','t','y','u','i','o','p'],['a','s','d','f','g','h','j','k','l'],['z','x','c','v','b','n','m'],[',','.','@','/','-','_','+','=']],
            "en_upper": [['Q','W','E','R','T','Y','U','I','O','P'],['A','S','D','F','G','H','J','K','L'],['Z','X','C','V','B','N','M'],['!','?','#','$','%','&','*','(',')']],
            "numbers": [['1','2','3'],['4','5','6'],['7','8','9'],['.','0','-']],
            "symbols": [['!','@','#','$','%','^','&','*','(',')'],['-','_','=','+','[',']','{','}','\\','|'],[';','\'','"',',','.','<','>','?','/'],['~','`','©','®','™','€','£','¥','§','°']],
            "cn_preset": [['管理员','操作员','张伟','李强','王芳'],['赵敏','陈杰','测试员','访客','系统'],['合格','不合格','褶皱','划痕','其他']]
        }
        self.mode = "en_lower" 

        bar = tk.Frame(f_main, bg=THEME["bg_card"], height=45)
        bar.pack(fill="x")
        self.btn_en = self.mk_tab(bar, "英文", "en_lower")
        self.btn_num = self.mk_tab(bar, "数字", "numbers")
        self.btn_sym = self.mk_tab(bar, "符号", "symbols")
        self.btn_cn = self.mk_tab(bar, "中文", "cn_preset")
        tk.Button(bar, text="⬇ 隐藏键盘", bg=THEME["neon_red"], fg="white", font=("微软雅黑", 11, "bold"), relief="flat", command=self.destroy).pack(side="right", padx=5, pady=2)

        self.key_area = tk.Frame(f_main, bg=THEME["bg_base"]); self.key_area.pack(fill="both", expand=True, padx=10, pady=5)
        
        func_f = tk.Frame(f_main, bg=THEME["bg_base"]); func_f.pack(fill="x", padx=10, pady=5)
        self.btn_shift = tk.Button(func_f, text="大小写", width=10, height=2, bg=THEME["bg_card"], fg="white", font=("微软雅黑", 12), relief="flat", command=self.toggle_shift); self.btn_shift.pack(side="left", padx=5)
        tk.Button(func_f, text="—— 空 格 ——", width=25, height=2, bg=THEME["bg_card"], fg="white", relief="flat", command=lambda: self.add_char(" ")).pack(side="left", padx=5, expand=True, fill="x")
        tk.Button(func_f, text="退格", width=10, height=2, bg=THEME["neon_gold"], fg="black", font=("微软雅黑", 12, "bold"), relief="flat", command=self.backspace).pack(side="left", padx=5)
        tk.Button(func_f, text="确定", width=10, height=2, bg=THEME["neon_green"], fg="black", font=("微软雅黑", 12, "bold"), relief="flat", command=self.destroy).pack(side="right", padx=5)

        self.render_keys()
        self.lift(); self.focus_force()

    def mk_tab(self, parent, text, mode):
        return tk.Button(parent, text=text, font=("微软雅黑", 11), bg=THEME["bg_base"], fg=THEME["text_sub"], relief="flat", padx=15, command=lambda: self.switch_mode(mode)).pack(side="left")

    def switch_mode(self, mode):
        self.mode = mode
        self.render_keys()

    def render_keys(self):
        for w in self.key_area.winfo_children(): w.destroy()
        for row in self.layouts[self.mode]:
            row_f = tk.Frame(self.key_area, bg=THEME["bg_base"]); row_f.pack(fill="both", expand=True)
            for char in row:
                tk.Button(row_f, text=char, font=("微软雅黑", 14), bg=THEME["bg_input"], fg="white", relief="flat", 
                          activebackground=THEME["neon_blue"], activeforeground="black",
                          command=lambda c=char: self.add_char(c)).pack(side="left", fill="both", expand=True, padx=3, pady=3)

    def toggle_shift(self): self.mode = "en_upper" if self.mode == "en_lower" else "en_lower"; self.render_keys()
    def add_char(self, char): self.target.insert(tk.END, char)
    def backspace(self): 
        cur = self.target.get(); self.target.delete(0, tk.END); self.target.insert(0, cur[:-1])

# ================= 8. 粒子特效 (量子场) =================
class ParticleSystem:
    def __init__(self, canvas, width, height, count=55):
        self.canvas = canvas; self.width = width; self.height = height
        self.particles = []
        for _ in range(count):
            self.particles.append({
                "x": random.randint(0, width), "y": random.randint(0, height),
                "vx": random.uniform(-0.6, 0.6), "vy": random.uniform(-0.6, 0.6),
                "r": random.randint(1, 3)
            })
    
    def animate(self):
        try:
            self.canvas.delete("all")
            for i, p1 in enumerate(self.particles):
                p1["x"] += p1["vx"]; p1["y"] += p1["vy"]
                if p1["x"]<0 or p1["x"]>self.width: p1["vx"] *= -1
                if p1["y"]<0 or p1["y"]>self.height: p1["vy"] *= -1
                
                # 绘制粒子 (荧光蓝)
                self.canvas.create_oval(p1["x"]-p1["r"], p1["y"]-p1["r"], p1["x"]+p1["r"], p1["y"]+p1["r"], fill=THEME["neon_blue"], outline="")
                
                # 绘制神经网络连线
                for p2 in self.particles[i+1:]:
                    dist = math.hypot(p1["x"]-p2["x"], p1["y"]-p2["y"])
                    if dist < 110:
                        alpha = int((1 - dist/110) * 100)
                        if alpha > 15: 
                            self.canvas.create_line(p1["x"], p1["y"], p2["x"], p2["y"], fill=THEME["bg_input"], width=1)
        except: pass

# ================= 9. 主程序 =================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Intelligent Sorter V28 Commander")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=THEME["bg_base"])
        
        self.tk_img = None; self.kb_win = None
        self.setup_styles()
        self.root.bind("<Button-1>", self.auto_close_kb)
        threading.Thread(target=diagnosis_thread, daemon=True).start()
        self.build_login_ui()

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=THEME["bg_base"])
        # 表格美化
        style.configure("Treeview", background=THEME["bg_card"], foreground=THEME["text_main"], 
                        fieldbackground=THEME["bg_card"], borderwidth=0, rowheight=55, font=("微软雅黑", 12))
        style.configure("Treeview.Heading", background=THEME["bg_base"], foreground=THEME["neon_blue"], 
                        font=("微软雅黑", 14, "bold"), relief="flat")
        style.map("Treeview", background=[('selected', THEME["neon_blue"])], foreground=[('selected', 'white')])
        style.configure("Horizontal.TProgressbar", thickness=14, troughcolor=THEME["bg_base"], background=THEME["neon_green"], bordercolor=THEME["bg_card"])

    # --- 1. 登录界面 (极光特效) ---
    def build_login_ui(self):
        self.clear_window()
        self.cv_bg = tk.Canvas(self.root, bg=THEME["bg_base"], highlightthickness=0)
        self.cv_bg.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.particles = ParticleSystem(self.cv_bg, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        self.animate_bg()

        # 顶部时间
        self.lbl_login_time = tk.Label(self.root, text="--:--:--", font=("Arial", 36, "bold"), bg=THEME["bg_base"], fg=THEME["text_sub"])
        self.lbl_login_time.place(relx=0.5, rely=0.15, anchor="center")
        self.lbl_login_date = tk.Label(self.root, text="----", font=("微软雅黑", 16), bg=THEME["bg_base"], fg=THEME["text_sub"])
        self.lbl_login_date.place(relx=0.5, rely=0.22, anchor="center")

        center = tk.Frame(self.root, bg=THEME["bg_base"])
        center.place(relx=0.5, rely=0.40, anchor="center")
        
        tk.Label(center, text="⚡ 智能视觉分拣系统", font=("微软雅黑", 48, "bold"), bg=THEME["bg_base"], fg="white").pack(pady=10)
        tk.Label(center, text="V28.0 赛博指挥官版 | 全域感知引擎", font=("微软雅黑", 16), bg=THEME["bg_base"], fg=THEME["neon_blue"]).pack(pady=(0, 40))
        
        # 玻璃拟态登录卡片
        border = tk.Frame(center, bg=THEME["neon_blue"], padx=1, pady=1); border.pack()
        card = tk.Frame(border, bg=THEME["bg_card"], padx=60, pady=50); card.pack()
        
        self.mk_login_entry(card, "操作人员", "admin", False)
        self.mk_login_entry(card, "身份验证", "123", True)
        
        self.btn_login = tk.Button(card, text="进入指挥中心", bg=THEME["neon_blue"], fg="white", font=("微软雅黑", 18, "bold"), relief="flat", cursor="hand2", command=self.do_login)
        self.btn_login.pack(fill="x", ipady=12, pady=(30,0))

        self.update_clock(True)

    def mk_login_entry(self, parent, label, default, is_pwd):
        tk.Label(parent, text=label, font=("微软雅黑", 12), bg=THEME["bg_card"], fg=THEME["text_sub"]).pack(anchor="w", pady=(15,5))
        f = tk.Frame(parent, bg=THEME["bg_input"], padx=2, pady=2); f.pack(fill="x")
        
        entry = tk.Entry(f, font=("微软雅黑", 16), bg=THEME["bg_input"], fg="white", relief="flat", insertbackground="white", width=22)
        if is_pwd: entry.config(show="●")
        entry.pack(side="left", ipady=8, padx=5); entry.insert(0, default)
        
        btn = tk.Button(f, text="⌨", bg=THEME["bg_card"], fg=THEME["neon_blue"], width=4, font=("Arial", 14), relief="flat", command=lambda: self.open_kb(None, entry))
        btn.pack(side="right", fill="y")
        
        entry.bind("<Button-1>", lambda e: self.open_kb(e, entry))
        if is_pwd: self.en_pwd = entry
        else: self.en_user = entry

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
            threading.Thread(target=worker_thread, daemon=True).start()
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
        self.sidebar = tk.Frame(self.root, bg=THEME["bg_card"], width=260)
        self.sidebar.pack(side="left", fill="y"); self.sidebar.pack_propagate(False)
        
        tk.Label(self.sidebar, text="❖ 指挥中控", font=("微软雅黑", 22, "bold"), bg=THEME["bg_card"], fg=THEME["neon_blue"]).pack(pady=(60, 50))
        
        self.nav_frame = tk.Frame(self.sidebar, bg=THEME["bg_card"]); self.nav_frame.pack(fill="both", expand=True)
        self.nav_btns = {}
        self.mk_nav_btn("实时监控", "Monitor")
        self.mk_nav_btn("数据看板", "Data")
        self.mk_nav_btn("系统状态", "Status")
        self.mk_nav_btn("追溯记录", "History")
        
        bot = tk.Frame(self.sidebar, bg=THEME["bg_card"])
        bot.pack(side="bottom", fill="x", pady=30, padx=20)
        tk.Label(bot, text=f"指挥官: {sys_state['user']}", font=("微软雅黑", 12), bg=THEME["bg_card"], fg=THEME["text_sub"]).pack(anchor="w", pady=(0, 15))
        tk.Button(bot, text="安全退出", bg=THEME["neon_red"], fg="white", relief="flat", font=("微软雅黑", 12, "bold"), command=self.root.destroy).pack(fill="x", ipady=12)

        main = tk.Frame(self.root, bg=THEME["bg_base"])
        main.pack(side="right", fill="both", expand=True)
        
        header = tk.Frame(main, bg=THEME["bg_base"], height=70)
        header.pack(side="top", fill="x", padx=30, pady=15); header.pack_propagate(False)
        self.lbl_page_title = tk.Label(header, text="实时监控", font=("微软雅黑", 24, "bold"), bg=THEME["bg_base"], fg="white")
        self.lbl_page_title.pack(side="left")
        self.lbl_time = tk.Label(header, text="--", font=("Arial", 20, "bold"), bg=THEME["bg_base"], fg=THEME["neon_blue"])
        self.lbl_time.pack(side="right")

        self.container = tk.Frame(main, bg=THEME["bg_base"])
        self.container.pack(side="bottom", fill="both", expand=True, padx=30, pady=(0, 30))
        self.container.grid_rowconfigure(0, weight=1); self.container.grid_columnconfigure(0, weight=1)
        
        self.pages = {}
        for P in (MonitorPage, DataPage, StatusPage, HistoryPage):
            f = P(parent=self.container, controller=self)
            self.pages[P.__name__] = f
            f.grid(row=0, column=0, sticky="nsew")
        
        self.show_page("MonitorPage")
        self.update_loop()

    def mk_nav_btn(self, text, tag):
        f = tk.Frame(self.nav_frame, bg=THEME["bg_card"], height=60, cursor="hand2")
        f.pack(fill="x", pady=2); f.pack_propagate(False)
        bar = tk.Frame(f, bg=THEME["bg_card"], width=5); bar.pack(side="left", fill="y")
        lbl = tk.Label(f, text=text, font=("微软雅黑", 14), bg=THEME["bg_card"], fg=THEME["text_sub"])
        lbl.pack(side="left", padx=30)
        for w in [f, bar, lbl]: w.bind("<Button-1>", lambda e: self.show_page(f"{tag}Page"))
        self.nav_btns[f"{tag}Page"] = {"frame": f, "bar": bar, "lbl": lbl}

    def show_page(self, name):
        self.pages[name].tkraise()
        titles = {"MonitorPage":"实时监控","DataPage":"数据看板","StatusPage":"系统状态","HistoryPage":"追溯记录"}
        self.lbl_page_title.config(text=titles.get(name, ""))
        for key, w in self.nav_btns.items():
            active = (key == name)
            w["frame"].config(bg=THEME["bg_input"] if active else THEME["bg_card"])
            w["bar"].config(bg=THEME["neon_blue"] if active else THEME["bg_card"])
            w["lbl"].config(bg=THEME["bg_input"] if active else THEME["bg_card"], fg="white" if active else THEME["text_sub"])

    def clear_window(self):
        for w in self.root.winfo_children(): w.destroy()

    def update_loop(self):
        try:
            with lock:
                self.lbl_time.config(text=time.strftime("%Y-%m-%d %H:%M:%S"))
                if sys_state["alerts"]: self.lbl_page_title.config(fg=THEME["neon_red"])
                else: self.lbl_page_title.config(fg="white")
                for p in self.pages.values():
                    if p.winfo_ismapped(): p.update_data()
        except: pass
        self.root.after(30, self.update_loop)

# ================= 9. 页面逻辑 =================

class MonitorPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_base"])
        self.tk_live = None; self.tk_res = None; self.last_log = ""
        
        top = tk.Frame(self, bg=THEME["bg_base"]); top.place(relx=0, rely=0, relwidth=1, relheight=0.65)
        self.f1 = self.mk_v_frame(top, "■ 实时监控", 0)
        self.lbl_live = tk.Label(self.f1, bg="black", text="无信号", fg=THEME["text_sub"], font=("微软雅黑", 16))
        self.lbl_live.pack(fill="both", expand=True, padx=1, pady=1)
        
        self.f2 = self.mk_v_frame(top, "❖ 缺陷锁定", 0.51)
        self.lbl_res_img = tk.Label(self.f2, bg="#111", text="待命", fg=THEME["text_sub"], font=("微软雅黑", 16))
        self.lbl_res_img.pack(fill="both", expand=True, padx=1, pady=1)

        bot = tk.Frame(self, bg=THEME["bg_base"]); bot.place(relx=0, rely=0.68, relwidth=1, relheight=0.32)
        
        c1 = self.mk_card(bot, 0, 0.3); tk.Label(c1, text="AI 判定结果", fg=THEME["text_sub"], bg=THEME["bg_card"]).pack(pady=10)
        self.lbl_res_txt = tk.Label(c1, text="READY", font=("黑体", 40, "bold"), fg="white", bg=THEME["bg_card"]); self.lbl_res_txt.pack()
        self.bar = ttk.Progressbar(c1, style="Horizontal.TProgressbar", length=180, mode="determinate"); self.bar.pack(pady=15)
        
        c2 = self.mk_card(bot, 0.32, 0.45); tk.Label(c2, text="系统内核日志", fg=THEME["text_sub"], bg=THEME["bg_card"]).pack(anchor="w", padx=10, pady=5)
        self.log = st.ScrolledText(c2, bg="#000", fg=THEME["neon_green"], font=("Consolas", 10), relief="flat", state="disabled")
        self.log.pack(fill="both", expand=True, padx=10, pady=10)
        
        c3 = tk.Frame(bot, bg=THEME["bg_base"]); c3.place(relx=0.79, rely=0, relwidth=0.21, relheight=1)
        self.btn_run = tk.Button(c3, text="启动", bg="#14532d", fg="white", font=("微软雅黑", 24, "bold"), relief="flat", command=self.toggle)
        self.btn_run.pack(fill="both", expand=True)

    def mk_v_frame(self, parent, title, x):
        f = tk.Frame(parent, bg=THEME["bg_card"], highlightbackground=THEME["border"], highlightthickness=1)
        f.place(relx=x, rely=0, relwidth=0.49, relheight=1)
        tk.Label(f, text=title, bg=THEME["bg_card"], fg=THEME["neon_blue"], font=("微软雅黑", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        c = tk.Frame(f, bg=THEME["neon_blue"], padx=1, pady=1); c.pack(fill="both", expand=True, padx=10, pady=(0,10))
        return c

    def mk_card(self, parent, x, w):
        f = tk.Frame(parent, bg=THEME["bg_card"], highlightbackground=THEME["border"], highlightthickness=1)
        f.place(relx=x, rely=0, relwidth=w, relheight=1)
        return f

    def toggle(self):
        sys_state["running"] = not sys_state["running"]
        if sys_state["running"]: self.btn_run.config(text="停止", bg="#7f1d1d")
        else: self.btn_run.config(text="启动", bg="#14532d")

    def update_data(self):
        if not sys_state["running"]: self.btn_run.config(bg="#14532d")
        
        if sys_state["live_frame"] is not None:
            try:
                img = Image.fromarray(sys_state["live_frame"]).resize((560, 320))
                self.tk_live = ImageTk.PhotoImage(img)
                self.lbl_live.config(image=self.tk_live, text="")
            except: pass
        
        if sys_state["result_frame"] is not None:
            try:
                img = Image.fromarray(sys_state["result_frame"]).resize((560, 320))
                self.tk_res = ImageTk.PhotoImage(img)
                self.lbl_res_img.config(image=self.tk_res, text="")
            except: pass
        
        res = sys_state["res"]
        c = THEME["neon_green"] if "合格" in res else (THEME["neon_red"] if "不合格" in res else THEME["text_sub"])
        self.lbl_res_txt.config(text=res, fg=c)
        self.bar["value"] = sys_state["conf"] * 100
        
        if sys_state["log"] != self.last_log:
            self.log.config(state="normal"); self.log.insert(tk.END, "> "+sys_state["log"]+"\n"); self.log.see(tk.END); self.log.config(state="disabled"); self.last_log=sys_state["log"]

class DataPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_base"])
        row1 = tk.Frame(self, bg=THEME["bg_base"]); row1.pack(fill="x", pady=10)
        self.mk_stat(row1, "生产总数", "lbl_total", 0)
        self.mk_stat(row1, "合格品数", "lbl_ok", 1, THEME["neon_green"])
        self.mk_stat(row1, "次品数量", "lbl_ng", 2, THEME["neon_red"])
        
        row2 = tk.Frame(self, bg=THEME["bg_base"]); row2.pack(fill="both", expand=True)
        self.c1 = self.mk_chart(row2, "良品率趋势 (ECharts)", 0)
        self.c2 = self.mk_chart(row2, "缺陷分布 (ECharts)", 1)

    def mk_stat(self, parent, title, var_name, col, color="white"):
        f = tk.Frame(parent, bg=THEME["bg_card"], height=100); f.place(relx=col*0.34, rely=0, relwidth=0.32, relheight=1)
        tk.Label(f, text=title, font=("微软雅黑", 12), bg=THEME["bg_card"], fg=THEME["text_sub"]).pack(pady=(15,5))
        l = tk.Label(f, text="0", font=("Arial", 36, "bold"), bg=THEME["bg_card"], fg=color)
        l.pack()
        setattr(self, var_name, l)

    def mk_chart(self, parent, title, col):
        f = tk.Frame(parent, bg=THEME["bg_card"]); f.place(relx=col*0.51, rely=0, relwidth=0.49, relheight=1)
        tk.Label(f, text=title, font=("微软雅黑", 14, "bold"), bg=THEME["bg_card"], fg=THEME["text_main"]).pack(pady=10)
        c = tk.Canvas(f, bg=THEME["bg_card"], highlightthickness=0); c.pack(fill="both", expand=True)
        return c

    def update_data(self):
        total = sys_state["good"] + sys_state["bad"]
        self.lbl_total.config(text=str(total))
        self.lbl_ok.config(text=str(sys_state["good"]))
        self.lbl_ng.config(text=str(sys_state["bad"]))
        
        self.c1.delete("all"); w=self.c1.winfo_width(); h=self.c1.winfo_height()
        if w>10:
            x,y,r = w/2, h/2, min(w,h)/3.5
            rate = sys_state["good"]/total if total>0 else 0
            self.c1.create_oval(x-r,y-r,x+r,y+r,outline=THEME["grid_line"],width=25)
            if rate>0: 
                self.c1.create_arc(x-r,y-r,x+r,y+r,start=90,extent=-360*rate,style="arc",outline=THEME["neon_green"],width=25)
            self.c1.create_text(x,y,text=f"{int(rate*100)}%",fill="white",font=("Arial",30,"bold"))
            
        self.c2.delete("all"); w=self.c2.winfo_width(); h=self.c2.winfo_height()
        if w>10:
            d=sys_state["defects"]; items=list(d.items()); m=max(d.values()) if any(d.values()) else 1
            bw=w/(len(items)*3); sp=w/len(items)
            for i in range(5):
                ly = (h-40)*(i/4)+20
                self.c2.create_line(40,ly,w-40,ly,fill=THEME["grid_line"],dash=(2,2))
            
            colors = [THEME["neon_blue"], THEME["neon_gold"], THEME["ec_purple"]]
            for i,(k,v) in enumerate(items):
                cx=i*sp+sp/2; ch=(v/m)*(h*0.6); base=h-40
                self.c2.create_rectangle(cx-bw/2,base-ch,cx+bw/2,base,fill=colors[i%3],outline="")
                self.c2.create_text(cx,base+15,text=k,fill=THEME["text_sub"],font=("微软雅黑",10))
                self.c2.create_text(cx,base-ch-15,text=str(v),fill="white",font=("Arial",11))

class StatusPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_base"])
        
        top = tk.Frame(self, bg=THEME["bg_card"]); top.pack(fill="x", pady=20, padx=20)
        tk.Label(top, text="系统健康度诊断", font=("微软雅黑", 16, "bold"), bg=THEME["bg_card"], fg="white").pack(anchor="w", padx=20, pady=15)
        
        mid = tk.Frame(self, bg=THEME["bg_base"]); mid.pack(fill="both", expand=True, padx=20)
        self.mk_h(mid, "云端连接", "cloud", 0, 0)
        self.mk_h(mid, "视觉传感器", "camera", 0, 1)
        self.mk_h(mid, "传送电机", "motor", 1, 0)
        self.mk_h(mid, "红外触发", "sensor", 1, 1)
        
        bot = tk.Frame(self, bg=THEME["bg_card"]); bot.pack(fill="x", pady=20, padx=20)
        tk.Label(bot, text="核心负载", bg=THEME["bg_card"], fg=THEME["text_sub"]).pack(anchor="w", padx=20, pady=10)
        self.cpu_bar = ttk.Progressbar(bot, length=800); self.cpu_bar.pack(padx=20, pady=(0,20))
        self.lbl_disk = tk.Label(bot, text="存储: --", bg=THEME["bg_card"], fg="white"); self.lbl_disk.pack(pady=5)

    def mk_h(self, parent, title, key, r, c):
        f = tk.Frame(parent, bg=THEME["bg_card"], highlightbackground=THEME["border"], highlightthickness=1)
        f.grid(row=r, column=c, padx=10, pady=10, sticky="nsew")
        parent.grid_columnconfigure(c, weight=1); parent.grid_rowconfigure(r, weight=1)
        tk.Label(f, text=title, font=("微软雅黑", 14), bg=THEME["bg_card"], fg=THEME["text_sub"]).pack(pady=15)
        l = tk.Label(f, text="--", font=("微软雅黑", 20, "bold"), bg=THEME["bg_card"], fg="white")
        l.pack()
        setattr(self, f"lbl_{key}", l)

    def update_data(self):
        h = sys_state["health"]
        self.lbl_cloud.config(text="在线" if h["cloud"] else "离线", fg=THEME["neon_green"] if h["cloud"] else THEME["neon_red"])
        self.lbl_camera.config(text="正常" if h["camera"] else "无信号", fg=THEME["neon_green"] if h["camera"] else THEME["neon_red"])
        self.lbl_motor.config(text="运行" if h["motor"] else "静止", fg=THEME["neon_green"] if h["motor"] else THEME["neon_gold"])
        self.lbl_sensor.config(text="就绪" if h["sensor"] else "未连接", fg=THEME["neon_green"] if h["sensor"] else THEME["neon_red"])
        self.cpu_bar["value"] = h["cpu_load"]
        self.lbl_disk.config(text=f"剩余空间: {h['disk_free']}")

class HistoryPage(tk.Frame):
    def __init__(self, parent, controller):
        tk.Frame.__init__(self, parent, bg=THEME["bg_base"])
        c = tk.Frame(self, bg=THEME["bg_card"]); c.pack(fill="both", expand=True, padx=20, pady=20)
        tk.Label(c, text="全量检测记录", font=("微软雅黑", 16, "bold"), bg=THEME["bg_card"], fg="white").pack(anchor="w", padx=20, pady=20)
        cols = ("time", "res", "detail", "conf")
        self.tree = ttk.Treeview(c, columns=cols, show="headings")
        self.tree.heading("time", text="时间戳"); self.tree.column("time", width=200, anchor="center")
        self.tree.heading("res", text="判定"); self.tree.column("res", width=100, anchor="center")
        self.tree.heading("detail", text="详情"); self.tree.column("detail", width=150, anchor="center")
        self.tree.heading("conf", text="置信度"); self.tree.column("conf", width=100, anchor="center")
        self.tree.pack(fill="both", expand=True, padx=20, pady=20)
        self.tree.tag_configure("ok", foreground=THEME["neon_green"]); self.tree.tag_configure("ng", foreground=THEME["neon_red"])

    def update_data(self):
        if sys_state["new_records"]:
            for r in sys_state["new_records"]:
                t = "ok" if "合格" in r["result"] else "ng"
                self.tree.insert("", 0, values=(r["time"], r["result"], r["detail"], r["conf"]), tags=(t,))
            sys_state["new_records"] = []

if __name__ == "__main__":
    root = tk.Tk(); app = App(root); root.mainloop()