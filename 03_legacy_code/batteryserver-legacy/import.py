import mysql.connector
import json

# 云服务器数据库配置
config = {
    'host': 'localhost',
    'user': 'root',
    'password': '6af6887566cc5fca', 
    'database': 'battery_sys'
}

def import_now():
    try:
        with open('db_data.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        conn = mysql.connector.connect(**config)
        cursor = conn.cursor()
        
        # 1. 导入用户 (使用 INSERT IGNORE 避免重复)
        for u in data['users']:
            cursor.execute("INSERT IGNORE INTO users (id, username, password, role) VALUES (%s, %s, %s, %s)", 
                           (u['id'], u['username'], u['password'], u['role']))
        
        # 2. 导入记录
        for r in data['records']:
            cursor.execute("INSERT IGNORE INTO detection_records (id, detection_time, operator, result, image_path, confidence, is_reviewed) VALUES (%s, %s, %s, %s, %s, %s, %s)", 
                           (r['id'], r['detection_time'], r['operator'], r['result'], r['image_path'], r['confidence'], r['is_reviewed']))
        
        conn.commit()
        print(f"🚀 成功搬运 {len(data['users'])} 个用户和 {len(data['records'])} 条检测记录！")
        conn.close()
    except Exception as e:
        print(f"❌ 出错啦: {e}")

if __name__ == '__main__':
    import_now()