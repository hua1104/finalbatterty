from flask import Flask, request
import os

app = Flask(__name__)

#  设置你要求的绝对路径
# 使用 r'' 原始字符串防止反斜杠转义
UPLOAD_FOLDER = r"C:\Users\jin\Desktop\BatteryServer\received_images"

# 如果文件夹不存在，则自动创建
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)
    print(f" 已创建目录: {UPLOAD_FOLDER}")

@app.route('/upload', methods=['POST'])
def upload_file():
    # 检查请求中是否包含文件
    if 'file' not in request.files:
        return "No file part", 400
    
    file = request.files['file']
    
    if file.filename == '':
        return "No selected file", 400

    if file:
        # 拼接完整保存路径
        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)
        print(f" 成功接收并保存图片: {file.filename}")
        print(f" 保存位置: {file_path}")
        return "Upload Success", 200

if __name__ == '__main__':
    # 监听 0.0.0.0 确保 Tailscale 的虚拟网卡请求能进入
    print(f" 服务器启动中，图片将存入: {UPLOAD_FOLDER}")
    app.run(host='0.0.0.0', port=5000)