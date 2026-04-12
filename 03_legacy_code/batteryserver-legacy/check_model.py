from flask import Flask, render_template_string, request, send_file, redirect, url_for, session, jsonify
from mysql.connector import pooling
import os
import pandas as pd
import io
import json
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'battery_admin_secure_key_2026'

# ================= 1. 系统配置 =================
DB_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': '3', 
    'database': 'battery_sys'
}

# 连接池
try:
    db_pool = pooling.MySQLConnectionPool(pool_name="battery_pool", pool_size=10, pool_reset_session=True, **DB_CONFIG)
except: pass

UPLOAD_FOLDER = r'C:\Users\jin\Desktop\BatteryServer\received_images'
if not os.path.exists(UPLOAD_FOLDER):
    try: os.makedirs(UPLOAD_FOLDER)
    except: pass

def get_db(): return db_pool.get_connection()

# ================= 2. 登录页面 =================
LOGIN_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>系统登录 | 智能生产中控</title>
    <style>
        body { margin: 0; padding: 0; font-family: "Microsoft YaHei", sans-serif; height: 100vh; overflow: hidden; display: flex; justify-content: center; align-items: center; background: #000; }
        .aurora-bg { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: -1; background: linear-gradient(45deg, #0f172a, #1e1b4b); overflow: hidden; }
        .aurora-bg::before { content: ''; position: absolute; top: -50%; left: -50%; width: 200%; height: 200%; background: radial-gradient(circle, rgba(56,189,248,0.1) 0%, transparent 60%), radial-gradient(circle, rgba(16,185,129,0.05) 0%, transparent 50%); animation: rotate 20s linear infinite; }
        @keyframes rotate { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .login-card { background: rgba(30, 41, 59, 0.75); backdrop-filter: blur(20px); border: 1px solid rgba(255, 255, 255, 0.1); padding: 50px 40px; border-radius: 20px; width: 380px; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); text-align: center; }
        h1 { color: #fff; margin-bottom: 5px; font-weight: 300; letter-spacing: 2px; font-size: 24px; }
        p { color: #94a3b8; font-size: 14px; margin-bottom: 30px; letter-spacing: 1px; }
        input { width: 100%; padding: 12px 15px; border-radius: 8px; border: 1px solid #475569; background: rgba(15, 23, 42, 0.6); color: white; outline: none; box-sizing: border-box; font-size: 15px; transition: 0.3s; margin-bottom: 20px; }
        input:focus { border-color: #38bdf8; box-shadow: 0 0 0 2px rgba(56,189,248,0.2); }
        button { width: 100%; padding: 14px; border: none; border-radius: 8px; background: linear-gradient(90deg, #38bdf8, #2563eb); color: white; font-weight: bold; cursor: pointer; transition: 0.3s; font-size: 16px; letter-spacing: 2px; }
        button:hover { filter: brightness(1.1); transform: translateY(-1px); }
        #error-msg { height: 0; overflow: hidden; color: #f87171; font-size: 13px; transition: 0.3s; }
        #error-msg.show { height: 20px; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="aurora-bg"></div>
    <div class="login-card">
        <h1>系统登录</h1>
        <p>电池表面缺陷智能检测系统 v9.0</p>
        <form onsubmit="handleLogin(event)">
            <input type="text" id="username" placeholder="账号" value="admin">
            <input type="password" id="password" placeholder="密码" value="123">
            <button type="submit" id="btn-login">立即进入</button>
            <div id="error-msg"></div>
        </form>
    </div>
    <script>
        async function handleLogin(e) {
            e.preventDefault();
            const btn = document.getElementById('btn-login');
            const err = document.getElementById('error-msg');
            btn.innerHTML = "正在验证..."; btn.style.opacity = "0.7";
            try {
                const res = await fetch('/login_api', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username: document.getElementById('username').value, password: document.getElementById('password').value })
                });
                const data = await res.json();
                if (data.success) {
                    btn.innerHTML = "验证成功"; btn.style.background = "#10b981";
                    setTimeout(() => window.location.href = '/', 500);
                } else {
                    btn.innerHTML = "立即进入"; btn.style.opacity = "1";
                    err.innerText = "⚠ " + data.message; err.classList.add('show');
                }
            } catch (e) { alert("网络连接错误"); btn.innerHTML = "立即进入"; }
        }
    </script>
</body>
</html>
'''

# ================= 3. 核心路由 =================

@app.route('/login', methods=['GET'])
def login_page():
    if 'user' in session: return redirect(url_for('main_app'))
    return render_template_string(LOGIN_HTML)

@app.route('/')
def main_app():
    if 'user' not in session: return redirect(url_for('login_page'))
    # 将用户角色传给前端，控制菜单显示
    user_role = session.get('role', 'operator')
    current_user = session.get('user', 'User')
    return render_template_string(DASHBOARD_HTML, user_role=user_role, current_user=current_user)

# ================= 4. API 接口 (含权限控制) =================

@app.route('/login_api', methods=['POST'])
def login_api():
    try:
        data = request.json
        conn = get_db(); cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users")
        users = cursor.fetchall(); conn.close()
        for user in users:
            if str(user.get('username')) == data.get('username') and str(user.get('password')) == data.get('password'):
                session['user'] = data.get('username')
                # [关键] 记录用户角色，默认为 operator
                session['role'] = user.get('role', 'operator')
                return jsonify({'success': True})
        return jsonify({'success': False, 'message': '账号或密码错误'})
    except Exception as e: return jsonify({'success': False, 'message': str(e)})

@app.route('/api/dashboard_data', methods=['POST'])
def dashboard_data():
    try:
        data = request.json
        start = data.get('start'); end = data.get('end')
        conn = get_db(); cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT COUNT(*) as total, SUM(CASE WHEN result LIKE '%不合格%' THEN 1 ELSE 0 END) as bad FROM detection_records WHERE detection_time BETWEEN %s AND %s", (start+" 00:00:00", end+" 23:59:59"))
        stats = cursor.fetchone()
        
        sql_chart = "SELECT DATE(detection_time) as day, SUM(CASE WHEN result LIKE '%不合格%' THEN 1 ELSE 0 END) as ng_count, SUM(CASE WHEN result NOT LIKE '%不合格%' THEN 1 ELSE 0 END) as ok_count FROM detection_records WHERE detection_time BETWEEN %s AND %s GROUP BY day ORDER BY day ASC"
        cursor.execute(sql_chart, (start+" 00:00:00", end+" 23:59:59"))
        chart_raw = cursor.fetchall()
        conn.close()
        
        total = stats['total'] or 0; bad = stats['bad'] or 0; good = total - bad
        rate = round((good/total*100), 2) if total > 0 else 0.0
        
        c_dates = [str(d['day']) for d in chart_raw]
        c_ok = [int(d['ok_count']) for d in chart_raw]
        c_ng = [int(d['ng_count']) for d in chart_raw]
        c_rate = []
        for d in chart_raw:
            t = int(d['ok_count']) + int(d['ng_count'])
            c_rate.append(round((int(d['ok_count'])/t)*100, 1) if t > 0 else 0)
            
        return jsonify({'success': True, 'stats': {'total': total, 'good': good, 'bad': bad, 'rate': rate}, 'chart': {'dates': c_dates, 'ok': c_ok, 'ng': c_ng, 'rate': c_rate}})
    except Exception as e: return jsonify({'success': False, 'message': str(e)})

@app.route('/api/search_history', methods=['POST'])
def search_history():
    try:
        data = request.json
        sql = "SELECT * FROM detection_records WHERE 1=1"
        params = []
        if data.get('start'): sql += " AND detection_time >= %s"; params.append(data.get('start') + " 00:00:00")
        if data.get('end'): sql += " AND detection_time <= %s"; params.append(data.get('end') + " 23:59:59")
        if data.get('operator'): sql += " AND operator LIKE %s"; params.append(f"%{data.get('operator')}%")
        if data.get('res_type') == 'NG': sql += " AND result LIKE '%不合格%'"
        elif data.get('res_type') == 'OK': sql += " AND result NOT LIKE '%不合格%'"
        
        sql += " ORDER BY detection_time DESC LIMIT 100"
        conn = get_db(); cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, tuple(params))
        results = cursor.fetchall(); conn.close()
        for r in results: r['detection_time'] = str(r['detection_time'])
        return jsonify({'success': True, 'data': results})
    except Exception as e: return jsonify({'success': False, 'message': str(e)})

# --- [权限升级] 用户管理 API (仅管理员可用) ---
@app.route('/api/users', methods=['GET', 'POST', 'DELETE'])
def manage_users():
    # 权限拦截
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '无权操作：仅系统管理员可用'})

    try:
        conn = get_db(); cursor = conn.cursor(dictionary=True)
        
        if request.method == 'GET':
            cursor.execute("SELECT id, username, role FROM users")
            users = cursor.fetchall()
            return jsonify({'success': True, 'data': users})
        
        if request.method == 'POST':
            data = request.json
            uid = data.get('id')
            username = data.get('username')
            password = data.get('password')
            role = data.get('role', 'operator') # 获取角色
            
            if uid: # 修改
                # 只有修改密码时不为空才更新密码，否则只更新角色
                if password:
                    cursor.execute("UPDATE users SET password=%s, role=%s WHERE id=%s", (password, role, uid))
                else:
                    cursor.execute("UPDATE users SET role=%s WHERE id=%s", (role, uid))
            else: # 新增
                cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
                if cursor.fetchone(): return jsonify({'success': False, 'message': '用户已存在'})
                if not password: return jsonify({'success': False, 'message': '密码不能为空'})
                cursor.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, %s)", (username, password, role))
            conn.commit()
            return jsonify({'success': True})
            
        if request.method == 'DELETE':
            uid = request.args.get('id')
            if uid == '1': return jsonify({'success': False, 'message': '无法删除超级管理员'})
            cursor.execute("DELETE FROM users WHERE id=%s", (uid,))
            conn.commit()
            return jsonify({'success': True})
            
    except Exception as e: return jsonify({'success': False, 'message': str(e)})
    finally: conn.close()

# ================= 5. 前端模板 (动态权限渲染) =================
DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>智能生产指挥舱</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root { --bg-dark: #0f172a; --card-bg: #1e293b; --accent: #38bdf8; --text-main: #f8fafc; --text-sub: #94a3b8; --ok-color: #10b981; --ng-color: #ef4444; }
        body { margin: 0; font-family: "Microsoft YaHei", sans-serif; background: var(--bg-dark); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; }
        
        .sidebar { width: 240px; background: #111827; border-right: 1px solid #374151; display: flex; flex-direction: column; }
        .brand { padding: 25px; border-bottom: 1px solid #374151; }
        .nav-item { padding: 15px 25px; cursor: pointer; color: var(--text-sub); transition: 0.2s; border-left: 4px solid transparent; display: flex; align-items: center; gap: 10px; font-size: 15px;}
        .nav-item:hover, .nav-item.active { background: rgba(56,189,248,0.1); color: var(--accent); border-left-color: var(--accent); }

        .main { flex: 1; padding: 30px; overflow-y: auto; position: relative; }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        
        .card { background: var(--card-bg); border-radius: 12px; padding: 20px; border: 1px solid #334155; box-shadow: 0 4px 6px rgba(0,0,0,0.3); margin-bottom: 20px; position: relative; }
        .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 25px; }
        .stat-val { font-size: 36px; font-weight: 700; margin: 5px 0; letter-spacing: 1px; }
        .stat-label { color: var(--text-sub); font-size: 14px; font-weight: 500; }

        .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.7); z-index: 100; display: none; justify-content: center; align-items: center; backdrop-filter: blur(5px); }
        .modal { background: var(--card-bg); width: 400px; padding: 30px; border-radius: 15px; border: 1px solid #334155; box-shadow: 0 20px 50px rgba(0,0,0,0.5); }
        .modal h2 { margin-top: 0; color: var(--accent); }
        
        .quick-date-btn { padding: 4px 10px; background: #334155; color: white; border: none; border-radius: 4px; font-size: 12px; cursor: pointer; margin-left: 5px; }
        .quick-date-btn:hover { background: var(--accent); color: black; }
        input, select { background: #0f172a; border: 1px solid #475569; color: white; padding: 8px 12px; border-radius: 4px; outline: none; width: 100%; box-sizing: border-box; }
        .btn-query, .btn-action { background: var(--accent); color: black; padding: 8px 20px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; }
        .btn-del { background: var(--ng-color); color: white; padding: 5px 10px; border:none; border-radius:4px; cursor:pointer; font-size:12px; }
        .btn-edit { background: #f59e0b; color: white; padding: 5px 10px; border:none; border-radius:4px; cursor:pointer; font-size:12px; margin-right:5px; }

        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
        th { text-align: left; padding: 12px; background: #111827; color: var(--text-sub); border-bottom: 1px solid #334155; position: sticky; top: 0; z-index: 10; }
        td { padding: 12px; border-bottom: 1px solid #334155; }
        .badge { padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: bold; }
        .b-ok { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .b-ng { background: rgba(239, 68, 68, 0.2); color: #f87171; }
        
        .toggle-btn { padding: 6px 15px; border: 1px solid #334155; background: #0f172a; color: var(--text-sub); border-radius: 20px; cursor: pointer; font-size: 13px; }
        .toggle-btn.active { background: var(--accent); color: black; border-color: var(--accent); font-weight: bold; }
        
        .view-section { display: none; animation: fadeIn 0.3s; }
        .view-section.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="brand">
            <h2 style="color:#38bdf8; margin:0;">🔋 智能中控</h2>
            <div style="font-size:12px; color:#94a3b8; margin-top:5px;">
                用户: <span style="color:#fff">{{ current_user }}</span> 
                {% if user_role == 'admin' %}
                <span style="background:var(--accent); color:black; padding:1px 4px; border-radius:3px; font-size:10px;">管理员</span>
                {% else %}
                <span style="background:#475569; color:white; padding:1px 4px; border-radius:3px; font-size:10px;">操作员</span>
                {% endif %}
            </div>
        </div>
        
        <div class="nav-item active" onclick="switchView('dash', this)">📊 生产概览看板</div>
        <div class="nav-item" onclick="switchView('list', this)">📜 历史查询明细</div>
        
        {% if user_role == 'admin' %}
        <div class="nav-item" onclick="switchView('users', this)">👥 人员权限管理</div>
        {% endif %}
        
        <a href="/export" target="_blank" style="text-decoration:none;"><div class="nav-item">📥 导出 Excel 报表</div></a>
        <div style="margin-top:auto; padding:20px;"><a href="/logout" style="color:#f87171; text-decoration:none; font-size:14px;">↺ 退出登录</a></div>
    </div>

    <div class="main">
        <div id="view-dash" class="view-section active">
            <div class="top-bar">
                <h1>生产指挥舱</h1>
                <div style="display:flex; gap:10px; align-items:center;">
                    <div style="font-size:12px; color:#94a3b8;">
                        <button class="quick-date-btn" onclick="quickDate(7)">7天</button>
                        <button class="quick-date-btn" onclick="quickDate(30)">30天</button>
                        <button class="quick-date-btn" onclick="quickDate(365)" style="color:#38bdf8;">1年</button>
                    </div>
                    <input type="date" id="d_start" style="width:130px;">
                    <span style="color:#94a3b8">-</span>
                    <input type="date" id="d_end" style="width:130px;">
                    <button class="btn-query" onclick="loadDashboard()">刷新</button>
                </div>
            </div>
            <div class="grid-4" id="stats-area">
                <div class="card"><div class="stat-label">总产量</div><div class="stat-val" id="val_total">-</div></div>
                <div class="card"><div class="stat-label">良品数</div><div class="stat-val" style="color:var(--ok-color)" id="val_good">-</div></div>
                <div class="card"><div class="stat-label">次品数</div><div class="stat-val" style="color:var(--ng-color)" id="val_bad">-</div></div>
                <div class="card"><div class="stat-label">综合良率</div><div class="stat-val" style="color:#38bdf8" id="val_rate">-</div></div>
            </div>
            <div class="card" style="height: 480px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
                    <h3 style="margin:0; font-size:16px; color:#94a3b8;">可视化分析</h3>
                    <div style="display:flex; gap:10px;">
                        <button class="toggle-btn active" onclick="showChart('bar', this)">📊 堆叠柱状图</button>
                        <button class="toggle-btn" onclick="showChart('line', this)">📈 良率折线图</button>
                    </div>
                </div>
                <div style="height:400px; position:relative;">
                    <canvas id="barChart"></canvas>
                    <canvas id="lineChart" style="display:none;"></canvas>
                </div>
            </div>
        </div>

        <div id="view-list" class="view-section">
            <div class="top-bar"><h1>高级历史查询</h1></div>
            <div class="card">
                <div style="display:flex; gap:15px; flex-wrap:wrap; align-items:flex-end;">
                    <div><label style="font-size:12px; color:#94a3b8;">开始</label><input type="date" id="q_start"></div>
                    <div><label style="font-size:12px; color:#94a3b8;">结束</label><input type="date" id="q_end"></div>
                    <div><label style="font-size:12px; color:#94a3b8;">结果</label><select id="q_res"><option value="All">全部</option><option value="OK">合格</option><option value="NG">不合格</option></select></div>
                    <div><label style="font-size:12px; color:#94a3b8;">操作员</label><input type="text" id="q_op" placeholder="姓名..."></div>
                    <button class="btn-query" onclick="loadHistory()">查询</button>
                </div>
            </div>
            <div class="card" style="padding:0; height:calc(100vh - 300px); overflow-y:auto;">
                <table id="historyTable">
                    <thead><tr><th>检测时间</th><th>操作员</th><th>判定结果</th><th>现场快照</th></tr></thead>
                    <tbody id="tableBody"></tbody>
                </table>
            </div>
        </div>

        {% if user_role == 'admin' %}
        <div id="view-users" class="view-section">
            <div class="top-bar">
                <h1>人员权限管理</h1>
                <button class="btn-query" onclick="openUserModal()">+ 新增操作员</button>
            </div>
            <div class="card" style="padding:0;">
                <table>
                    <thead><tr><th>账号</th><th>权限身份</th><th>操作</th></tr></thead>
                    <tbody id="userBody"></tbody>
                </table>
            </div>
        </div>
        {% endif %}
    </div>

    <div class="modal-overlay" id="userModal">
        <div class="modal">
            <h2 id="modalTitle">新增用户</h2>
            <input type="hidden" id="modal_uid">
            <div style="margin-bottom:15px;">
                <label style="color:#94a3b8; display:block; margin-bottom:5px;">登录账号</label>
                <input type="text" id="modal_username" placeholder="请输入用户名">
            </div>
            <div style="margin-bottom:15px;">
                <label style="color:#94a3b8; display:block; margin-bottom:5px;">登录密码</label>
                <input type="text" id="modal_password" placeholder="请输入密码 (留空则不修改)">
            </div>
            <div style="margin-bottom:25px;">
                <label style="color:#94a3b8; display:block; margin-bottom:5px;">权限身份</label>
                <select id="modal_role">
                    <option value="operator">普通操作员</option>
                    <option value="admin">系统管理员</option>
                </select>
            </div>
            <div style="display:flex; justify-content:flex-end; gap:10px;">
                <button onclick="closeModal()" style="background:#334155; border:none; color:white; padding:8px 15px; border-radius:5px; cursor:pointer;">取消</button>
                <button onclick="saveUser()" class="btn-query">保存</button>
            </div>
        </div>
    </div>

    <script>
        let barChart, lineChart;
        window.onload = function() { quickDate(14); };

        function switchView(id, btn) {
            document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
            document.getElementById('view-' + id).classList.add('active');
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            if(btn) btn.classList.add('active');
            if(id === 'users') loadUsers();
        }

        function quickDate(days) {
            const end = new Date(); const start = new Date(); start.setDate(end.getDate() - days);
            document.getElementById('d_end').value = end.toISOString().split('T')[0];
            document.getElementById('d_start').value = start.toISOString().split('T')[0];
            document.getElementById('q_end').value = end.toISOString().split('T')[0];
            document.getElementById('q_start').value = start.toISOString().split('T')[0];
            loadDashboard(); loadHistory();
        }

        async function loadDashboard() {
            try {
                const res = await fetch('/api/dashboard_data', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ start: document.getElementById('d_start').value, end: document.getElementById('d_end').value })
                });
                const data = await res.json();
                if (data.success) {
                    document.getElementById('val_total').innerText = data.stats.total;
                    document.getElementById('val_good').innerText = data.stats.good;
                    document.getElementById('val_bad').innerText = data.stats.bad;
                    document.getElementById('val_rate').innerText = data.stats.rate + '%';
                    
                    if(barChart) barChart.destroy();
                    const ctx = document.getElementById('barChart').getContext('2d');
                    barChart = new Chart(ctx, {
                        type: 'bar',
                        data: {
                            labels: data.chart.dates,
                            datasets: [
                                { label: '良品', data: data.chart.ok, backgroundColor: '#10b981', stack: 'Stack 0' },
                                { label: '次品', data: data.chart.ng, backgroundColor: '#ef4444', stack: 'Stack 0' }
                            ]
                        },
                        options: { responsive: true, maintainAspectRatio: false, scales: { x: { grid: { display: false } }, y: { grid: { color: '#334155' }, stacked: true } } }
                    });

                    if(lineChart) lineChart.destroy();
                    const ctxLine = document.getElementById('lineChart').getContext('2d');
                    lineChart = new Chart(ctxLine, {
                        type: 'line',
                        data: {
                            labels: data.chart.dates,
                            datasets: [{ label: '良率 %', data: data.chart.rate, borderColor: '#38bdf8', borderWidth: 2, tension: 0.3, fill:true, backgroundColor:'rgba(56,189,248,0.1)' }]
                        },
                        options: { responsive: true, maintainAspectRatio: false, scales: { y: { min: 0, max: 105, grid: { color: '#334155' } } } }
                    });
                }
            } catch(e) {}
        }

        function showChart(type, btn) {
            document.querySelectorAll('.toggle-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('barChart').style.display = type === 'bar' ? 'block' : 'none';
            document.getElementById('lineChart').style.display = type === 'line' ? 'block' : 'none';
        }

        async function loadHistory() {
            try {
                const res = await fetch('/api/search_history', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        start: document.getElementById('q_start').value, end: document.getElementById('q_end').value,
                        res_type: document.getElementById('q_res').value, operator: document.getElementById('q_op').value
                    })
                });
                const data = await res.json();
                const tbody = document.getElementById('tableBody'); tbody.innerHTML = "";
                if(data.success) {
                    data.data.forEach(r => {
                        const isNG = r.result.includes('不合格');
                        tbody.innerHTML += `<tr><td>${r.detection_time}</td><td>${r.operator}</td><td><span class="badge ${isNG?'b-ng':'b-ok'}">${r.result}</span></td><td><a href="/received_images/${r.image_path}" target="_blank">查看图片</a></td></tr>`;
                    });
                }
            } catch(e) {}
        }

        async function loadUsers() {
            const res = await fetch('/api/users');
            const data = await res.json();
            const tbody = document.getElementById('userBody'); tbody.innerHTML = "";
            data.data.forEach(u => {
                const roleName = u.role === 'admin' ? '<span style="color:#38bdf8">系统管理员</span>' : '<span>普通操作员</span>';
                tbody.innerHTML += `<tr>
                    <td>${u.username}</td><td>${roleName}</td>
                    <td>
                        <button class="btn-edit" onclick="openUserModal('${u.id}', '${u.username}', '${u.role}')">修改</button>
                        <button class="btn-del" onclick="deleteUser('${u.id}')">删除</button>
                    </td>
                </tr>`;
            });
        }

        function openUserModal(id=null, name='', role='operator') {
            document.getElementById('modal_uid').value = id || '';
            document.getElementById('modal_username').value = name;
            document.getElementById('modal_password').value = '';
            document.getElementById('modal_role').value = role;
            document.getElementById('modal_username').disabled = !!id; 
            document.getElementById('modalTitle').innerText = id ? "修改用户权限/密码" : "新增用户";
            document.getElementById('userModal').style.display = 'flex';
        }

        function closeModal() { document.getElementById('userModal').style.display = 'none'; }

        async function saveUser() {
            const uid = document.getElementById('modal_uid').value;
            const u = document.getElementById('modal_username').value;
            const p = document.getElementById('modal_password').value;
            const r = document.getElementById('modal_role').value;
            
            if(!u) return alert("账号不能为空");
            
            const res = await fetch('/api/users', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ id: uid, username: u, password: p, role: r })
            });
            const d = await res.json();
            if(d.success) { closeModal(); loadUsers(); alert("保存成功"); }
            else alert(d.message);
        }

        async function deleteUser(id) {
            if(!confirm("确定要删除该用户吗？此操作不可恢复！")) return;
            const res = await fetch('/api/users?id=' + id, { method: 'DELETE' });
            const d = await res.json();
            if(d.success) loadUsers(); else alert(d.message);
        }
    </script>
</body>
</html>
'''

# ================= 6. 功能 API =================
@app.route('/received_images/<path:filename>')
def get_image(filename):
    if not filename or filename == 'None': return "No Image", 404
    path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(path): return "Image Not Found", 404
    return send_file(path)

@app.route('/upload', methods=['POST'])
def upload():
    try:
        f = request.files['file']
        fname = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{f.filename}"
        f.save(os.path.join(UPLOAD_FOLDER, fname))
        conn = get_db(); c = conn.cursor()
        c.execute("INSERT INTO detection_records (operator, result, image_path) VALUES (%s, %s, %s)", 
                  (request.form.get('operator'), request.form.get('result'), fname))
        conn.commit(); conn.close()
        return "OK", 200
    except Exception as e: return str(e), 500

@app.route('/export')
def export_data():
    try:
        if 'user' not in session: return redirect(url_for('login_page'))
        conn = get_db()
        sql = "SELECT id, detection_time, operator, result FROM detection_records ORDER BY detection_time DESC"
        df = pd.read_sql(sql, conn)
        conn.close()
        df.columns = ['检测编号', '检测时间', '操作员', '判定结果']
        df['检测时间'] = df['检测时间'].astype(str)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='检测数据')
        output.seek(0)
        return send_file(output, download_name=f"检测报表_{datetime.now().strftime('%Y%m%d')}.xlsx", as_attachment=True)
    except Exception as e: return f"导出失败: {e}"

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login_page'))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)