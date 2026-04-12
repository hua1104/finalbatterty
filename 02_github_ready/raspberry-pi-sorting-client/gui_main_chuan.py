import tkinter as tk
from tkinter import messagebox, ttk
import threading
import time
import os
import random

# =================  硬件驱动加载 =================
try:
    from stepper_driver import StepperMotor
    from ir_sensor import IRSensor
    from sorter_motor import SorterMotor
    HARDWARE_AVAILABLE = True
except ImportError:
    print("[警告] 未找到硬件驱动文件，将运行在【模拟模式】")
    HARDWARE_AVAILABLE = False

# =================  全局参数配置 =================
#  电脑服务器配置 (已修改为 Tailscale 虚拟 IP)
SERVER_IP = "100.93.162.20"
SERVER_PORT = 5000
SERVER_URL = f"http://{SERVER_IP}:{SERVER_PORT}/upload"

SAVE_FOLDER = "/home/pi/conveyor_photos"

#  运动参数
STEP_DELAY = 0.0006  # 脉冲间隔
BATCH_STEPS = 50     # 每次循环步数
TARGET_STEPS_SORTER = 6000 # 坏电池剔除距离

# =================  系统全局状态 =================
lock = threading.RLock()

system_state = {
    "is_running": False,
    "good_count": 0,
    "bad_count": 0,
    "latest_log": "系统待机中...",
    "exit_flag": False,
    "history": []
}

# =================  后台控制逻辑 =================

def log_msg(msg):
    """线程安全的日志记录"""
    print(msg)
    with lock:
        system_state["latest_log"] = msg

def upload_task(file_path):
    """后台上传任务"""
    if 'requests' not in globals(): return
    try:
        filename = os.path.basename(file_path)
        with open(file_path, 'rb') as f:
            # 发送请求到电脑
            requests.post(SERVER_URL, files={'file': (filename, f, 'image/jpeg')}, timeout=5)
    except Exception as e:
        print(f"上传失败: {e}")

def save_snapshot(frame, folder):
    """保存图片"""
    if 'cv2' not in globals(): return None
    if not os.path.exists(folder): os.makedirs(folder)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(folder, f"img_{timestamp}.jpg")
    cv2.imwrite(path, frame)
    return path

class BatteryItem:
    def __init__(self, is_bad):
        self.is_bad = is_bad
        self.steps = 0
        self.id = int(time.time() % 10000)

def sorting_thread_func():
    """ 核心控制线程"""
    
    log_msg("[系统] 正在加载视觉核心库...")
    global cv2, requests
    import cv2
    import requests
    
    log_msg("[系统] 正在初始化硬件驱动...")
    if HARDWARE_AVAILABLE:
        motor = StepperMotor()
        sensor = IRSensor()
        pusher = SorterMotor(steps=400)
    else:
        # 模拟器
        class Mock: 
            def move_steps(self, *a, **k): time.sleep(0.001)
            def is_object_detected(self): return False
            def eject(self): pass
            def cleanup(self): pass
        motor = Mock(); sensor = Mock(); pusher = Mock()

    log_msg("[系统] 正在启动摄像头...")
    try:
        cap = cv2.VideoCapture(0)
        cap.set(3, 640)
        cap.set(4, 480)
    except Exception:
        log_msg("[警告] 摄像头启动失败")
        cap = None

    active_batteries = []
    last_sensor_state = False
    log_msg("[系统] 就绪，等待启动")

    while not system_state["exit_flag"]:
        if not system_state["is_running"]:
            time.sleep(0.1)
            continue

        try:
            # A. 传送带运动
            if HARDWARE_AVAILABLE:
                motor.move_steps(BATCH_STEPS, delay=STEP_DELAY)
            else:
                time.sleep(0.02)
            
            for b in active_batteries: b.steps += BATCH_STEPS

            # B. 传感器检测
            is_detected = sensor.is_object_detected()
            if not HARDWARE_AVAILABLE and random.random() < 0.005: is_detected = True

            if is_detected and not last_sensor_state:
                log_msg("[检测] 发现电池，拍照分析中...")
                
                time.sleep(0.5) 
                
                if cap and cap.isOpened():
                    ret, frame = cap.read()
                    if ret:
                        # 1. 保存并在后台上传
                        path = save_snapshot(frame, SAVE_FOLDER)
                        threading.Thread(target=upload_task, args=(path,)).start()
                        
                        # 2. 模拟识别结果
                        is_bad = random.choice([True, False])
                        
                        now_time = time.strftime("%H:%M:%S")
                        status_str = "[次品] 坏电池" if is_bad else "[良品] 好电池"
                        
                        with lock:
                            if is_bad: system_state["bad_count"] += 1
                            else: system_state["good_count"] += 1
                            
                            new_record = {"time": now_time, "id": int(time.time()%10000), "status": status_str}
                            system_state["history"].insert(0, new_record)
                            if len(system_state["history"]) > 50: system_state["history"].pop()
                            
                            log_msg(f"识别结果: {status_str}")

                        active_batteries.append(BatteryItem(is_bad))
            
            last_sensor_state = is_detected

            # C. 分拣剔除逻辑
            for i in range(len(active_batteries)-1, -1, -1):
                b = active_batteries[i]
                if b.is_bad and b.steps >= TARGET_STEPS_SORTER:
                    log_msg(f"[分拣] 执行剔除 (ID:{b.id})")
                    time.sleep(0.2)
                    pusher.eject()
                    active_batteries.pop(i)
                elif not b.is_bad and b.steps > TARGET_STEPS_SORTER + 2000:
                    active_batteries.pop(i)

        except Exception as e:
            log_msg(f"[错误] {e}")

    if cap: cap.release()
    if HARDWARE_AVAILABLE:
        motor.cleanup(); pusher.cleanup()

