import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk
import threading
import time
import os
import random
import requests 

# ================= 1. 模块导入 =================
# 尝试加载硬件驱动
try:
    from stepper_driver import StepperMotor
    from ir_sensor import IRSensor
    from sorter_motor import SorterMotor
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False
    print("提示: 未检测到硬件驱动, 系统将运行在 [模拟演示模式]")

# 尝试加载 AI 检测模块
try:
    from ai_detector import DefectDetector
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False
    print("提示: 未检测到 ai_detector.py, AI 功能不可用")

# ================= 2. 全局配置 =================
# 请确认这是正确的服务器 IP
SERVER_IP = "100.93.162.20" 
SERVER_URL = f"http://{SERVER_IP}:5000"
SAVE_FOLDER = "/home/pi/conveyor_photos"

STEP_DELAY = 0.0006 
BATCH_STEPS = 50     

# ================= 3. 全局状态 =================
lock = threading.RLock()
sys_state = {
    "running": False,
    "user": None,
    "good": 0,
    "bad": 0,
    "img": None,
    "res": "等待检测",
    "time": "--:--:--",
    "log": "设备已就绪",
    "new_records": [],
    "exit": False
}

# ================= 4. 视觉主题 =================
COLOR_BG = "#1e293b"        # 背景: 深岩灰
COLOR_PANEL = "#334155"     # 面板: 浅岩灰
COLOR_INPUT = "#475569"     # 输入框底色
COLOR_HIGHLIGHT = "#0ea5e9" # 高亮蓝
COLOR_BTN_MAIN = "#2563eb"  # 主按钮蓝
COLOR_BTN_DANGER = "#dc2626"# 警示红
COLOR_BTN_SUCCESS = "#16a34a"# 成功绿
COLOR_TEXT_MAIN = "#ffffff" # 主文字白
COLOR_TEXT_DIM = "#cbd5e1"  # 辅助文字灰

# ================= 5. 后台通信逻辑 =================

def verify_login_on_server(username, password):
    try:
        data = {'username': username, 'password': password}
        resp = requests.post(f"{SERVER_URL}/login", data=data, timeout=3)
        if resp.status_code == 200: return True
    except: pass
    # 如果没联网，为了演示方便，允许 admin/123 本地登录
    if username == "admin" and password == "123":
        return True
    return False

def upload_task(path, result_str):
    try:
        filename = os.path.basename(path)
        with open(path, 'rb') as f:
            data = {'operator': sys_state['user'], 'result': result_str}
            files = {'file': (filename, f, 'image/jpeg')}
            requests.post(f"{SERVER_URL}/upload", data=data, files=files, timeout=5)
    except: pass

def save_snapshot(frame):
    if not os.path.exists(SAVE_FOLDER): os.makedirs(SAVE_FOLDER)
    fname = f"img_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
    path = os.path.join(SAVE_FOLDER, fname)
    import cv2
    cv2.imwrite(path, frame)
    return path

# ================= 6. 核心工作线程 (AI集成点) =================

