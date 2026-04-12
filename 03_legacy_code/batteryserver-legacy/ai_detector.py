import cv2
import requests
import numpy as np

class DefectDetector:
    def __init__(self, model_path=None):
        # 指向云端 AI 专属的 5050 端口
        self.api_url = "http://39.106.39.101:5050/predict"
        print(f"[AI] API Connected: {self.api_url}")

    def detect(self, frame, conf_threshold=0.5):
        # 1. 压缩图片
        _, img_encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        
        is_bad = False
        res_str = "Waiting..."
        conf = 0.0
        ann_img = frame.copy()

        try:
            # 2. 发送请求
            files = {'file': ('image.jpg', img_encoded.tobytes(), 'image/jpeg')}
            resp = requests.post(self.api_url, files=files, timeout=3.0)
            
            if resp.status_code == 200:
                data = resp.json()
                is_bad = data.get('is_bad')
                res_str = data.get('res_str') # 这里会拿回 NG-Wrinkle 等
                conf = data.get('conf')
                box = data.get('box', [])

                # 3. 绘制结果（红色代表NG，绿色代表OK）
                color = (0, 0, 255) if is_bad else (0, 255, 0)
                if len(box) == 4 and conf > 0.1:
                    x1, y1, x2, y2 = box
                    cv2.rectangle(ann_img, (x1, y1), (x2, y2), color, 4)
                    # 使用英文标注，彻底解决问号问题
                    cv2.putText(ann_img, f"{res_str} {conf:.2f}", (x1, max(30, y1-10)), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)
            else:
                res_str = "Server Error"
        except Exception as e:
            res_str = "Timeout"

        return is_bad, res_str, float(conf), ann_img