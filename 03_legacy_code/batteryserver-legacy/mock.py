import mysql.connector
import random
from datetime import datetime, timedelta

DB_CONFIG = { 'host': 'localhost', 'user': 'root', 'password': '3', 'database': 'battery_sys' }

def generate_full_year():
    print("🚀 正在生成 365 天的全量数据，请稍候...")
    conn = mysql.connector.connect(**DB_CONFIG)
    cursor = conn.cursor()
    
    # 清空旧数据 (可选)
    # cursor.execute("TRUNCATE TABLE detection_records")
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365) # 回溯一年
    
    operators = ['张伟', '李强', '王芳', '赵敏', '陈杰']
    defects = ['不合格 (Wrinkle)', '不合格 (Scratch)', '不合格 (Dent)']
    
    current = start_date
    total = 0
    
    while current <= end_date:
        # 每天生成 30-80 条数据，模拟周末产量低
        count = random.randint(30, 80)
        if current.weekday() >= 5: count = random.randint(10, 30) # 周末
        
        # 模拟良率波动 (95% ~ 99%)
        daily_yield = random.uniform(0.95, 0.99)
        
        # 批量插入优化速度
        values = []
        for _ in range(count):
            t = current.replace(hour=random.randint(8,20), minute=random.randint(0,59))
            res = "合格" if random.random() < daily_yield else random.choice(defects)
            op = random.choice(operators)
            # 假图片路径
            path = f"mock_{t.strftime('%Y%m%d%H%M%S')}.jpg"
            values.append((t, op, res, path))
            
        cursor.executemany("INSERT INTO detection_records (detection_time, operator, result, image_path) VALUES (%s, %s, %s, %s)", values)
        total += count
        current += timedelta(days=1)
        
    conn.commit()
    conn.close()
    print(f"✅ 生成完毕！共插入 {total} 条数据。请去网页点击 '1年' 按钮查看效果。")

if __name__ == "__main__":
    generate_full_year()