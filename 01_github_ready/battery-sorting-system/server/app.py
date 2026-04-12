import os
import sys
import cv2
import numpy as np
from flask import Flask, request, jsonify
from ultralytics import YOLO

app = Flask(__name__)

# 加载模型
MODEL_PATH = "best.pt"
model = YOLO(MODEL_PATH)
print(f"[Cloud] YOLOv13 Brain Loaded on Port 5050")

@app.route('/predict', methods=['POST'])
def predict():
    try:
        file = request.files['file']
        img_bytes = file.read()
        nparr = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # 执行推理
        results = model.predict(img, imgsz=640, conf=0.4, verbose=False)[0]
        
        is_bad = False
        res_str = "Qualified" # 默认为合格
        box_coords = []
        conf = 0.0

        # 核心逻辑修正：只要检测到框，即为不合格
        if len(results.boxes) > 0:
            is_bad = True
            best_box = results.boxes[results.boxes.conf.argmax()]
            conf = float(best_box.conf)
            class_id = int(best_box.cls)
            
            # 类别映射（请根据你训练时的标签顺序核对）
            if class_id == 0: res_str = "NG-Wrinkle"  # 褶皱
            elif class_id == 1: res_str = "NG-Scratch" # 划痕
            else: res_str = "NG-Other"
            
            box_coords = [int(x) for x in best_box.xyxy[0].tolist()]

        return jsonify({
            "is_bad": is_bad, 
            "res_str": res_str, 
            "conf": round(conf, 3), 
            "box": box_coords
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # 强制使用 5050 端口隔离
    app.run(host='0.0.0.0', port=5050)