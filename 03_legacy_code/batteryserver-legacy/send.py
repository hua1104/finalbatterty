import requests
import os
import time

# ================= 配置 =================

#  已经帮你填好了刚才查到的 IP
SERVER_IP = "10.174.27.100" 
SERVER_PORT = 5000

# 目标文件夹 (自动扫描这里面的照片)
PHOTO_FOLDER = "/home/pi/conveyor_photos"
URL = f"http://{SERVER_IP}:{SERVER_PORT}/upload"

# ================= 功能函数 =================

def get_latest_photo():
    """找到文件夹里最新的一张照片"""
    if not os.path.exists(PHOTO_FOLDER):
        print(f" [错误] 文件夹不存在: {PHOTO_FOLDER}")
        # 如果文件夹不存在，自动创建一个，防止报错
        os.makedirs(PHOTO_FOLDER)
        print(f"   已自动创建空文件夹，请往里面放张照片测试！")
        return None
    
    # 获取所有 jpg/png 文件
    files = [os.path.join(PHOTO_FOLDER, f) for f in os.listdir(PHOTO_FOLDER) 
             if f.lower().endswith(('.jpg', '.jpeg', '.png'))]
    
    if not files:
        print(f" [警告] 文件夹 {PHOTO_FOLDER} 是空的！")
        print("   -> 请先拍一张照片，或者手动复制一张 jpg 进去。")
        return None
    
    # 按修改时间排序，找最新的
    latest_file = max(files, key=os.path.getmtime)
    return latest_file

def send_photo(file_path):
    filename = os.path.basename(file_path)
    print(f"\n [发送] 正在上传: {filename}")
    print(f"   目标: {URL}")
    
    try:
        with open(file_path, 'rb') as f:
            # 构造数据包
            files = {'file': (filename, f, 'image/jpeg')}
            
            # 发送请求 (设置 5秒超时)
            start_t = time.time()
            response = requests.post(URL, files=files, timeout=5)
            end_t = time.time()
            
        if response.status_code == 200:
            print(f" [成功] 上传完成！耗时: {end_t - start_t:.2f}秒")
            print(f"   服务器回复: {response.json()}")
        else:
            print(f" [失败] 服务器返回状态码: {response.status_code}")

    except requests.exceptions.ConnectTimeout:
        print(" [超时] 电脑没反应！")
        print("   -> 1. 检查电脑防火墙是否关闭 (最常见原因)")
        print("   -> 2. 检查电脑 server.py 是否正在运行")
        print("   -> 3. 校园网可能禁止设备互连 (AP隔离)")
    except requests.exceptions.ConnectionError:
        print(" [错误] 连接被拒绝！")
        print("   -> 电脑 IP 可能变了，或者 server.py 没启动。")
    except Exception as e:
        print(f" [异常] {e}")

# ================= 主程序 =================

if __name__ == "__main__":
    print("=== 实战图片上传程序 ===")
    print(f"目标服务器: {SERVER_IP}")
    print(f"扫描目录: {PHOTO_FOLDER}")
    
    while True:
        input("\n 按回车键，发送【最新】的一张照片...")
        
        # 1. 找照片
        photo_path = get_latest_photo()
        
        # 2. 如果找到了，就发送
        if photo_path:
            send_photo(photo_path)