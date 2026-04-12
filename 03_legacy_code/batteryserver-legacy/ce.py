import requests
import time

SERVER_IP = "39.106.39.101"
SERVER_URL = f"http://{SERVER_IP}:5000"

print(f"=======================================")
print(f" 开始测试与服务器的云端连接...")
print(f"目标地址: {SERVER_URL}")
print(f"=======================================")

# --- 测试 1: 基础网页连通性 ---
print("\n[测试 1] 基础网页连通性 (GET /)...")
try:
    start_time = time.time()
    res = requests.get(SERVER_URL, timeout=5)
    ping = int((time.time() - start_time) * 1000)
    print(f"   成功! 状态码: {res.status_code}, 延迟: {ping}ms")
except requests.exceptions.Timeout:
    print("   失败: 请求超时 (Timeout)！可能是网络延迟太高。")
except requests.exceptions.ConnectionError:
    print("   失败: 连接被拒绝！请检查树莓派是否连上网了。")
except Exception as e:
    print(f"   失败: 其他错误 -> {e}")


# --- 测试 2: 模拟树莓派登录 API ---
print("\n[测试 2] 模拟终端 API 登录 (POST /login_api)...")
payload = {"username": "admin", "password": "123"}
headers = {"Content-Type": "application/json"}

try:
    start_time = time.time()
    # 故意把超时设长一点，看是不是因为原来代码的 timeout=2 太短
    res = requests.post(f"{SERVER_URL}/login_api", json=payload, headers=headers, timeout=5)
    ping = int((time.time() - start_time) * 1000)
    print(f"  -> 请求完成! 延迟: {ping}ms, HTTP 状态码: {res.status_code}")
    
    try:
        data = res.json()
        print(f"  -> 服务器返回的数据: {data}")
        
        if data.get("success"):
            print("   结论: 登录 API 工作完全正常！连接失败可能是因为原来代码 timeout=2 秒太短了。")
        else:
            print(f"   结论: 连接是通的，但服务器拒绝了登录！")
            print(f"     原因: {data.get('message')}")
            print(f"     (如果是被限制频率，请等几分钟再试；如果是密码错误，请检查数据库)")
            
    except Exception as e:
        print(f"   结论: API 崩溃了！服务器没有返回 JSON，而是返回了报错页面。")
        print(f"     服务器返回内容的前 200 个字符: {res.text[:200]}")

except requests.exceptions.Timeout:
    print("   失败: API 登录请求超时！")
except Exception as e:
    print(f"   失败: {e}")

print("\n=======================================")