def worker_thread():
    global cv2
    import cv2
    
    # --- 硬件初始化 ---
    if HARDWARE_AVAILABLE:
        motor = StepperMotor(); sensor = IRSensor(); pusher = SorterMotor(steps=400)
    else:
        # 模拟类
        class Mock: 
            def move_steps(self, *a, **k): time.sleep(0.01)
            def is_object_detected(self): return False
            def eject(self): pass
        motor=Mock(); sensor=Mock(); pusher=Mock()

    # --- AI 初始化 ---
    detector = None
    if AI_AVAILABLE:
        try:
            # 加载 best.pt 模型
            detector = DefectDetector(model_path='best.pt')
        except Exception as e:
            print(f"[错误] AI 模型加载失败: {e}")

    # --- 摄像头初始化 ---
    cap = cv2.VideoCapture(0)
    cap.set(3, 640); cap.set(4, 480)
    
    active_items = []
    last_detect = False

    while not sys_state["exit"]:
        if not sys_state["running"]:
            time.sleep(0.1); continue

        # 1. 传送带运动
        if HARDWARE_AVAILABLE: motor.move_steps(BATCH_STEPS, delay=STEP_DELAY)
        else: time.sleep(0.02)
        
        for item in active_items: item['steps'] += BATCH_STEPS
        
        # 2. 传感器检测
        detected = sensor.is_object_detected()
        if not HARDWARE_AVAILABLE and random.random() < 0.005: detected = True # 模拟触发

        # 3. 触发检测逻辑
        if detected and not last_detect:
            with lock: sys_state["log"] = "正在 AI 分析..."
            time.sleep(0.8) # 等待物体停稳
            
            if cap.isOpened():
                for _ in range(3): cap.read() # 清缓存
                ret, frame = cap.read()
                
                if ret:
                    # ====== 核心修改: AI 画框与判定 ======
                    final_img = frame # 默认用原图
                    is_bad = False
                    res_str = "检测中"
                    
                    if detector:
                        # 调用 AI，获取画了框的 annotated_img
                        is_bad, res_str, conf, annotated_img = detector.detect(frame)
                        final_img = annotated_img # 替换为画框图
                        log_msg = f"AI判定: {res_str}"
                    else:
                        # 无 AI 时的随机模拟
                        is_bad = random.choice([True, False])
                        res_str = "模拟NG" if is_bad else "模拟OK"
                        log_msg = f"模拟判定: {res_str}"
                    
                    # 保存 (保存的是带框的图)
                    path = save_snapshot(final_img)
                    curr_time = time.strftime("%H:%M:%S")
                    
                    # 更新状态
                    with lock:
                        sys_state["img"] = path
                        sys_state["res"] = res_str
                        sys_state["time"] = curr_time
                        sys_state["log"] = log_msg
                        if is_bad: sys_state["bad"] += 1
                        else: sys_state["good"] += 1
                        
                        sys_state["new_records"].append({
                            "time": curr_time,
                            "result": res_str,
                            "operator": sys_state["user"]
                        })
                    
                    # 上传
                    threading.Thread(target=upload_task, args=(path, res_str)).start()
                    active_items.append({'is_bad': is_bad, 'steps': 0})
        
        last_detect = detected

        # 4. 剔除逻辑
        for i in range(len(active_items)-1, -1, -1):
            item = active_items[i]
            if item['is_bad'] and item['steps'] >= 6000:
                pusher.eject(); active_items.pop(i)
            elif not item['is_bad'] and item['steps'] > 8000:
                active_items.pop(i)
                
    if cap: cap.release()

