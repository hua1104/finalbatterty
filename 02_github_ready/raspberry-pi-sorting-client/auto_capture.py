import cv2
import time
import os
import sys
import numpy as np
from ir_sensor import IRSensor

# --- 配置参数 ---
# 照片保存路径
SAVE_FOLDER = "/home/pi/conveyor_photos"

# 摄像头分辨率 (1080p)
FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

# 摄像头索引
CAMERA_INDEX = 0

def ensure_folder_exists(folder):
    """创建并检查文件夹权限"""
    try:
        if not os.path.exists(folder):
            os.makedirs(folder)
            print(f"[系统] 文件夹已创建: {folder}")
            os.chmod(folder, 0o777)
        return True
    except Exception as e:
        print(f"[错误] 无法访问文件夹: {folder} - {e}")
        return False

def enhance_image_fast(image):
    """
    快速画质增强 (适合 1080p 抓拍)
    仅保留 CLAHE 和 锐化，去除耗时的降噪，保证速度。
    """
    # 1. CLAHE (智能对比度 - 改善光照不均)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    cl = clahe.apply(l)
    limg = cv2.merge((cl, a, b))
    enhanced = cv2.cvtColor(limg, cv2.COLOR_LAB2BGR)

    # 2. 适度锐化 (让边缘更清晰)
    # 锐化核
    kernel = np.array([[0, -1, 0], 
                       [-1, 5, -1], 
                       [0, -1, 0]])
    final_img = cv2.filter2D(enhanced, -1, kernel)

    return final_img

def main():
    print("[系统] 1080p 自动抓拍系统启动...")

    # 1. 检查路径
    if not ensure_folder_exists(SAVE_FOLDER):
        return

    # 2. 初始化传感器
    sensor = None
    try:
        sensor = IRSensor()
    except Exception as e:
        print(f"[错误] 传感器初始化失败: {e}")
        return

    # 3. 初始化摄像头
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print(f"[错误] 无法打开摄像头")
        return

    # 设置 1080p
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    
    # 打印实际分辨率以确认
    real_w = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    real_h = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"[系统] 摄像头分辨率已设置为: {int(real_w)} x {int(real_h)}")
    print("[系统] 等待红外触发 (按 'q' 退出)...")

    # 窗口
    cv2.namedWindow('1080p Capture', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('1080p Capture', 960, 540) # 预览窗口缩小一半，方便查看

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("[警告] 丢帧")
                break

            # --- 传感器触发逻辑 ---
            if sensor.is_object_detected():
                print("\n[触发] 检测到物体！")
                
                # 1. 快速增强画质 (处理原图 frame)
                # 如果觉得处理太慢，可以注释掉下面这行，直接用 final_frame = frame
                final_frame = enhance_image_fast(frame)
                
                # 2. 生成文件名并保存
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"img_{timestamp}_1080p.jpg"
                full_path = os.path.join(SAVE_FOLDER, filename)
                
                success = cv2.imwrite(full_path, final_frame)
                
                if success:
                    print(f"[保存] {filename} 保存成功")
                    # 屏幕反馈
                    cv2.putText(frame, f"SAVED: {filename}", (50, 100), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
                else:
                    print(f"[失败] 图片保存失败")

                # 显示带文字的反馈画面
                cv2.imshow('1080p Capture', frame)
                cv2.waitKey(1)

                # 3. 防抖延时 (给物体通过的时间)
                print("[等待] 等待物体离开...")
                time.sleep(2.0) 

            else:
                # 待机状态：显示实时画面
                cv2.imshow('1080p Capture', frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    except KeyboardInterrupt:
        print("\n[停止] 用户手动停止")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if sensor:
            sensor.cleanup()

if __name__ == '__main__':
    main()