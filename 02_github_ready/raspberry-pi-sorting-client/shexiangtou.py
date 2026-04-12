import cv2
import time
import numpy as np

def test_camera():
    print("=======================================")
    print("树莓派工业相机极限测试工具启动...")
    print("=======================================")
    
    # 1. 尝试打开摄像头 (先尝试 0，不行就尝试 1)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print(" 索引 0 打开失败，尝试索引 1...")
        cap = cv2.VideoCapture(1)
        
    if not cap.isOpened():
        print(" 致命错误：无法打开任何摄像头！请检查物理连接。")
        return

    # 2. 设置保底分辨率和强制流格式 (极其重要)
    # 使用 MJPG 缓解树莓派 USB 带宽压力
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    
    # 初始化状态
    is_auto_exposure = True
    manual_exposure_val = 150  # 初始手动曝光预设值
    
    # 强制先进入自动曝光模式 (V4L2 中 3 代表自动)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3) 
    
    print("\n摄像头连接成功！(当前分辨率: 1280x720)")
    print("\n  控制台按键说明 (请确保输入法在英文状态)：")
    print("  [A] 键 : 切换 自动曝光 / 手动曝光")
    print("  [W] 键 : 增加曝光值 (画面变亮，必须在手动模式下)")
    print("  [S] 键 : 减少曝光值 (画面变暗，防拖影，必须在手动模式下)")
    print("  [Q] 键 : 退出测试")
    print("=======================================")

    prev_time = time.time()
    
    while True:
        ret, frame = cap.read()
        if not ret:
            print(" 读取画面失败，正在重试...")
            time.sleep(0.5)
            continue
            
        # 黑屏诊断：计算画面平均亮度 (0-255)
        brightness = np.mean(frame)
        if brightness < 5:
            print(f" 警告: 画面极度欠曝/全黑 (亮度: {brightness:.1f})")

        # 计算 FPS
        curr_time = time.time()
        fps = 1 / (curr_time - prev_time + 0.00001)
        prev_time = curr_time

        # 在画面上绘制实时状态
        mode_text = "AUTO (3)" if is_auto_exposure else f"MANUAL (1)"
        exp_val_text = "---" if is_auto_exposure else str(manual_exposure_val)
        
        cv2.putText(frame, f"FPS: {fps:.1f}", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
        cv2.putText(frame, f"Mode: {mode_text}", (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
        cv2.putText(frame, f"Exposure Val: {exp_val_text}", (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 2)
        cv2.putText(frame, f"Avg Brightness: {brightness:.1f}", (20, 160), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        cv2.putText(frame, "Press 'A' to Toggle | 'W'/'S' to Adjust | 'Q' to Quit", (20, 200), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

        cv2.imshow("Industrial Camera Tuning", frame)

        # 键盘事件监听
        key = cv2.waitKey(1) & 0xFF
        
        if key == ord('q'):
            break
        elif key == ord('a'):
            is_auto_exposure = not is_auto_exposure
            if is_auto_exposure:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3) # 切换为自动
                print(">> 切换至: 自动曝光模式")
            else:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1) # 切换为手动 (V4L2=1)
                cap.set(cv2.CAP_PROP_EXPOSURE, manual_exposure_val)
                print(f">> 切换至: 手动曝光模式 (当前值: {manual_exposure_val})")
                
        elif key == ord('w'):
            if not is_auto_exposure:
                manual_exposure_val += 20
                cap.set(cv2.CAP_PROP_EXPOSURE, manual_exposure_val)
                print(f">> 增加曝光值 -> {manual_exposure_val}")
                
        elif key == ord('s'):
            if not is_auto_exposure:
                # 防止曝光值降到 0 以下报错
                manual_exposure_val = max(10, manual_exposure_val - 20)
                cap.set(cv2.CAP_PROP_EXPOSURE, manual_exposure_val)
                print(f">> 降低曝光值 -> {manual_exposure_val}")

    cap.release()
    cv2.destroyAllWindows()
    print(">> 测试结束，资源已释放。")

if __name__ == "__main__":
    test_camera()