# =================  UI 界面层 =================

# 配色方案
COLOR_BG = "#2c3e50"
COLOR_PANEL = "#34495e"
COLOR_TEXT = "#ecf0f1"
COLOR_ACCENT = "#3498db"
COLOR_SUCCESS = "#27ae60"
COLOR_DANGER = "#c0392b"

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("智能电池分拣系统 Pro")
        
        # 全屏模式
        self.root.attributes('-fullscreen', True)
        self.root.configure(bg=COLOR_BG)
        
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Treeview", background="white", foreground="black", rowheight=30, font=("Arial", 12))
        style.configure("Treeview.Heading", font=("微软雅黑", 14, "bold"))

        self.show_login()

    def show_login(self):
        """带虚拟键盘的登录界面"""
        self.login_frame = tk.Frame(self.root, bg=COLOR_BG)
        self.login_frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(self.login_frame, text="系统管理员登录", font=("微软雅黑", 24, "bold"), bg=COLOR_BG, fg=COLOR_TEXT).pack(pady=15)
        
        self.entry_pass = tk.Entry(self.login_frame, show="*", font=("Arial", 28), width=10, justify="center")
        self.entry_pass.pack(pady=10)
        
        # 虚拟数字键盘
        keypad_frame = tk.Frame(self.login_frame, bg=COLOR_BG)
        keypad_frame.pack(pady=10)

        keys = [
            ('1', 0, 0), ('2', 0, 1), ('3', 0, 2),
            ('4', 1, 0), ('5', 1, 1), ('6', 1, 2),
            ('7', 2, 0), ('8', 2, 1), ('9', 2, 2),
            ('退格', 3, 0), ('0', 3, 1), ('确认', 3, 2)
        ]

        for (text, row, col) in keys:
            if text == '确认':
                c_bg = COLOR_SUCCESS
                cmd = self.check_login
            elif text == '退格':
                c_bg = COLOR_DANGER
                cmd = self.backspace_key
            else:
                c_bg = "#34495e"
                cmd = lambda t=text: self.add_key(t)

            tk.Button(keypad_frame, text=text, font=("Arial", 20, "bold"), 
                      bg=c_bg, fg="white", width=5, height=2,
                      activebackground="#95a5a6", command=cmd).grid(row=row, column=col, padx=5, pady=5)

    def add_key(self, char):
        self.entry_pass.insert(tk.END, char)

    def backspace_key(self):
        current = self.entry_pass.get()
        self.entry_pass.delete(0, tk.END)
        self.entry_pass.insert(0, current[:-1])

    def check_login(self):
        if self.entry_pass.get() == "123456":
            self.login_frame.destroy()
            self.show_dashboard()
            t = threading.Thread(target=sorting_thread_func, daemon=True)
            t.start()
        else:
            messagebox.showerror("错误", "密码错误！")
            self.entry_pass.delete(0, tk.END)

    def show_dashboard(self):
        """主控制台"""
        header = tk.Frame(self.root, bg="#1abc9c", height=60)
        header.pack(fill="x")
        tk.Label(header, text="智能电池分拣监控终端", font=("微软雅黑", 20, "bold"), bg="#1abc9c", fg="white").pack(pady=15)
        
        tk.Button(header, text="退出", font=("Arial", 12), bg=COLOR_DANGER, fg="white", 
                  command=self.quit_app).place(relx=0.9, rely=0.5, anchor="center")

        content = tk.Frame(self.root, bg=COLOR_BG)
        content.pack(expand=True, fill="both", padx=20, pady=20)

        # 左侧：数据看板
        panel_left = tk.Frame(content, bg=COLOR_PANEL, bd=2, relief="groove")
        panel_left.pack(side="left", fill="both", expand=True, padx=10)
        
        tk.Label(panel_left, text="良品 (OK)", font=("微软雅黑", 16), bg=COLOR_PANEL, fg="#bdc3c7").pack(pady=15)
        self.lbl_good = tk.Label(panel_left, text="0", font=("Arial", 60, "bold"), bg=COLOR_PANEL, fg=COLOR_SUCCESS)
        self.lbl_good.pack()
        
        tk.Label(panel_left, text="---------", bg=COLOR_PANEL, fg="#7f8c8d").pack(pady=10)
        
        tk.Label(panel_left, text="次品 (NG)", font=("微软雅黑", 16), bg=COLOR_PANEL, fg="#bdc3c7").pack(pady=15)
        self.lbl_bad = tk.Label(panel_left, text="0", font=("Arial", 60, "bold"), bg=COLOR_PANEL, fg=COLOR_DANGER)
        self.lbl_bad.pack()

        # 右侧：控制与日志
        panel_right = tk.Frame(content, bg=COLOR_BG)
        panel_right.pack(side="right", fill="both", expand=True, padx=10)

        tk.Label(panel_right, text="系统实时状态:", font=("微软雅黑", 12), bg=COLOR_BG, fg="white").pack(anchor="w")
        self.lbl_status = tk.Label(panel_right, text="初始化...", font=("微软雅黑", 14), bg="#34495e", fg="#f1c40f", 
                                   width=24, height=3, relief="sunken")
        self.lbl_status.pack(fill="x", pady=(5, 20))

        btn_opts = {"font": ("微软雅黑", 16, "bold"), "fg": "white", "height": 2, "relief": "raised"}
        
        self.btn_start = tk.Button(panel_right, text="启动流水线", bg=COLOR_SUCCESS, command=self.start_system, **btn_opts)
        self.btn_start.pack(fill="x", pady=8)

        self.btn_stop = tk.Button(panel_right, text="停止流水线", bg=COLOR_DANGER, state="disabled", command=self.stop_system, **btn_opts)
        self.btn_stop.pack(fill="x", pady=8)

        self.btn_history = tk.Button(panel_right, text="查看历史记录", bg=COLOR_ACCENT, command=self.open_history_window, **btn_opts)
        self.btn_history.pack(fill="x", pady=8)

        self.update_ui()

    def open_history_window(self):
        win = tk.Toplevel(self.root)
        win.title("检测历史")
        win.geometry("600x400")
        x = self.root.winfo_x() + 100
        y = self.root.winfo_y() + 50
        win.geometry(f"+{x}+{y}")

        cols = ("time", "id", "status")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("time", text="时间")
        tree.heading("id", text="ID")
        tree.heading("status", text="结果")
        
        tree.column("time", anchor="center")
        tree.column("id", anchor="center")
        tree.column("status", anchor="center")
        
        scrollbar = ttk.Scrollbar(win, orient="vertical", command=tree.yview)
        tree.configure(yscroll=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        
        with lock:
            for item in system_state["history"]:
                tree.insert("", "end", values=(item["time"], f"#{item['id']}", item["status"]))
        
        tk.Button(win, text="关闭", font=("Arial", 14), command=win.destroy, height=2).pack(fill="x")

    def start_system(self):
        system_state["is_running"] = True
        self.btn_start.config(state="disabled", bg="#7f8c8d")
        self.btn_stop.config(state="normal", bg=COLOR_DANGER)
        log_msg("[系统] 流水线已启动")

    def stop_system(self):
        system_state["is_running"] = False
        self.btn_start.config(state="normal", bg=COLOR_SUCCESS)
        self.btn_stop.config(state="disabled", bg="#7f8c8d")
        log_msg("[系统] 流水线已暂停")

    def quit_app(self):
        if messagebox.askyesno("确认", "确定要退出系统吗？"):
            system_state["exit_flag"] = True
            self.root.destroy()

    def update_ui(self):
        with lock:
            self.lbl_good.config(text=str(system_state['good_count']))
            self.lbl_bad.config(text=str(system_state['bad_count']))
            self.lbl_status.config(text=system_state["latest_log"])
        
        self.root.after(200, self.update_ui)

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    # root.config(cursor="none") # 如需隐藏鼠标请取消注释
    try:
        root.mainloop()
    except KeyboardInterrupt:
        system_state["exit_flag"] = True