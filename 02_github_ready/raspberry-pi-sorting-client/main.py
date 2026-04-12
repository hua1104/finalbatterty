import cv2
import time
import os
import sys
import random
import threading  # [新增] 多线程支持
import requests   # [新增] 网络请求支持

# 导入驱动
from stepper_driver import StepperMotor
from ir_sensor import IRSensor
from sorter_motor import SorterMotor 

# ================= 参数配置 =================
SAVE_FOLDER = "/home/pi/conveyor_photos"
CAMERA_INDEX = 0
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

# --- [新增] 网络配置 (根据刚才测试成功的 IP) ---
SERVER_IP = "10.174.27.100"
SERVER_PORT = 5000
SERVER_URL = f"http://{SERVER_IP}:{SERVER_PORT}/upload"

# --- 运动参数 ---
STEP_DELAY = 0.0008
BATCH_STEPS = 20

# 核心校准参数 (你之前设置的 6000)
TARGET_STEPS_SORTER = 6000 

# YOLO 等待时间
YOLO_WAIT_TIME = 1.0 # 改小一点，因为现在上传是在后台进行的

# =================  电池追踪类 =================
class BatteryItem:
    def __init__(self, is_bad, image_path):
        self.is_bad = is_bad
        self.steps_travelled = 0 
        self.id = int(time.time() % 10000)
        self.image_path = image_path

# =================  工具函数 =================

def ensure_folder_exists(folder):
    if not os.path.exists(folder):
        os.makedirs(folder)
        os.chmod(folder, 0o777)

def save_snapshot(frame, folder):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"img_{timestamp}.jpg"
    full_path = os.path.join(folder, filename)
    cv2.imwrite(full_path, frame)
    return full_path

# --- [新增] 后台上传任务 ---
def upload_task(file_path):
    """
    这个函数会在后台线程运行，绝对不会卡住电机！
    """
    filename = os.path.basename(file_path)
    # print(f"   [NET] 后台上传中: {filename} ...")
    
    try:
        with open(file_path, 'rb') as f:
            files = {'file': (filename, f, 'image/jpeg')}
            # 发送请求 (5秒超时)
            requests.post(SERVER_URL, files=files, timeout=5)
            print(f"   [NET]  图片上传成功！")
    except Exception as e:
        print(f"   [NET]  上传失败: {e}")

def mock_yolo_inference(image_path):
    print(f"   [AI] 本地分析中...")
    time.sleep(YOLO_WAIT_TIME)
    
    # 调试模式：强制全部为坏电池，方便测试推杆
    is_bad = True 
    print(f"   [TEST] 判定为:  坏电池")
    
    return is_bad

# =================  主程序 =================

def main():
    print("=== 最终版分拣系统 (步数控制 + 网络通信) ===")
    print(f"[配置] 目标服务器: {SERVER_URL}")
    ensure_folder_exists(SAVE_FOLDER)

    # 1. 初始化硬件
    motor = StepperMotor()
    sensor = IRSensor()
    pusher = SorterMotor(steps=400)
    
    cap = cv2.VideoCapture(CAMERA_INDEX)
    cap.set(3, FRAME_WIDTH)
    cap.set(4, FRAME_HEIGHT)
    for _ in range(5): cap.read()

    # 任务列表
    active_batteries = []
    last_sensor_state = False

    print("[SYSTEM] 流水线启动...")

    try:
        while True:
            # --- 1. 传送带运行 (微步进) ---
            motor.move_steps(BATCH_STEPS, delay=STEP_DELAY)
            
            # 更新所有电池位置
            for battery in active_batteries:
                battery.steps_travelled += BATCH_STEPS

            # --- 2. 检查入口 (拍照) ---
            is_detected = sensor.is_object_detected()
            
            if is_detected and not last_sensor_state:
                print(f"\n[DETECT] 电池到达 ->  停车拍照")
                time.sleep(0.5) 
                
                cap.grab()
                ret, frame = cap.read()
                if ret:
                    path = save_snapshot(frame, SAVE_FOLDER)
                    
                    #  [关键] 启动后台线程传图片
                    # 这行代码瞬间完成，不影响后面逻辑
                    threading.Thread(target=upload_task, args=(path,)).start()
                    
                    # 本地逻辑判断
                    is_bad = mock_yolo_inference(path)
                    
                    # 加入队列
                    new_item = BatteryItem(is_bad, path)
                    active_batteries.append(new_item)
                    print(f"   [TRACK] ID:{new_item.id} 加入队列")
                    print(" 恢复运行...")
                
            last_sensor_state = is_detected

            # --- 3. 检查出口 (分拣) ---
            for i in range(len(active_batteries) - 1, -1, -1):
                battery = active_batteries[i]
                
                if battery.is_bad:
                    # 检查是否到达指定步数
                    if battery.steps_travelled >= TARGET_STEPS_SORTER:
                        print(f"\n[SORT] ID:{battery.id} 到位 ->  停车剔除")
                        
                        time.sleep(0.2)
                        pusher.eject()
                        print("   [DONE] 剔除完成")
                        
                        active_batteries.pop(i)
                        print(" 恢复运行...")
                else:
                    # 好电池走远了就移除
                    if battery.steps_travelled > TARGET_STEPS_SORTER + 2000:
                        active_batteries.pop(i)

    except KeyboardInterrupt:
        print("\n[STOP] 用户停止")
    finally:
        if cap: cap.release()
        if motor: motor.cleanup()
        if pusher: pusher.cleanup()

if __name__ == "__main__":
    main()