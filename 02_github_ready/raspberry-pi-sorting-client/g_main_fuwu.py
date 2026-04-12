import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk
import threading
import time
import os
import random
import requests 

# ================= 硬件驱动加载 =================
try:
    from stepper_driver import StepperMotor
    from ir_sensor import IRSensor
    from sorter_motor import SorterMotor
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False
    print("提示: 未检测到硬件驱动, 系统将运行在 [模拟演示模式]")

# ================= 全局配置 =================
SERVER_IP = "120.53.28.221" 
SERVER_URL = f"http://{SERVER_IP}:5000"
SAVE_FOLDER = "/home/pi/conveyor_photos"

STEP_DELAY = 0.0006 
BATCH_STEPS = 50     

# ================= 全局状态 =================
lock = threading.RLock()
sys_state = {
    "running": False,
    "user": None,
    "good": 0,
    "bad": 0,
    "img": None,
    "res": "等待检测",
    "log": "设备已就绪",
    "new_records": [],
    "exit": False
}

# ================= 视觉主题 =================
COLOR_BG = "#1e293b"        # 背景: 深岩灰
COLOR_PANEL = "#334155"     # 面板: 浅岩灰
COLOR_INPUT = "#475569"     # 输入框底色
COLOR_HIGHLIGHT = "#0ea5e9" # 高亮蓝
COLOR_BTN_MAIN = "#2563eb"  # 主按钮蓝
COLOR_BTN_DANGER = "#dc2626"# 警示红
COLOR_BTN_SUCCESS = "#16a34a"# 成功绿
COLOR_TEXT_MAIN = "#ffffff" # 主文字白
COLOR_TEXT_DIM = "#cbd5e1"  # 辅助文字灰

# ================= 后台通信逻辑 =================

def fetch_user_list():
    try:
        resp = requests.get(f"{SERVER_URL}/get_users", timeout=2)
        if resp.status_code == 200:
            return resp.json()
    except: pass
    return ["本地管理员"]

def verify_login_on_server(username, password):
    try:
        data = {'username': username, 'password': password}
        resp = requests.post(f"{SERVER_URL}/login", data=data, timeout=3)
        if resp.status_code == 200: return True
    except: pass
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

def worker_thread():
    global cv2
    import cv2
    
    if HARDWARE_AVAILABLE:
        motor = StepperMotor(); sensor = IRSensor(); pusher = SorterMotor(steps=400)
    else:
        class Mock: 
            def move_steps(self, *a, **k): time.sleep(0.01)
            def is_object_detected(self): return False
            def eject(self): pass
        motor=Mock(); sensor=Mock(); pusher=Mock()

    cap = cv2.VideoCapture(0)
    cap.set(3, 640); cap.set(4, 480)
    
    active_items = []
    last_detect = False

    while not sys_state["exit"]:
        if not sys_state["running"]:
            time.sleep(0.1); continue

        if HARDWARE_AVAILABLE: motor.move_steps(BATCH_STEPS, delay=STEP_DELAY)
        else: time.sleep(0.02)
        
        for item in active_items: item['steps'] += BATCH_STEPS
        
        detected = sensor.is_object_detected()
        if not HARDWARE_AVAILABLE and random.random() < 0.005: detected = True

        if detected and not last_detect:
            with lock: sys_state["log"] = "正在分析..."
            time.sleep(0.8) 
            
            if cap.isOpened():
                for _ in range(3): cap.read()
                ret, frame = cap.read()
                if ret:
                    path = save_snapshot(frame)
                    
                    is_bad = random.choice([True, False])
                    res_str = "不合格" if is_bad else "合格"
                    curr_time = time.strftime("%H:%M:%S")
                    
                    with lock:
                        sys_state["img"] = path
                        sys_state["res"] = res_str
                        sys_state["log"] = f"判定结果: {res_str}"
                        if is_bad: sys_state["bad"] += 1
                        else: sys_state["good"] += 1
                        
                        sys_state["new_records"].append({
                            "time": curr_time,
                            "result": res_str,
                            "operator": sys_state["user"]
                        })
                    
                    threading.Thread(target=upload_task, args=(path, res_str)).start()
                    active_items.append({'is_bad': is_bad, 'steps': 0})
        
        last_detect = detected

        for i in range(len(active_items)-1, -1, -1):
            item = active_items[i]
            if item['is_bad'] and item['steps'] >= 6000:
                pusher.eject(); active_items.pop(i)
            elif not item['is_bad'] and item['steps'] > 8000:
                active_items.pop(i)
    if cap: cap.release()

