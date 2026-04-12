import mysql.connector

DB_CONFIG = { 'host': 'localhost', 'user': 'root', 'password': '3', 'database': 'battery_sys' }

def add_index():
    print("🚀 正在为数据库添加极速索引...")
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        # 添加索引，如果已存在会报错（没关系，catch住即可）
        sql = "CREATE INDEX idx_time ON detection_records(detection_time)"
        cursor.execute(sql)
        print("✅ 索引创建成功！查询速度理论提升 50-100 倍。")
    except Exception as e:
        print(f"ℹ️ 提示: {e}")
        print("这通常意味着索引已经存在，无需重复创建。")
    finally:
        if 'conn' in locals() and conn.is_connected(): conn.close()

if __name__ == "__main__":
    add_index()