# ================= 7. UI 界面层 =================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("软包锂电池缺陷检测与分类系统")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=COLOR_BG)
        self.tk_img = None; self.last_img = None
        
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure("Treeview", background=COLOR_PANEL, foreground="white", fieldbackground=COLOR_PANEL, borderwidth=0, rowheight=35, font=("Microsoft YaHei UI", 12))
        self.style.configure("Treeview.Heading", background=COLOR_INPUT, foreground="white", font=("Microsoft YaHei UI", 14, "bold"), relief="flat")
        self.style.map("Treeview", background=[('selected', COLOR_HIGHLIGHT)])
        
        self.show_login()

    def show_login(self):
        """登录界面 - 手动输入账号"""
        for w in self.root.winfo_children(): w.destroy()
        
        header = tk.Frame(self.root, bg=COLOR_BG)
        header.place(relx=0.5, rely=0.15, anchor="center")
        tk.Label(header, text="软包锂电池缺陷检测系统", font=("Microsoft YaHei UI", 42, "bold"), bg=COLOR_BG, fg=COLOR_TEXT_MAIN).pack(pady=5)
        tk.Label(header, text="[ 系统运行正常 | 等待操作员登录 ]", font=("Microsoft YaHei UI", 16), bg=COLOR_BG, fg=COLOR_BTN_SUCCESS).pack(pady=5)
        
        self.lbl_login_time = tk.Label(header, text="", font=("Microsoft YaHei UI", 14), bg=COLOR_BG, fg=COLOR_TEXT_DIM)
        self.lbl_login_time.pack(pady=5)
        self.update_login_time_loop()

        panel = tk.Frame(self.root, bg=COLOR_PANEL, padx=80, pady=60)
        panel.place(relx=0.5, rely=0.58, anchor="center")
        
        # 账号
        row1 = tk.Frame(panel, bg=COLOR_PANEL)
        row1.pack(fill="x", pady=20)
        tk.Label(row1, text="操作账号: ", font=("Microsoft YaHei UI", 18, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM, width=10, anchor="e").pack(side="left")
        self.entry_user = tk.Entry(row1, font=("Microsoft YaHei UI", 18), width=19, bg=COLOR_INPUT, fg="white", relief="flat", insertbackground="white")
        self.entry_user.pack(side="left", ipady=8, padx=2)

        # 密码
        row2 = tk.Frame(panel, bg=COLOR_PANEL)
        row2.pack(fill="x", pady=20)
        tk.Label(row2, text="授权密码: ", font=("Microsoft YaHei UI", 18, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM, width=10, anchor="e").pack(side="left")
        self.entry_pwd = tk.Entry(row2, show="*", font=("Microsoft YaHei UI", 18), width=19, bg=COLOR_INPUT, fg="white", relief="flat", insertbackground="white")
        self.entry_pwd.pack(side="left", ipady=8, padx=2)

        # 按钮
        btn_frame = tk.Frame(panel, bg=COLOR_PANEL)
        btn_frame.pack(fill="x", pady=(40, 0))
        tk.Button(btn_frame, text="确认登录", font=("Microsoft YaHei UI", 20, "bold"), bg=COLOR_BTN_MAIN, fg="white", relief="flat", width=12, command=self.do_login).pack(side="left", padx=20)
        tk.Button(btn_frame, text="关闭系统", font=("Microsoft YaHei UI", 20), bg=COLOR_BTN_DANGER, fg="white", relief="flat", width=8, command=self.root.destroy).pack(side="right", padx=20)
        
        tk.Label(self.root, text="智能制造物联网实验室 内部专用 V2.0", font=("Microsoft YaHei UI", 12), bg=COLOR_BG, fg="#475569").pack(side="bottom", pady=20)

    def update_login_time_loop(self):
        try:
            current_time = time.strftime("当前日期: %Y-%m-%d  %H:%M:%S")
            self.lbl_login_time.config(text=current_time)
            self.root.after(1000, self.update_login_time_loop)
        except: pass

    def do_login(self):
        user = self.entry_user.get().strip()
        pwd = self.entry_pwd.get().strip()
        if not user:
            messagebox.showwarning("提示", "请输入操作账号")
            return
        if verify_login_on_server(user, pwd):
            sys_state["user"] = user
            for w in self.root.winfo_children(): w.destroy()
            self.show_main()
            threading.Thread(target=worker_thread, daemon=True).start()
        else:
            messagebox.showerror("登录失败", "密码错误 或 账号不存在")

    def show_main(self):
        top = tk.Frame(self.root, bg=COLOR_PANEL, height=80)
        top.pack(fill="x")
        tk.Label(top, text="生产监控终端", font=("Microsoft YaHei UI", 24, "bold"), bg=COLOR_PANEL, fg=COLOR_HIGHLIGHT).pack(side="left", padx=30, pady=15)
        info_frame = tk.Frame(top, bg=COLOR_PANEL)
        info_frame.pack(side="right", padx=20)
        tk.Label(info_frame, text=f"操作员: {sys_state['user']}", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg="white").pack(side="left", padx=15)
        tk.Button(info_frame, text="注销退出", font=("Microsoft YaHei UI", 12), bg=COLOR_BTN_DANGER, fg="white", relief="flat", command=self.logout).pack(side="right")

        main = tk.Frame(self.root, bg=COLOR_BG)
        main.pack(fill="both", expand=True, padx=20, pady=20)

        # 左侧
        left_f = tk.Frame(main, bg=COLOR_BG)
        left_f.place(relx=0, rely=0, relwidth=0.65, relheight=1)
        tk.Label(left_f, text="实时影像采集", font=("Microsoft YaHei UI", 16), bg=COLOR_BG, fg=COLOR_TEXT_DIM).pack(anchor="w", pady=(0,5))
        self.img_lbl = tk.Label(left_f, bg="black", text="等待感应器触发...", font=("Microsoft YaHei UI", 24), fg="#64748b")
        self.img_lbl.pack(fill="both", expand=True)

        # 详细信息框
        info_box = tk.Frame(left_f, bg=COLOR_PANEL, bd=1, relief="solid")
        info_box.pack(side="bottom", fill="x", pady=(20, 0), ipady=10)
        info_inner = tk.Frame(info_box, bg=COLOR_PANEL)
        info_inner.pack(anchor="c", pady=10)
        
        tk.Label(info_inner, text="检测时间: ", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).grid(row=0, column=0, sticky="e", padx=10, pady=5)
        self.lbl_info_time = tk.Label(info_inner, text="--:--:--", font=("Microsoft YaHei UI", 16, "bold"), bg=COLOR_PANEL, fg="white")
        self.lbl_info_time.grid(row=0, column=1, sticky="w", padx=10)

        tk.Label(info_inner, text="检测结果: ", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).grid(row=1, column=0, sticky="e", padx=10, pady=5)
        self.lbl_info_res = tk.Label(info_inner, text="等待中", font=("Microsoft YaHei UI", 16, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM)
        self.lbl_info_res.grid(row=1, column=1, sticky="w", padx=10)

        tk.Label(info_inner, text="检测人员: ", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).grid(row=2, column=0, sticky="e", padx=10, pady=5)
        tk.Label(info_inner, text=sys_state['user'], font=("Microsoft YaHei UI", 16, "bold"), bg=COLOR_PANEL, fg="white").grid(row=2, column=1, sticky="w", padx=10)

        # 右侧
        right_f = tk.Frame(main, bg=COLOR_BG)
        right_f.place(relx=0.67, rely=0, relwidth=0.33, relheight=1)

        res_card = tk.Frame(right_f, bg=COLOR_PANEL, padx=10, pady=20)
        res_card.pack(fill="x", pady=(0, 20))
        tk.Label(res_card, text="当前判定结果", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).pack()
        self.lbl_res_big = tk.Label(res_card, text="等待检测", font=("Microsoft YaHei UI", 60, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM)
        self.lbl_res_big.pack(pady=10)
        self.lbl_log = tk.Label(res_card, text="设备就绪", font=("Microsoft YaHei UI", 14), bg=COLOR_PANEL, fg=COLOR_HIGHLIGHT)
        self.lbl_log.pack()

        stat_card = tk.Frame(right_f, bg=COLOR_PANEL, padx=10, pady=20)
        stat_card.pack(fill="x", pady=(0, 20))
        tk.Label(stat_card, text="当班产量统计", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).pack(pady=(0,15))
        
        s_row = tk.Frame(stat_card, bg=COLOR_PANEL)
        s_row.pack(fill="x")
        ok_box = tk.Frame(s_row, bg=COLOR_PANEL)
        ok_box.pack(side="left", expand=True)
        tk.Label(ok_box, text="合格品", font=("Microsoft YaHei UI", 14), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).pack()
        self.lbl_ok = tk.Label(ok_box, text="0", font=("Microsoft YaHei UI", 36, "bold"), bg=COLOR_PANEL, fg=COLOR_BTN_SUCCESS)
        self.lbl_ok.pack()
        
        ng_box = tk.Frame(s_row, bg=COLOR_PANEL)
        ng_box.pack(side="right", expand=True)
        tk.Label(ng_box, text="不合格", font=("Microsoft YaHei UI", 14), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM).pack()
        self.lbl_ng = tk.Label(ng_box, text="0", font=("Microsoft YaHei UI", 36, "bold"), bg=COLOR_PANEL, fg=COLOR_BTN_DANGER)
        self.lbl_ng.pack()

        ctrl_card = tk.Frame(right_f, bg=COLOR_BG)
        ctrl_card.pack(fill="both", expand=True)
        self.btn_start = tk.Button(ctrl_card, text="启动生产线", font=("Microsoft YaHei UI", 20, "bold"), bg=COLOR_BTN_SUCCESS, fg="white", relief="flat", command=self.start_sys)
        self.btn_start.pack(fill="x", pady=10, ipady=10)
        self.btn_stop = tk.Button(ctrl_card, text="暂停", font=("Microsoft YaHei UI", 20, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM, relief="flat", command=self.stop_sys, state="disabled")
        self.btn_stop.pack(fill="x", pady=10, ipady=10)

        self.update_ui()

    def start_sys(self):
        sys_state["running"] = True
        self.btn_start.config(state="disabled", bg=COLOR_PANEL, fg=COLOR_TEXT_DIM)
        self.btn_stop.config(state="normal", bg=COLOR_BTN_DANGER, fg="white")

    def stop_sys(self):
        sys_state["running"] = False
        self.btn_start.config(state="normal", bg=COLOR_BTN_SUCCESS, fg="white")
        self.btn_stop.config(state="disabled", bg=COLOR_PANEL, fg=COLOR_TEXT_DIM)

    def logout(self):
        sys_state["exit"] = True
        self.root.destroy()

    def update_ui(self):
        with lock:
            path = sys_state["img"]
            if path and path != self.last_img and os.path.exists(path):
                try:
                    img_raw = Image.open(path)
                    try: resample = Image.Resampling.LANCZOS
                    except: resample = Image.ANTIALIAS
                    img_raw.thumbnail((800, 600), resample)
                    self.tk_img = ImageTk.PhotoImage(img_raw)
                    self.img_lbl.config(image=self.tk_img, text="")
                    self.last_img = path
                except: pass
            
            res = sys_state["res"]
            color = COLOR_BTN_SUCCESS if res == "合格" else (COLOR_BTN_DANGER if res == "不合格" else COLOR_TEXT_DIM)
            
            self.lbl_res_big.config(text=res, fg=color)
            self.lbl_info_res.config(text=res, fg=color)
            self.lbl_info_time.config(text=sys_state["time"])
            self.lbl_ok.config(text=f"{sys_state['good']}")
            self.lbl_ng.config(text=f"{sys_state['bad']}")
            self.lbl_log.config(text=sys_state["log"])

        self.root.after(200, self.update_ui)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()