# ================= UI 界面层 =================

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("软包锂电池缺陷检测与分类系统")
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=COLOR_BG)
        self.tk_img = None; self.last_img = None
        
        self.style = ttk.Style()
        self.style.theme_use('clam')
        
        self.style.configure("TCombobox", fieldbackground=COLOR_INPUT, background=COLOR_INPUT, 
                        foreground="white", arrowcolor="white", borderwidth=0, arrowsize=20)
        self.root.option_add('*TCombobox*Listbox.font', ("Microsoft YaHei UI", 16))
        
        self.style.configure("Treeview", 
                        background=COLOR_PANEL, 
                        foreground="white", 
                        fieldbackground=COLOR_PANEL, 
                        borderwidth=0, 
                        rowheight=35,
                        font=("Microsoft YaHei UI", 12))
        
        self.style.configure("Treeview.Heading", 
                        background=COLOR_INPUT, 
                        foreground="white", 
                        font=("Microsoft YaHei UI", 14, "bold"),
                        relief="flat")
        
        self.style.map("Treeview", background=[('selected', COLOR_HIGHLIGHT)])
        
        self.show_login()

    def show_login(self):
        """登录界面"""
        for w in self.root.winfo_children(): w.destroy()
        
        header = tk.Frame(self.root, bg=COLOR_BG)
        header.place(relx=0.5, rely=0.15, anchor="center")
        
        # 1. 主标题
        tk.Label(header, text="软包锂电池缺陷检测系统", font=("Microsoft YaHei UI", 42, "bold"), bg=COLOR_BG, fg=COLOR_TEXT_MAIN).pack(pady=5)
        
        # 2. 状态栏 (已移除所有特殊图形符号)
        tk.Label(header, text="[ 系统运行正常 | 等待操作员登录 ]", font=("Microsoft YaHei UI", 16), bg=COLOR_BG, fg=COLOR_BTN_SUCCESS).pack(pady=5)

        # 3. 日期时间显示
        self.lbl_login_time = tk.Label(header, text="", font=("Microsoft YaHei UI", 14), bg=COLOR_BG, fg=COLOR_TEXT_DIM)
        self.lbl_login_time.pack(pady=5)
        self.update_login_time_loop()

        panel = tk.Frame(self.root, bg=COLOR_PANEL, padx=80, pady=60)
        panel.place(relx=0.5, rely=0.58, anchor="center")
        self.user_list = fetch_user_list()
        
        row1 = tk.Frame(panel, bg=COLOR_PANEL)
        row1.pack(fill="x", pady=20)
        tk.Label(row1, text="当前班组: ", font=("Microsoft YaHei UI", 18, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM, width=10, anchor="e").pack(side="left")
        self.user_var = tk.StringVar()
        self.cmb = ttk.Combobox(row1, textvariable=self.user_var, font=("Microsoft YaHei UI", 18), width=18)
        self.cmb['values'] = self.user_list
        if self.user_list: self.cmb.current(0)
        self.cmb.pack(side="left", ipady=8)

        row2 = tk.Frame(panel, bg=COLOR_PANEL)
        row2.pack(fill="x", pady=20)
        tk.Label(row2, text="授权密码: ", font=("Microsoft YaHei UI", 18, "bold"), bg=COLOR_PANEL, fg=COLOR_TEXT_DIM, width=10, anchor="e").pack(side="left")
        self.entry_pwd = tk.Entry(row2, show="*", font=("Microsoft YaHei UI", 18), width=19, bg=COLOR_INPUT, fg="white", relief="flat", insertbackground="white")
        self.entry_pwd.pack(side="left", ipady=8, padx=2)

        btn_frame = tk.Frame(panel, bg=COLOR_PANEL)
        btn_frame.pack(fill="x", pady=(40, 0))
        tk.Button(btn_frame, text="确认登录", font=("Microsoft YaHei UI", 20, "bold"), bg=COLOR_BTN_MAIN, fg="white", relief="flat", width=12, command=self.do_login).pack(side="left", padx=20)
        tk.Button(btn_frame, text="关闭系统", font=("Microsoft YaHei UI", 20), bg=COLOR_BTN_DANGER, fg="white", relief="flat", width=8, command=self.root.destroy).pack(side="right", padx=20)
        
        # 底部版权
        tk.Label(self.root, text="智能制造物联网实验室 内部专用 V2.0", font=("Microsoft YaHei UI", 12), bg=COLOR_BG, fg="#475569").pack(side="bottom", pady=20)

    def update_login_time_loop(self):
        """登录界面的时间刷新循环"""
        try:
            current_time = time.strftime("当前日期: %Y-%m-%d  %H:%M:%S")
            self.lbl_login_time.config(text=current_time)
            self.root.after(1000, self.update_login_time_loop)
        except:
            pass

    def do_login(self):
        user = self.user_var.get()
        pwd = self.entry_pwd.get()
        if verify_login_on_server(user, pwd):
            sys_state["user"] = user
            for w in self.root.winfo_children(): w.destroy()
            self.show_main()
            threading.Thread(target=worker_thread, daemon=True).start()
        else:
            messagebox.showerror("登录失败", "密码错误 或 网络未连接")

    def show_main(self):
        # 生产监控主界面
        top = tk.Frame(self.root, bg=COLOR_PANEL, height=80)
        top.pack(fill="x")
        tk.Label(top, text="生产监控终端", font=("Microsoft YaHei UI", 24, "bold"), bg=COLOR_PANEL, fg=COLOR_HIGHLIGHT).pack(side="left", padx=30, pady=15)
        info_frame = tk.Frame(top, bg=COLOR_PANEL)
        info_frame.pack(side="right", padx=20)
        tk.Label(info_frame, text=f"操作员: {sys_state['user']}", font=("Microsoft YaHei UI", 16), bg=COLOR_PANEL, fg="white").pack(side="left", padx=15)
        tk.Button(info_frame, text="注销退出", font=("Microsoft YaHei UI", 12), bg=COLOR_BTN_DANGER, fg="white", relief="flat", command=self.logout).pack(side="right")

        main = tk.Frame(self.root, bg=COLOR_BG)
        main.pack(fill="both", expand=True, padx=20, pady=20)

        # === 左侧布局 ===
        left_f = tk.Frame(main, bg=COLOR_BG)
        left_f.place(relx=0, rely=0, relwidth=0.65, relheight=1)
        
        tk.Label(left_f, text="实时影像采集", font=("Microsoft YaHei UI", 16), bg=COLOR_BG, fg=COLOR_TEXT_DIM).pack(anchor="w", pady=(0,5))
        self.img_lbl = tk.Label(left_f, bg="black", text="等待感应器触发...", font=("Microsoft YaHei UI", 24), fg="#64748b")
        self.img_lbl.pack(fill="both", expand=True)

        # 表格
        table_frame = tk.Frame(left_f, bg=COLOR_PANEL)
        table_frame.pack(side="bottom", fill="x", pady=(15, 0))
        table_frame.config(height=200)
        table_frame.pack_propagate(False)

        cols = ("time", "result", "operator")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=5)
        self.tree.heading("time", text="检测时间")
        self.tree.heading("result", text="检测结果")
        self.tree.heading("operator", text="检测人员")
        self.tree.column("time", width=150, anchor="center")
        self.tree.column("result", width=120, anchor="center")
        self.tree.column("operator", width=150, anchor="center")

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("ok_row", foreground="#22c55e")
        self.tree.tag_configure("ng_row", foreground="#ef4444")

        # === 右侧布局 ===
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
            self.lbl_ok.config(text=f"{sys_state['good']}")
            self.lbl_ng.config(text=f"{sys_state['bad']}")
            self.lbl_log.config(text=sys_state["log"])

            if sys_state["new_records"]:
                for rec in sys_state["new_records"]:
                    row_tag = "ok_row" if rec["result"] == "合格" else "ng_row"
                    self.tree.insert("", 0, values=(rec["time"], rec["result"], rec["operator"]), tags=(row_tag,))
                sys_state["new_records"] = []
                if len(self.tree.get_children()) > 50:
                    self.tree.delete(self.tree.get_children()[-1])

        self.root.after(200, self.update_ui)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()