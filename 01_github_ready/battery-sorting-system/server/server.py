from flask import Flask, render_template_string, request, send_file, redirect, url_for, session, jsonify
from mysql.connector import pooling
from werkzeug.security import generate_password_hash, check_password_hash
from waitress import serve
import os
import pandas as pd
import io
import shutil
import random
from datetime import datetime, timedelta
import time
import logging
from logging.handlers import RotatingFileHandler
import json

def setup_logging():
    try:
        os.makedirs("logs", exist_ok=True)
        logger = logging.getLogger("server")
        logger.setLevel(logging.INFO)
        log_path = os.path.join("logs", "server.log")
        handler = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        handler.setFormatter(formatter)
        if not logger.handlers:
            logger.addHandler(handler)
        return logger
    except Exception:
        return logging.getLogger("server")

logger = setup_logging()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_CONFIG_PATH = os.getenv("SERVER_CONFIG", os.path.join(BASE_DIR, "server_config.json"))

def load_server_config():
    if not os.path.exists(SERVER_CONFIG_PATH):
        return {}
    try:
        with open(SERVER_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        logger.warning("server_config.json root must be an object")
    except Exception as e:
        logger.exception(f"Load server config failed: {e}")
    return {}

SERVER_CONFIG = load_server_config()

def to_bool(val, default=False):
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
    return default

def cfg_get(env_key, json_key, default=None, cast=None):
    if env_key and os.getenv(env_key) is not None:
        val = os.getenv(env_key)
    elif json_key and json_key in SERVER_CONFIG:
        val = SERVER_CONFIG.get(json_key)
    else:
        return default
    if cast:
        try:
            return cast(val)
        except Exception:
            return default
    return val

def get_json():
    data = request.get_json(silent=True)
    if isinstance(data, dict):
        return data
    return {}

def to_int(val, default):
    try:
        return int(val)
    except Exception:
        return default

app = Flask(__name__)
app.secret_key = cfg_get("APP_SECRET_KEY", "app_secret_key", "battery_copyright_final_2026_v10")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if to_bool(cfg_get("SESSION_COOKIE_SECURE", "session_cookie_secure", False)):
    app.config["SESSION_COOKIE_SECURE"] = True

ERROR_LOG = []
ERROR_MAX = 200
DASH_CACHE = {}
DASH_CACHE_TTL = cfg_get("DASH_CACHE_TTL", "dash_cache_ttl", 20, int)
RATE_BUCKET = {}
LOGIN_FAILS = {}

LOGIN_RATE_WINDOW = cfg_get("LOGIN_RATE_WINDOW", "login_rate_window", 60, int)
LOGIN_RATE_MAX = cfg_get("LOGIN_RATE_MAX", "login_rate_max", 10, int)
LOGIN_LOCK_MAX_FAILS = cfg_get("LOGIN_LOCK_MAX_FAILS", "login_lock_max_fails", 5, int)
LOGIN_LOCK_WINDOW = cfg_get("LOGIN_LOCK_WINDOW", "login_lock_window", 600, int)
LOGIN_LOCK_DURATION = cfg_get("LOGIN_LOCK_DURATION", "login_lock_duration", 300, int)
ADMIN_ONLY_DELETE = to_bool(cfg_get("ADMIN_ONLY_DELETE", "admin_only_delete", False))
LOG_PATH = os.path.join("logs", "server.log")
LOG_TAIL_MAX = cfg_get("LOG_TAIL_MAX", "log_tail_max", 300, int)

def log_error(where, err):
    try:
        ERROR_LOG.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "where": where,
            "message": str(err)
        })
        if len(ERROR_LOG) > ERROR_MAX:
            del ERROR_LOG[:len(ERROR_LOG) - ERROR_MAX]
    except Exception:
        pass

def read_log_tail(path, max_lines=200):
    try:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        if max_lines and len(lines) > max_lines:
            lines = lines[-max_lines:]
        return [line.rstrip("\n") for line in lines]
    except Exception as e:
        log_error("log_tail", e)
        return []

def get_client_ip():
    ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    return ip or request.remote_addr or "unknown"

def rate_limit(key, window_sec, max_req):
    now = time.time()
    bucket = RATE_BUCKET.get(key, [])
    bucket = [t for t in bucket if now - t < window_sec]
    if len(bucket) >= max_req:
        RATE_BUCKET[key] = bucket
        return False
    bucket.append(now)
    RATE_BUCKET[key] = bucket
    return True

def is_login_locked(username):
    now = time.time()
    rec = LOGIN_FAILS.get(username)
    if not rec:
        return False
    locked_until = rec.get("locked_until", 0)
    if locked_until and now < locked_until:
        return True
    return False

def record_login_fail(username):
    now = time.time()
    rec = LOGIN_FAILS.get(username, {"count": 0, "first_ts": now, "locked_until": 0})
    if now - rec.get("first_ts", now) > LOGIN_LOCK_WINDOW:
        rec = {"count": 0, "first_ts": now, "locked_until": 0}
    rec["count"] += 1
    if rec["count"] >= LOGIN_LOCK_MAX_FAILS:
        rec["locked_until"] = now + LOGIN_LOCK_DURATION
    LOGIN_FAILS[username] = rec

def clear_login_fail(username):
    if username in LOGIN_FAILS:
        del LOGIN_FAILS[username]

def validate_password(pw):
    if not pw or len(pw) < 6:
        return False, "密码长度至少6位"
    has_letter = any(c.isalpha() for c in pw)
    has_digit = any(c.isdigit() for c in pw)
    if not (has_letter and has_digit):
        return False, "密码需包含字母与数字"
    return True, ""

def audit_log(action, detail=""):
    try:
        user = session.get("user", "unknown")
        ip = get_client_ip()
        logger.info(f"AUDIT action={action} user={user} ip={ip} detail={detail}")
    except Exception:
        pass

# ================= 1. ???? =================
DB_CONFIG = {
    "host": cfg_get("DB_HOST", "db_host", "localhost"),
    "user": cfg_get("DB_USER", "db_user", "root"),
    "password": cfg_get("DB_PASSWORD", "db_password", "6af6887566cc5fca"),
    "database": cfg_get("DB_NAME", "db_name", "battery_sys")
}

db_pool = None

def init_db_pool():
    global db_pool
    if db_pool is not None:
        return True
    try:
        db_pool = pooling.MySQLConnectionPool(pool_name="battery_pool", pool_size=20, pool_reset_session=True, **DB_CONFIG)
        return True
    except Exception as e:
        logger.exception(f"Pool Error: {e}")
        log_error('db_pool', e)
        db_pool = None
        return False

def get_db():
    global db_pool
    if not init_db_pool():
        raise RuntimeError("DB pool unavailable")
    try:
        return db_pool.get_connection()
    except Exception:
        # try re-init once
        db_pool = None
        if init_db_pool():
            return db_pool.get_connection()
        raise

UPLOAD_FOLDER = cfg_get("UPLOAD_FOLDER", "upload_folder", "/www/wwwroot/received_images")
if not os.path.exists(UPLOAD_FOLDER):
    try:
        os.makedirs(UPLOAD_FOLDER)
    except Exception as e:
        logger.exception(f"Create upload dir failed: {e}")
        log_error('upload_dir', e)

# [自动升级数据库]
def upgrade_db_structure():
    print("🔧 正在检查数据库结构完整性...")
    try:
        conn = get_db()
        cursor = conn.cursor()
        try: cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(20) DEFAULT 'operator'")
        except: pass
        try:
            default_pass = '123' 
            cursor.execute("INSERT IGNORE INTO users (username, password, role) VALUES ('admin', %s, 'admin')", (default_pass,))
            cursor.execute("UPDATE users SET role='admin' WHERE username='admin'")
        except: pass
        try: cursor.execute("ALTER TABLE detection_records ADD COLUMN confidence FLOAT DEFAULT 0.95")
        except: pass
        try: cursor.execute("ALTER TABLE detection_records ADD COLUMN is_reviewed TINYINT DEFAULT 0")
        except: pass
        try: cursor.execute("ALTER TABLE detection_records ADD INDEX idx_detection_time (detection_time)")
        except: pass
        conn.commit()
        conn.close()
        print("✅ 数据库结构检查完毕")
    except Exception as e:
        print(f"DB Check Warning: {e}")

# ================= 2. 页面路由 =================
@app.route('/login', methods=['GET'])
def login_page():
    return render_template_string(LOGIN_HTML)

@app.route('/')
def main_app():
    if 'user' not in session: return redirect(url_for('login_page'))
    try:
        total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
        disk_free = round(free / (2**30), 2)
    except: disk_free = 0
    return render_template_string(DASHBOARD_HTML, user_role=session.get('role', 'operator'), current_user=session.get('user', 'User'), disk_free=disk_free)

# ================= 3. API 接口 =================
@app.route('/login_api', methods=['POST'])
def login_api():
    try:
        ip = get_client_ip()
        if not rate_limit(f"login:{ip}", LOGIN_RATE_WINDOW, LOGIN_RATE_MAX):
            return jsonify({'success': False, 'message': '操作过于频繁，请稍后再试'})
        data = get_json()
        username = (data.get('username') or "").strip()
        if is_login_locked(username):
            return jsonify({'success': False, 'message': '登录失败次数过多，请稍后再试'})
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
        user = cursor.fetchone()
        conn.close()
        if user:
            is_valid = False
            if user['password'].startswith('scrypt:') or user['password'].startswith('pbkdf2:'):
                if check_password_hash(user['password'], data.get('password')): is_valid = True
            elif user['password'] == data.get('password'): is_valid = True
            if is_valid:
                session['user'] = user['username']
                session['role'] = user.get('role', 'operator')
                clear_login_fail(username)
                audit_log("login", "success")
                return jsonify({'success': True})
        record_login_fail(username)
        audit_log("login", "failed")
        return jsonify({'success': False, 'message': '账号或密码错误'})
    except Exception as e:
        logger.exception("login_api error")
        log_error('login_api', e)
        return jsonify({'success': False, 'message': '服务器错误'})

@app.route('/upload', methods=['POST'])
def upload():
    try:
        f = request.files.get('file')
        if not f:
            return "No file", 400
        today = datetime.now().strftime('%Y-%m-%d')
        save_dir = os.path.join(UPLOAD_FOLDER, today)
        if not os.path.exists(save_dir): os.makedirs(save_dir)
        fname = f"{datetime.now().strftime('%H%M%S')}_{f.filename}"
        f.save(os.path.join(save_dir, fname))
        client_conf = request.form.get('confidence')
        conf = float(client_conf) if client_conf else round(random.uniform(0.85, 0.999), 4)
        conn = get_db()
        c = conn.cursor()
        operator = request.form.get('operator') or ""
        result = request.form.get('result') or ""
        c.execute("INSERT INTO detection_records (operator, result, image_path, confidence) VALUES (%s, %s, %s, %s)", (operator, result, f"{today}/{fname}", conf))
        conn.commit()
        conn.close()
        return "OK", 200
    except Exception as e:
        logger.exception("upload error")
        log_error('upload', e)
        return str(e), 500

@app.route('/api/delete_record', methods=['POST'])
def delete_record():
    if 'user' not in session: return jsonify({'success': False, 'message': '未登录'})
    if ADMIN_ONLY_DELETE and session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '无权操作'})
    try:
        data = get_json()
        rec_id = data.get('id')
        if not rec_id:
            return jsonify({'success': False, 'message': '缺少记录ID'})
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT image_path FROM detection_records WHERE id=%s", (rec_id,))
        rec = cursor.fetchone()
        if rec:
            cursor.execute("DELETE FROM detection_records WHERE id=%s", (rec_id,))
            conn.commit()
            try:
                full_path = os.path.join(UPLOAD_FOLDER, rec['image_path'])
                if os.path.exists(full_path): os.remove(full_path)
            except: pass
            conn.close()
            audit_log("delete_record", f"id={rec_id}")
            return jsonify({'success': True})
        else:
            conn.close()
            return jsonify({'success': False, 'message': '记录不存在'})
    except Exception as e:
        logger.exception("delete_record error")
        log_error('delete_record', e)
        return jsonify({'success': False, 'message': '服务器错误'})

# ✅ 批量删除接口
@app.route('/api/delete_bulk', methods=['POST'])
def delete_bulk():
    if 'user' not in session: return jsonify({'success': False, 'message': '未登录'})
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '无权操作'})
    try:
        data = get_json()
        start_date = data.get('start')
        end_date = data.get('end')
        if not start_date or not end_date: return jsonify({'success': False, 'message': '请选择日期范围'})
        start_ts = start_date + " 00:00:00"
        end_ts = end_date + " 23:59:59"
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT image_path FROM detection_records WHERE detection_time BETWEEN %s AND %s", (start_ts, end_ts))
        records_to_del = cursor.fetchall()
        count = len(records_to_del)
        if count == 0:
            conn.close()
            return jsonify({'success': True, 'count': 0, 'message': '该时段无数据'})
        cursor.execute("DELETE FROM detection_records WHERE detection_time BETWEEN %s AND %s", (start_ts, end_ts))
        conn.commit()
        conn.close()
        deleted_files = 0
        for rec in records_to_del:
            try:
                full_path = os.path.join(UPLOAD_FOLDER, rec['image_path'])
                if os.path.exists(full_path):
                    os.remove(full_path)
                    deleted_files += 1
            except: pass
        audit_log("delete_bulk", f"start={start_date} end={end_date} count={count}")
        return jsonify({'success': True, 'count': count, 'deleted_files': deleted_files})
    except Exception as e:
        logger.exception("delete_bulk error")
        log_error('delete_bulk', e)
        return jsonify({'success': False, 'message': '服务器错误'})

@app.route('/api/delete_selected', methods=['POST'])
def delete_selected():
    if 'user' not in session: return jsonify({'success': False, 'message': '未登录'})
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '无权操作'})
    try:
        data = get_json()
        ids = data.get('ids')
        if not isinstance(ids, list) or not ids:
            return jsonify({'success': False, 'message': '请选择要删除的记录'})
        clean_ids = []
        for raw in ids:
            try:
                clean_ids.append(int(raw))
            except Exception:
                continue
        clean_ids = list(dict.fromkeys(clean_ids))
        if not clean_ids:
            return jsonify({'success': False, 'message': '记录ID无效'})
        placeholders = ",".join(["%s"] * len(clean_ids))
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT id, image_path FROM detection_records WHERE id IN ({placeholders})", tuple(clean_ids))
        records = cursor.fetchall()
        if not records:
            conn.close()
            return jsonify({'success': True, 'count': 0, 'deleted_files': 0})
        ids_found = [r['id'] for r in records]
        placeholders = ",".join(["%s"] * len(ids_found))
        cursor.execute(f"DELETE FROM detection_records WHERE id IN ({placeholders})", tuple(ids_found))
        conn.commit()
        conn.close()
        deleted_files = 0
        for rec in records:
            try:
                full_path = os.path.join(UPLOAD_FOLDER, rec['image_path'])
                if os.path.exists(full_path):
                    os.remove(full_path)
                    deleted_files += 1
            except Exception:
                pass
        audit_log("delete_selected", f"count={len(ids_found)}")
        return jsonify({'success': True, 'count': len(ids_found), 'deleted_files': deleted_files})
    except Exception as e:
        logger.exception("delete_selected error")
        log_error('delete_selected', e)
        return jsonify({'success': False, 'message': '服务器错误'})

@app.route('/api/search_history', methods=['POST'])
def search_history():
    try:
        data = get_json()
        sql_base = "FROM detection_records WHERE 1=1"
        params = []
        if data.get('start'):
            sql_base += " AND detection_time >= %s"
            params.append(data.get('start') + " 00:00:00")
        if data.get('end'):
            sql_base += " AND detection_time <= %s"
            params.append(data.get('end') + " 23:59:59")
        if data.get('operator'):
            sql_base += " AND operator LIKE %s"
            params.append(f"%{data.get('operator')}%")
        res_type = data.get('res_type')
        if res_type == 'NG': sql_base += " AND result LIKE '%不合格%'"
        elif res_type == 'OK': sql_base += " AND result NOT LIKE '%不合格%'"
        elif res_type and res_type != 'All':
            sql_base += " AND result LIKE %s"
            params.append(f"%{res_type}%")
        page = to_int(data.get('page', 1), 1)
        page_size = to_int(data.get('page_size', 50), 50)
        if page < 1: page = 1
        if page_size < 1: page_size = 50
        if page_size > 200: page_size = 200
        offset = (page - 1) * page_size
        count_sql = "SELECT COUNT(*) as total " + sql_base
        data_sql = "SELECT id, detection_time, operator, result, image_path, confidence, is_reviewed " + sql_base + " ORDER BY detection_time DESC LIMIT %s OFFSET %s"
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(count_sql, tuple(params))
        total_row = cursor.fetchone()
        total = total_row['total'] if total_row else 0
        cursor.execute(data_sql, tuple(params + [page_size, offset]))
        results = cursor.fetchall()
        conn.close()
        for r in results: 
            r['detection_time'] = str(r['detection_time'])
            current_conf = r.get('confidence')
            r['confidence'] = (current_conf * 100) if current_conf is not None else 95.0
        return jsonify({'success': True, 'data': results, 'total': total, 'page': page, 'page_size': page_size})
    except Exception as e:
        logger.exception("search_history error")
        log_error('search_history', e)
        return jsonify({'success': False, 'message': str(e)})

# ✅ 修复了 finally 块的语法错误
@app.route('/api/users', methods=['GET', 'POST', 'DELETE'])
def manage_users():
    if session.get('role') != 'admin': 
        return jsonify({'success': False, 'message': '无权操作'})
    conn = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        if request.method == 'GET':
            cursor.execute("SELECT id, username, role FROM users")
            return jsonify({'success': True, 'data': cursor.fetchall()})
        if request.method == 'POST':
            d = get_json()
            if d.get('id'): 
                if d.get('password'): 
                    ok, msg = validate_password(d.get('password'))
                    if not ok:
                        return jsonify({'success': False, 'message': msg})
                    hashed = generate_password_hash(d['password'])
                    cursor.execute("UPDATE users SET password=%s, role=%s WHERE id=%s", (hashed, d['role'], d['id']))
                else: 
                    cursor.execute("UPDATE users SET role=%s WHERE id=%s", (d['role'], d['id']))
                audit_log("user_update", f"id={d.get('id')}")
            else: 
                cursor.execute("SELECT * FROM users WHERE username=%s", (d['username'],))
                if cursor.fetchone(): 
                    return jsonify({'success': False, 'message': '用户已存在'})
                ok, msg = validate_password(d.get('password'))
                if not ok:
                    return jsonify({'success': False, 'message': msg})
                hashed = generate_password_hash(d['password'])
                cursor.execute("INSERT INTO users (username, password, role) VALUES (%s, %s, %s)", (d['username'], hashed, d['role']))
                audit_log("user_create", f"username={d.get('username')}")
            conn.commit()
            return jsonify({'success': True})
        if request.method == 'DELETE':
            if request.args.get('id') == '1': 
                return jsonify({'success': False, 'message': '不可删除超级管理员'})
            cursor.execute("DELETE FROM users WHERE id=%s", (request.args.get('id'),))
            conn.commit()
            audit_log("user_delete", f"id={request.args.get('id')}")
            return jsonify({'success': True})
    except Exception as e:
        logger.exception("manage_users error")
        log_error('manage_users', e)
        return jsonify({'success': False, 'message': '服务器错误'})
    finally:
        # ✅ 这里已修复
        if conn:
            conn.close()

@app.route('/api/review_record', methods=['POST'])
def review_record():
    if 'user' not in session: return jsonify({'success': False, 'message': '未登录'})
    if ADMIN_ONLY_DELETE and session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '无权操作'})
    try:
        data = get_json()
        if not data.get('id'):
            return jsonify({'success': False, 'message': '缺少记录ID'})
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE detection_records SET result=%s, is_reviewed=1 WHERE id=%s", (data.get('new_result'), data.get('id')))
        conn.commit()
        conn.close()
        audit_log("review_record", f"id={data.get('id')} result={data.get('new_result')}")
        return jsonify({'success': True})
    except Exception as e:
        logger.exception("review_record error")
        log_error('review_record', e)
        return jsonify({'success': False, 'message': '服务器错误'})

@app.route('/api/dashboard_data', methods=['POST'])
def dashboard_data():
    try:
        data = get_json()
        start = data.get('start')
        end = data.get('end')
        if not start or not end:
            return jsonify({'success': False, 'message': '请选择日期范围'})
        cache_key = f"{start}_{end}"
        now = time.time()
        cached = DASH_CACHE.get(cache_key)
        if cached and now - cached.get("ts", 0) < DASH_CACHE_TTL:
            return jsonify({'success': True, 'stats': cached['stats'], 'chart': cached['chart']})
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) as total, SUM(CASE WHEN result LIKE '%不合格%' THEN 1 ELSE 0 END) as bad FROM detection_records WHERE detection_time BETWEEN %s AND %s", (start+" 00:00:00", end+" 23:59:59"))
        stats = cursor.fetchone()
        sql_chart = "SELECT DATE(detection_time) as day, SUM(CASE WHEN result LIKE '%不合格%' THEN 1 ELSE 0 END) as ng_count, SUM(CASE WHEN result NOT LIKE '%不合格%' THEN 1 ELSE 0 END) as ok_count FROM detection_records WHERE detection_time BETWEEN %s AND %s GROUP BY day ORDER BY day ASC"
        cursor.execute(sql_chart, (start+" 00:00:00", end+" 23:59:59"))
        chart_raw = cursor.fetchall()
        conn.close()
        total = stats['total'] or 0
        bad = stats['bad'] or 0
        good = total - bad
        rate = round((good/total*100), 2) if total > 0 else 0.0
        c_dates = [str(d['day']) for d in chart_raw]
        c_ok = [int(d['ok_count']) for d in chart_raw]
        c_ng = [int(d['ng_count']) for d in chart_raw]
        c_rate = []
        for d in chart_raw:
            t = int(d['ok_count']) + int(d['ng_count'])
            c_rate.append(round((int(d['ok_count'])/t)*100, 1) if t > 0 else 0)
        stats_payload = {'total': total, 'good': good, 'bad': bad, 'rate': rate}
        chart_payload = {'dates': c_dates, 'ok': c_ok, 'ng': c_ng, 'rate': c_rate}
        DASH_CACHE[cache_key] = {"ts": now, "stats": stats_payload, "chart": chart_payload}
        return jsonify({'success': True, 'stats': stats_payload, 'chart': chart_payload})
    except Exception as e:
        logger.exception("dashboard_data error")
        log_error('dashboard_data', e)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/system_status', methods=['GET'])
def system_status():
    try:
        db_ok = False
        try:
            conn = get_db()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            conn.close()
            db_ok = True
        except Exception as e:
            log_error('db_check', e)
            db_ok = False
        try:
            total, used, free = shutil.disk_usage(UPLOAD_FOLDER)
            disk_free_gb = round(free / (2**30), 2)
        except Exception:
            disk_free_gb = None
        data = {
            "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "db_ok": db_ok,
            "disk_free_gb": disk_free_gb,
            "errors": ERROR_LOG[-50:]
        }
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        logger.exception("system_status error")
        log_error('system_status', e)
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/logs', methods=['GET'])
def api_logs():
    if 'user' not in session: return jsonify({'success': False, 'message': '未登录'})
    if session.get('role') != 'admin':
        return jsonify({'success': False, 'message': '无权操作'})
    try:
        lines = to_int(request.args.get('lines', 200), 200)
        if lines < 1: lines = 200
        if lines > LOG_TAIL_MAX: lines = LOG_TAIL_MAX
        data = read_log_tail(LOG_PATH, lines)
        return jsonify({'success': True, 'lines': data, 'count': len(data)})
    except Exception as e:
        logger.exception("api_logs error")
        log_error('api_logs', e)
        return jsonify({'success': False, 'message': '服务器错误'})

@app.route('/received_images/<path:filename>')
def get_image(filename):
    path = os.path.join(UPLOAD_FOLDER, filename)
    if os.path.exists(path): return send_file(path)
    return "Not Found", 404

@app.route('/export')
def export_data():
    if 'user' not in session: return redirect(url_for('login_page'))
    try:
        conn = get_db()
        sql = "SELECT id, detection_time, operator, result, confidence, is_reviewed FROM detection_records ORDER BY detection_time DESC"
        df = pd.read_sql(sql, conn)
        conn.close()
        df.columns = ['编号', '时间', '操作员', '结果', 'AI置信度', '已复核']
        df['时间'] = df['时间'].astype(str)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='检测数据')
        output.seek(0)
        return send_file(output, download_name=f"Report_{datetime.now().strftime('%Y%m%d')}.xlsx", as_attachment=True)
    except Exception as e:
        logger.exception("export_data error")
        log_error('export_data', e)
        return f"Error: {e}"

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))

# ================= 4. 前端模板 =================
LOGIN_HTML = '''<!DOCTYPE html><html><head><title>系统登录</title><style>body{margin:0;font-family:"Microsoft YaHei";height:100vh;overflow:hidden;display:flex;justify-content:center;align-items:center;background:#000}.aurora-bg{position:absolute;top:0;left:0;width:100%;height:100%;z-index:-1;background:linear-gradient(45deg,#0f172a,#1e1b4b);overflow:hidden}.aurora-bg::before{content:'';position:absolute;top:-50%;left:-50%;width:200%;height:200%;background:radial-gradient(circle,rgba(56,189,248,0.1)0%,transparent 60%),radial-gradient(circle,rgba(16,185,129,0.05)0%,transparent 50%);animation:rotate 20s linear infinite}@keyframes rotate{0%{transform:rotate(0deg)}100%{transform:rotate(360deg)}}.login-card{background:rgba(30,41,59,0.85);backdrop-filter:blur(20px);border:1px solid rgba(255,255,255,0.1);padding:50px 40px;border-radius:20px;width:380px;text-align:center}h1{color:#fff;margin-bottom:5px;font-weight:300}p{color:#94a3b8;font-size:14px;margin-bottom:30px}input{width:100%;padding:12px;border-radius:8px;border:1px solid #475569;background:rgba(15,23,42,0.6);color:white;margin-bottom:20px}button{width:100%;padding:14px;border-radius:8px;background:linear-gradient(90deg,#38bdf8,#2563eb);color:white;border:none;cursor:pointer;font-weight:bold}#error-msg{color:#f87171;font-size:13px;height:0;overflow:hidden;transition:0.3s}#error-msg.show{height:20px;margin-top:10px}</style></head><body><div class="aurora-bg"></div><div class="login-card"><h1>系统登录</h1><p>锂电池外观缺陷检测与智能分拣系统</p><form onsubmit="handleLogin(event)"><input type="text" id="username" placeholder="请输入管理员账号" autocomplete="off"><input type="password" id="password" placeholder="请输入安全密码" autocomplete="off"><button type="submit" id="btn-login">立即进入</button><div id="error-msg"></div></form></div><script>async function handleLogin(e){e.preventDefault();const btn=document.getElementById('btn-login');const err=document.getElementById('error-msg');const u=document.getElementById('username').value;const p=document.getElementById('password').value;if(!u||!p){err.innerText="⚠ 请输入账号和密码";err.classList.add('show');return}btn.innerHTML="正在验证...";btn.style.opacity="0.7";try{const res=await fetch('/login_api',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});const data=await res.json();if(data.success){btn.innerHTML="验证成功";setTimeout(()=>window.location.href='/',300)}else{btn.innerHTML="立即进入";btn.style.opacity="1";err.innerText="⚠ "+data.message;err.classList.add('show')}}catch(e){alert("网络错误");btn.innerHTML="立即进入"}}</script></body></html>'''

DASHBOARD_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>智能生产指挥舱</title>
    <script src="https://cdn.bootcdn.net/ajax/libs/echarts/5.4.3/echarts.min.js"></script>
    <script>
        // 页面加载前优先读取本地存储的主题设置，避免页面闪烁
        if (localStorage.getItem('theme') === 'light') {
            document.documentElement.classList.add('light-mode');
        }
    </script>
    <style>
        /* 默认：深色夜间模式变量 */
        :root { 
            --bg-dark: #0f172a; 
            --card-bg: #1e293b; 
            --sidebar-bg: #111827;
            --accent: #38bdf8; 
            --text-main: #f8fafc; 
            --text-sub: #94a3b8; 
            --ok-color: #10b981; 
            --ng-color: #ef4444;
            --border-color: #334155;
            --input-bg: #0f172a;
            --th-bg: #111827;
            --btn-trash-bg: #111827;
            --modal-overlay: rgba(0,0,0,0.8);
        }
        /* 切换：浅色白天模式变量 */
        :root.light-mode {
            --bg-dark: #f1f5f9; 
            --card-bg: #ffffff; 
            --sidebar-bg: #ffffff;
            --accent: #0284c7; 
            --text-main: #0f172a; 
            --text-sub: #64748b; 
            --ok-color: #059669; 
            --ng-color: #dc2626;
            --border-color: #cbd5e1;
            --input-bg: #f8fafc;
            --th-bg: #f8fafc;
            --btn-trash-bg: #f1f5f9;
            --modal-overlay: rgba(0,0,0,0.4);
        }

        body { margin: 0; font-family: "Microsoft YaHei", sans-serif; background: var(--bg-dark); color: var(--text-main); display: flex; height: 100vh; overflow: hidden; transition: background-color 0.3s, color 0.3s; }
        .sidebar { width: 240px; background: var(--sidebar-bg); border-right: 1px solid var(--border-color); display: flex; flex-direction: column; transition: 0.3s; }
        .brand { padding: 25px; border-bottom: 1px solid var(--border-color); }
        .nav-item { padding: 15px 25px; cursor: pointer; color: var(--text-sub); transition: 0.2s; border-left: 4px solid transparent; display: flex; align-items: center; gap: 10px; font-size: 15px;}
        .nav-item:hover, .nav-item.active { background: rgba(56,189,248,0.1); color: var(--accent); border-left-color: var(--accent); }
        .main { flex: 1; padding: 30px; overflow-y: auto; position: relative; }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .card { background: var(--card-bg); border-radius: 12px; padding: 20px; border: 1px solid var(--border-color); box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px; position: relative; transition: 0.3s; }
        .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 25px; }
        .stat-val { font-size: 36px; font-weight: 700; margin: 5px 0; letter-spacing: 1px; }
        .stat-label { color: var(--text-sub); font-size: 14px; font-weight: 500; }
        .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--modal-overlay); z-index: 200; display: none; justify-content: center; align-items: center; backdrop-filter: blur(5px); }
        .modal { background: var(--card-bg); width: 400px; padding: 30px; border-radius: 15px; border: 1px solid var(--border-color); box-shadow: 0 20px 50px rgba(0,0,0,0.2); }
        .modal h2 { margin-top: 0; color: var(--accent); }
        .quick-date-btn { padding: 4px 10px; background: var(--border-color); color: var(--text-main); border: 1px solid var(--border-color); border-radius: 4px; font-size: 12px; cursor: pointer; margin-left: 5px; }
        .quick-date-btn:hover { background: var(--accent); color: #fff; border-color: var(--accent); box-shadow: 0 0 0 2px rgba(56,189,248,0.15); }
        input, select { background: var(--input-bg); border: 1px solid var(--border-color); color: var(--text-main); padding: 8px 12px; border-radius: 4px; outline: none; width: 100%; box-sizing: border-box; }
        .btn-query, .btn-action { background: linear-gradient(90deg, #60a5fa, #38bdf8); color: #0b1120; padding: 8px 20px; border: 1px solid rgba(125, 211, 252, 0.6); border-radius: 6px; font-weight: bold; cursor: pointer; box-shadow: 0 4px 10px rgba(56, 189, 248, 0.25); }
        .btn-query:hover, .btn-action:hover { box-shadow: 0 6px 14px rgba(56, 189, 248, 0.35); transform: translateY(-1px); }
        .btn-del { background: var(--ng-color); color: white; padding: 5px 10px; border:1px solid var(--ng-color); border-radius:4px; cursor:pointer; font-size:12px; box-shadow: 0 2px 6px rgba(239,68,68,0.25); }
        .btn-edit { background: #f59e0b; color: #111827; padding: 5px 10px; border:1px solid #fbbf24; border-radius:4px; cursor:pointer; font-size:12px; margin-right:5px; box-shadow: 0 2px 6px rgba(245,158,11,0.25); }
        .btn-trash { background: var(--btn-trash-bg); border: 1px solid var(--border-color); color: var(--text-main); width: 30px; height: 30px; border-radius: 4px; cursor: pointer; display: inline-flex; justify-content: center; align-items: center; transition: 0.2s; font-size: 16px; }
        .btn-trash:hover { border-color: var(--ng-color); color: #fff; background: rgba(239,68,68,0.8); }
        .view-toggle { display: flex; background: var(--input-bg); border-radius: 6px; padding: 2px; border: 1px solid var(--border-color); }
        .view-btn { padding: 5px 12px; cursor: pointer; color: var(--text-sub); border-radius: 4px; font-size: 13px; }
        .view-btn.active { background: var(--border-color); color: var(--text-main); font-weight: bold; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 14px; }
        th { text-align: left; padding: 12px; background: var(--th-bg); color: var(--text-sub); border-bottom: 1px solid var(--border-color); position: sticky; top: 0; z-index: 10; }
        td { padding: 12px; border-bottom: 1px solid var(--border-color); }
        .badge { padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: bold; }
        .b-ok { background: rgba(16, 185, 129, 0.2); color: var(--ok-color); }
        .b-ng { background: rgba(239, 68, 68, 0.2); color: var(--ng-color); }
        .conf-bar-bg { width: 80px; height: 6px; background: var(--border-color); border-radius: 3px; display: inline-block; margin-right: 5px; }
        .conf-bar-fill { height: 100%; border-radius: 3px; }
        .gallery-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 15px; padding: 10px; display: none; }
        .gallery-item { background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 8px; overflow: hidden; transition: 0.2s; position: relative; }
        .gallery-item:hover { transform: translateY(-5px); border-color: var(--accent); box-shadow: 0 10px 20px rgba(0,0,0,0.1); }
        .gallery-img { width: 100%; height: 120px; object-fit: cover; cursor: pointer; }
        .gallery-info { padding: 10px; font-size: 12px; color: var(--text-sub); display:flex; justify-content:space-between; align-items: center; }
        .gallery-tag { position: absolute; top: 5px; right: 5px; font-size: 10px; padding: 2px 6px; border-radius: 3px; font-weight: bold; }
        .lightbox { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: var(--modal-overlay); z-index: 300; display: none; justify-content: center; align-items: center; flex-direction: column; }
        .lightbox img { max-width: 90%; max-height: 80vh; border: 2px solid var(--border-color); box-shadow: 0 0 50px rgba(0,0,0,0.5); }
        .lightbox-close { color: white; font-size: 30px; position: absolute; top: 20px; right: 40px; cursor: pointer; }
        .toggle-btn { padding: 6px 15px; border: 1px solid var(--border-color); background: var(--input-bg); color: var(--text-sub); border-radius: 20px; cursor: pointer; font-size: 13px; }
        .toggle-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); font-weight: bold; box-shadow: 0 0 0 2px rgba(56,189,248,0.15); }
        .toggle-btn:disabled { opacity: 0.45; cursor: not-allowed; }
        .view-section { display: none; animation: fadeIn 0.3s; }
        .view-section.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
        .disk-info { padding: 15px 25px; border-top: 1px solid var(--border-color); font-size: 12px; color: var(--text-sub); }
        .disk-bar-bg { height: 6px; background: var(--border-color); border-radius: 3px; margin-top: 8px; overflow: hidden; }
        .disk-bar-fill { height: 100%; background: var(--ok-color); width: 10%; }
        #barChart, #lineChart { width: 100%; height: 400px; }
        .tag { font-size: 12px; padding: 2px 6px; border-radius: 4px; border: 1px solid var(--border-color); color: var(--text-sub); margin-left: 8px; }
        .tag.ok { color: var(--ok-color); border-color: var(--ok-color); }
        .tag.bad { color: var(--ng-color); border-color: var(--ng-color); }
        .tag.warn { color: #f59e0b; border-color: #f59e0b; }
        .select-col { width: 36px; text-align: center; }
        .select-box { width: 16px; height: 16px; accent-color: var(--accent); cursor: pointer; }
        .select-tools { display: flex; gap: 10px; align-items: center; margin-left: auto; flex-wrap: wrap; }
        .selected-count { font-size: 12px; color: var(--text-sub); }
        .gallery-select { position: absolute; top: 6px; left: 6px; background: var(--card-bg); border: 1px solid var(--border-color); border-radius: 4px; padding: 2px; z-index: 5; }
        .log-table td { font-family: Consolas, "Microsoft YaHei", monospace; font-size: 12px; }
        .log-level { font-weight: bold; }
        .log-level.info { color: var(--accent); }
        .log-level.warn { color: #f59e0b; }
        .log-level.error { color: var(--ng-color); }
        .log-msg { color: var(--text-main); }
        
        /* 主题切换按钮专属样式 */
        .theme-switch-btn { width: 100%; padding: 10px; background: var(--input-bg); border: 1px solid var(--border-color); color: var(--text-main); border-radius: 8px; cursor: pointer; font-weight: bold; transition: 0.3s; margin-bottom: 10px;}
        .theme-switch-btn:hover { border-color: var(--accent); color: var(--accent); }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="brand">
            <h2 style="color:var(--accent); margin:0;">锂电池检测管理平台</h2>
            <div style="font-size:12px; color:var(--text-sub); margin-top:5px;">
                用户: <span style="color:var(--text-main); font-weight:bold;">{{ current_user }}</span> 
                {% if user_role == 'admin' %}
                <span style="background:var(--accent); color:#fff; padding:1px 4px; border-radius:3px; font-size:10px;">管理员</span>
                {% else %}
                <span style="background:var(--text-sub); color:white; padding:1px 4px; border-radius:3px; font-size:10px;">操作员</span>
                {% endif %}
            </div>
        </div>
        <div class="nav-item active" onclick="switchView('dash', this)">📊 生产情况概览</div>
        <div class="nav-item" onclick="switchView('list', this)">📜 历史查询明细</div>
        <div class="nav-item" onclick="switchView('status', this)">🛠 系统状态检测</div>
        
        {% if user_role == 'admin' %}
        <div class="nav-item" onclick="switchView('users', this)">👥 人员权限管理</div>
        <div class="nav-item" onclick="switchView('logs', this)">📄 日志查看</div>
        {% endif %}
        
        <a href="/export" target="_blank" style="text-decoration:none;"><div class="nav-item">📥 导出Excel报表</div></a>
        <div style="margin-top:auto;">
            <div style="padding: 0 20px;">
                <button class="theme-switch-btn" id="themeToggleBtn" onclick="toggleTheme()">☀️ 切换白天模式</button>
            </div>
            <div class="disk-info">
                <div>系统存储 (剩余 {{ disk_free }} GB)</div>
                <div class="disk-bar-bg"><div class="disk-bar-fill" style="width: 80%;"></div></div>
            </div>
            <div style="padding:15px 25px;"><a href="/logout" style="color:var(--ng-color); text-decoration:none; font-size:14px; font-weight:bold;">↺ 退出登录</a></div>
        </div>
    </div>

    <div class="main">
        <div id="view-dash" class="view-section active">
            <div class="top-bar">
                <h1>综合情况概览 <span style="font-size:12px; color:var(--ok-color); margin-left:10px; border:1px solid var(--ok-color); padding:2px 5px; border-radius:4px;">● AI 引擎运行中</span></h1>
                <div style="display:flex; gap:10px; align-items:center;">
                    <div style="font-size:12px; color:var(--text-sub);">
                        <button class="quick-date-btn" onclick="quickDate(7)">7天</button>
                        <button class="quick-date-btn" onclick="quickDate(30)">30天</button>
                        <button class="quick-date-btn" onclick="quickDate(365)">1年</button>
                    </div>
                    <input type="date" id="d_start" style="width:130px;">
                    <span style="color:var(--text-sub)">-</span>
                    <input type="date" id="d_end" style="width:130px;">
                    <button class="btn-query" onclick="loadDashboard()">刷新</button>
                </div>
            </div>
            <div class="grid-4" id="stats-area">
                <div class="card"><div class="stat-label">总产量</div><div class="stat-val" id="val_total">-</div></div>
                <div class="card"><div class="stat-label">良品数</div><div class="stat-val" style="color:var(--ok-color)" id="val_good">-</div></div>
                <div class="card"><div class="stat-label">次品数</div><div class="stat-val" style="color:var(--ng-color)" id="val_bad">-</div></div>
                <div class="card"><div class="stat-label">AI 综合良率</div><div class="stat-val" style="color:var(--accent)" id="val_rate">-</div></div>
            </div>
            <div class="card" style="height: 480px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
                    <h3 style="margin:0; font-size:16px; color:var(--text-sub);">可视化分析</h3>
                    <div style="display:flex; gap:10px;">
                        <button class="toggle-btn active" onclick="showChart('bar', this)">📊 堆叠柱状图</button>
                        <button class="toggle-btn" onclick="showChart('line', this)">📈 合格折线图</button>
                    </div>
                </div>
                <div style="height:400px; position:relative;">
                    <div id="barChart"></div>
                    <div id="lineChart" style="display:none;"></div>
                </div>
            </div>
        </div>

        <div id="view-list" class="view-section">
            <div class="top-bar">
                <h1>AI 历史判定记录</h1>
                <div class="view-toggle">
                    <div class="view-btn active" onclick="toggleListView('list', this)">📜 列表模式</div>
                    <div class="view-btn" onclick="toggleListView('gallery', this)">🖼️ 画廊模式</div>
                </div>
            </div>
            <div class="card">
                <div style="display:flex; gap:15px; flex-wrap:wrap; align-items:flex-end;">
                    <div><label style="font-size:12px; color:var(--text-sub);">开始</label><input type="date" id="q_start"></div>
                    <div><label style="font-size:12px; color:var(--text-sub);">结束</label><input type="date" id="q_end"></div>
                    <div>
                        <label style="font-size:12px; color:var(--text-sub);">缺陷类型</label>
                        <select id="q_res">
                            <option value="All">全部记录</option>
                            <option value="OK">仅合格 (OK)</option>
                            <option value="NG">所有不合格 (NG)</option>
                        </select>
                    </div>
                    <div><label style="font-size:12px; color:var(--text-sub);">操作员</label><input type="text" id="q_op" placeholder="姓名..."></div>
                    <button class="btn-query" onclick="loadHistory(true)">查询</button>
                    {% if user_role == 'admin' %}
                    <div class="select-tools">
                        <span class="selected-count">已选 <span id="selectedCount">0</span> 项</span>
                        <button class="toggle-btn" onclick="selectAllVisible()">全选当前页</button>
                        <button class="toggle-btn" onclick="clearSelection()">清空选择</button>
                        <button class="btn-del" onclick="deleteSelected()">🗑️ 删除选中</button>
                        <button class="btn-del" onclick="deleteBulk()">🗑️ 批量删除 (所选时段)</button>
                    </div>
                    {% endif %}
                </div>
            </div>
            <div class="card" style="padding:0; height:calc(100vh - 340px); overflow-y:auto;">
                <table id="historyTable">
                    <thead><tr>
                        {% if user_role == 'admin' %}
                        <th class="select-col"><input type="checkbox" id="selectAllBox" class="select-box" onclick="toggleSelectAll(this)"></th>
                        {% endif %}
                        <th>检测时间</th><th>操作员</th><th>AI 判定结果</th><th>AI 置信度</th><th>人工复核</th><th>操作</th>
                    </tr></thead>
                    <tbody id="tableBody"></tbody>
                </table>
                <div id="historyGallery" class="gallery-grid"></div>
            </div>
            <div class="card" style="padding:12px; display:flex; justify-content:flex-end; gap:10px; align-items:center;">
                <button class="toggle-btn" id="prevPageBtn" onclick="prevPage()">上一页</button>
                <span id="pageInfo" style="color:var(--text-sub); font-size:12px;">1 / 1</span>
                <button class="toggle-btn" id="nextPageBtn" onclick="nextPage()">下一页</button>
            </div>
        </div>

        <div id="view-status" class="view-section">
            <div class="top-bar">
                <h1>系统状态
                    <span class="tag" id="tag_db">DB: --</span>
                    <span class="tag" id="tag_disk">Disk: --</span>
                    <span class="tag" id="tag_err">Error: --</span>
                </h1>
                <button class="btn-query" onclick="loadStatus()">刷新</button>
            </div>
            <div class="grid-4">
                <div class="card"><div class="stat-label">服务器时间</div><div class="stat-val" id="st_time">-</div></div>
                <div class="card"><div class="stat-label">数据库连接</div><div class="stat-val" id="st_db">-</div></div>
                <div class="card"><div class="stat-label">磁盘剩余(GB)</div><div class="stat-val" id="st_disk">-</div></div>
                <div class="card"><div class="stat-label">异常条数</div><div class="stat-val" id="st_err_count">-</div></div>
            </div>
            <div class="card" style="padding:0; height:calc(100vh - 300px); overflow-y:auto;">
                <table>
                    <thead><tr><th>时间</th><th>模块</th><th>信息</th></tr></thead>
                    <tbody id="errorBody"></tbody>
                </table>
            </div>
        </div>

        {% if user_role == 'admin' %}
        <div id="view-users" class="view-section">
            <div class="top-bar"><h1>人员权限管理</h1><button class="btn-query" onclick="openUserModal()">+ 新增操作员</button></div>
            <div class="card" style="padding:0;">
                <table>
                    <thead><tr><th>账号</th><th>权限身份</th><th>操作</th></tr></thead>
                    <tbody id="userBody"></tbody>
                </table>
            </div>
        </div>
        {% endif %}

        {% if user_role == 'admin' %}
        <div id="view-logs" class="view-section">
            <div class="top-bar">
                <h1>系统日志</h1>
                <div style="display:flex; gap:10px; align-items:center;">
                    <select id="logLines" style="width:120px;">
                        <option value="100">最近 100 行</option>
                        <option value="200" selected>最近 200 行</option>
                        <option value="500">最近 500 行</option>
                    </select>
                    <button class="btn-query" onclick="loadLogs()">刷新</button>
                </div>
            </div>
            <div class="card" style="padding:0; height:calc(100vh - 260px); overflow-y:auto;">
                <table class="log-table">
                    <thead><tr><th>时间</th><th>级别</th><th>内容</th></tr></thead>
                    <tbody id="logBody"></tbody>
                </table>
            </div>
        </div>
        {% endif %}
    </div>

    <div class="modal-overlay" id="userModal">
        <div class="modal">
            <h2 id="modalTitle">新增用户</h2>
            <input type="hidden" id="modal_uid">
            <div style="margin-bottom:15px;"><label style="color:var(--text-sub); display:block; margin-bottom:5px;">登录账号</label><input type="text" id="modal_username" placeholder="请输入用户名"></div>
            <div style="margin-bottom:15px;"><label style="color:var(--text-sub); display:block; margin-bottom:5px;">登录密码</label><input type="text" id="modal_password" placeholder="请输入密码 (留空则不修改)"></div>
            <div style="margin-bottom:25px;"><label style="color:var(--text-sub); display:block; margin-bottom:5px;">权限身份</label><select id="modal_role"><option value="operator">普通操作员</option><option value="admin">系统管理员</option></select></div>
            <div style="display:flex; justify-content:flex-end; gap:10px;"><button onclick="closeModal()" style="background:var(--border-color); color:var(--text-main); border:none; padding:8px 15px; border-radius:5px; cursor:pointer;">取消</button><button onclick="saveUser()" class="btn-query">保存</button></div>
        </div>
    </div>

    <div class="lightbox" id="lightbox" onclick="if(event.target==this)this.style.display='none'">
        <div class="lightbox-close" onclick="this.parentElement.style.display='none'">&times;</div>
        <img id="lightbox-img" src="">
        <div style="margin-top:15px; display:flex; gap:15px;">
            <button class="btn-query" onclick="reviewRecord('合格')">✅ 修正为合格</button>
            <button class="btn-query" style="background:var(--ng-color); color:white;" onclick="reviewRecord('不合格 (人工)')">❌ 修正为不合格</button>
            <button class="btn-query" style="background:var(--border-color); color:var(--text-main);" onclick="deleteRecord(document.getElementById('review_id').value); document.getElementById('lightbox').style.display='none';">🗑️ 删除此记录</button>
        </div>
        <div style="color:var(--text-sub); margin-top:10px; font-size:12px;">* 点击上方按钮进行人工复核修正或删除</div>
        <input type="hidden" id="review_id">
    </div>

    <script>
        let myBarChart, myLineChart;
        let autoRefreshTimer;
        let historyPage = 1;
        let historyPageSize = 50;
        let historyTotal = 0;
        let selectedIds = new Set();
        const isAdmin = {{ 'true' if user_role == 'admin' else 'false' }};
        
        // 主题切换逻辑
        function toggleTheme() {
            document.documentElement.classList.toggle('light-mode');
            const isLight = document.documentElement.classList.contains('light-mode');
            localStorage.setItem('theme', isLight ? 'light' : 'dark');
            document.getElementById('themeToggleBtn').innerText = isLight ? '🌙 切换夜间模式' : '☀️ 切换白天模式';
            // 如果在看板页面，重新渲染图表以适配颜色
            if (document.getElementById('view-dash').classList.contains('active')) {
                loadDashboard(true);
            }
        }

        window.onload = function() { 
            // 初始化主题按钮状态
            if (document.documentElement.classList.contains('light-mode')) {
                document.getElementById('themeToggleBtn').innerText = '🌙 切换夜间模式';
            }

            quickDate(14); 
            autoRefreshTimer = setInterval(() => { 
                if(document.getElementById('view-dash') && document.getElementById('view-dash').classList.contains('active')) {
                    loadDashboard(true); 
                }
            }, 30000);
            
            window.addEventListener('resize', function() {
                if(myBarChart) myBarChart.resize();
                if(myLineChart) myLineChart.resize();
            });
        };

        function switchView(id, btn) {
            document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('view-' + id);
            if(target) target.classList.add('active');
            document.querySelectorAll('.nav-item').forEach(el => el.classList.remove('active'));
            if(btn) btn.classList.add('active');
            if(id === 'users') loadUsers();
            if(id === 'status') loadStatus();
            if(id === 'list') loadHistory(true);
            if(id === 'logs') loadLogs();
            if(id === 'dash') {
                setTimeout(() => {
                    if(myBarChart) myBarChart.resize();
                    if(myLineChart) myLineChart.resize();
                }, 100);
            }
        }

        function showChart(type, btn) {
            document.querySelectorAll('.view-dash .toggle-btn').forEach(b => b.classList.remove('active')); 
            btn.classList.add('active');
            const bar = document.getElementById('barChart');
            const line = document.getElementById('lineChart');
            if(type === 'bar') {
                bar.style.display = 'block'; line.style.display = 'none';
                if(myBarChart) myBarChart.resize();
            } else {
                bar.style.display = 'none'; line.style.display = 'block';
                if(myLineChart) myLineChart.resize();
            }
        }

        function toggleListView(mode, btn) {
            document.querySelectorAll('.view-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById('historyTable').style.display = mode === 'list' ? 'table' : 'none';
            document.getElementById('historyGallery').style.display = mode === 'gallery' ? 'grid' : 'none';
            if (isAdmin) syncSelectionUI();
        }

        function updateSelectedCount() {
            const el = document.getElementById('selectedCount');
            if (el) el.innerText = selectedIds.size;
        }

        function updateSelectAllBox() {
            const master = document.getElementById('selectAllBox');
            if (!master) return;
            const items = Array.from(document.querySelectorAll('.select-item'));
            if (items.length === 0) {
                master.checked = false;
                master.indeterminate = false;
                return;
            }
            const checkedCount = items.filter(i => i.checked).length;
            master.checked = checkedCount === items.length;
            master.indeterminate = checkedCount > 0 && checkedCount < items.length;
        }

        function syncSelectionUI() {
            document.querySelectorAll('.select-item').forEach(cb => {
                const id = cb.getAttribute('data-id');
                cb.checked = selectedIds.has(id);
            });
            updateSelectedCount();
            updateSelectAllBox();
        }

        function onSelectToggle(el) {
            const id = el.getAttribute('data-id');
            if (!id) return;
            if (el.checked) selectedIds.add(id);
            else selectedIds.delete(id);
            syncSelectionUI();
        }

        function selectAllVisible() {
            document.querySelectorAll('.select-item').forEach(cb => {
                const id = cb.getAttribute('data-id');
                if (id) {
                    cb.checked = true;
                    selectedIds.add(id);
                }
            });
            syncSelectionUI();
        }

        function clearSelection() {
            selectedIds.clear();
            syncSelectionUI();
        }

        function toggleSelectAll(master) {
            if (!master) return;
            document.querySelectorAll('.select-item').forEach(cb => {
                const id = cb.getAttribute('data-id');
                cb.checked = master.checked;
                if (id) {
                    if (master.checked) selectedIds.add(id);
                    else selectedIds.delete(id);
                }
            });
            syncSelectionUI();
        }

        function openLightbox(el) {
            const id = el.getAttribute('data-id');
            const src = el.getAttribute('data-src');
            document.getElementById('lightbox-img').src = src;
            document.getElementById('review_id').value = id;
            document.getElementById('lightbox').style.display = 'flex';
        }

        async function reviewRecord(newRes) {
            const id = document.getElementById('review_id').value;
            if(!confirm("确定要将此记录修正为: " + newRes + " 吗？")) return;
            await fetch('/api/review_record', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: id, new_result: newRes }) });
            document.getElementById('lightbox').style.display = 'none';
            loadHistory(); 
        }

        async function deleteRecord(id) {
            if(!confirm("⚠️ 警告：删除后数据和图片将不可恢复！\\n确定要删除此记录吗？")) return;
            try {
                const res = await fetch('/api/delete_record', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: id }) });
                const data = await res.json();
                if(data.success) {
                    if (isAdmin) {
                        selectedIds.delete(String(id));
                        syncSelectionUI();
                    }
                    loadHistory();
                } else alert("删除失败: " + data.message);
            } catch(e) { alert("网络错误"); }
        }

        async function deleteSelected() {
            if (!isAdmin) return;
            if (selectedIds.size === 0) { alert("请先选择要删除的记录"); return; }
            if(!confirm(`⚠️ 严重警告 ⚠️\\n\\n您将删除已选择的 ${selectedIds.size} 条记录及对应图片。\\n此操作无法撤销，是否继续？`)) return;
            try {
                const res = await fetch('/api/delete_selected', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ ids: Array.from(selectedIds) }) });
                const data = await res.json();
                if(data.success) {
                    alert(`✅ 操作成功！\\n共删除 ${data.count} 条记录\\n清理图片 ${data.deleted_files} 个`);
                    clearSelection();
                    loadHistory(true);
                } else { alert("操作失败: " + data.message); }
            } catch(e) { alert("网络错误"); }
        }

        async function deleteBulk() {
            const start = document.getElementById('q_start').value;
            const end = document.getElementById('q_end').value;
            if(!start || !end) { alert("请先选择 开始日期 和 结束日期！"); return; }
            if(!confirm(`⚠️ 严重警告 ⚠️\\n\\n您即将删除从 [${start}] 到 [${end}] 的所有记录！\\n\\n包括：\\n1. 数据库中的检测数据\\n2. 硬盘上的原始图片文件\\n\\n此操作【无法撤销】，是否继续？`)) return;
            try {
                const res = await fetch('/api/delete_bulk', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ start: start, end: end }) });
                const data = await res.json();
                if(data.success) {
                    alert(`✅ 操作成功！\\n共删除了 ${data.count} 条记录\\n清理了 ${data.deleted_files} 个图片文件`);
                    if (isAdmin) clearSelection();
                    loadHistory(); 
                } else { alert("操作失败: " + data.message); }
            } catch(e) { alert("网络错误"); }
        }

        function quickDate(days) {
            const end = new Date(); const start = new Date(); start.setDate(end.getDate() - days);
            const eStr = end.toISOString().split('T')[0];
            const sStr = start.toISOString().split('T')[0];
            document.getElementById('d_end').value = eStr; document.getElementById('d_start').value = sStr;
            document.getElementById('q_end').value = eStr; document.getElementById('q_start').value = sStr;
            loadDashboard(); loadHistory(true);
        }

        async function loadDashboard(silent=false) {
            if (typeof echarts === 'undefined') { if(!silent) console.warn("ECharts 加载失败"); }
            try {
                const res = await fetch('/api/dashboard_data', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ start: document.getElementById('d_start').value, end: document.getElementById('d_end').value }) });
                const data = await res.json();
                if (data.success) {
                    document.getElementById('val_total').innerText = data.stats.total;
                    document.getElementById('val_good').innerText = data.stats.good;
                    document.getElementById('val_bad').innerText = data.stats.bad;
                    document.getElementById('val_rate').innerText = data.stats.rate + '%';
                    
                    // 动态获取当前主题下的 Echarts 配色
                    const isLight = document.documentElement.classList.contains('light-mode');
                    const txtCol = isLight ? '#64748b' : '#94a3b8';
                    const splitCol = isLight ? '#e2e8f0' : '#334155';
                    const axisCol = isLight ? '#cbd5e1' : '#475569';

                    if (typeof echarts !== 'undefined') {
                        if(myBarChart) myBarChart.dispose(); // 销毁重建以完美应用新颜色
                        myBarChart = echarts.init(document.getElementById('barChart'));
                        const barOption = {
                            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                            legend: { textStyle: { color: txtCol } },
                            grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
                            xAxis: { type: 'category', data: data.chart.dates, axisLine: { lineStyle: { color: axisCol } }, axisLabel: { color: txtCol } },
                            yAxis: { type: 'value', splitLine: { lineStyle: { color: splitCol } }, axisLabel: { color: txtCol } },
                            series: [
                                { name: '良品', type: 'bar', stack: 'total', itemStyle: { color: '#10b981' }, data: data.chart.ok },
                                { name: '次品', type: 'bar', stack: 'total', itemStyle: { color: '#ef4444' }, data: data.chart.ng }
                            ]
                        };
                        myBarChart.setOption(barOption);
                        
                        if(myLineChart) myLineChart.dispose();
                        myLineChart = echarts.init(document.getElementById('lineChart'));
                        const lineOption = {
                            tooltip: { trigger: 'axis', formatter: '{b}<br/>良率: {c}%' },
                            grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
                            xAxis: { type: 'category', boundaryGap: false, data: data.chart.dates, axisLine: { lineStyle: { color: axisCol } }, axisLabel: { color: txtCol } },
                            yAxis: { type: 'value', min: 0, max: 105, splitLine: { lineStyle: { color: splitCol } }, axisLabel: { color: txtCol } },
                            series: [{
                                name: '良率', type: 'line', smooth: true,
                                lineStyle: { color: '#38bdf8', width: 3 },
                                areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(56,189,248,0.5)' }, { offset: 1, color: 'rgba(56,189,248,0.01)' }]) },
                                itemStyle: { color: '#38bdf8' },
                                data: data.chart.rate
                            }]
                        };
                        myLineChart.setOption(lineOption);
                    }
                }
            } catch(e) { console.error(e); }
        }

        async function loadHistory(resetPage=false) {
            if (resetPage) historyPage = 1;
            if (resetPage && isAdmin) {
                selectedIds.clear();
                updateSelectedCount();
            }
            try {
                const res = await fetch('/api/search_history', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ start: document.getElementById('q_start').value, end: document.getElementById('q_end').value, res_type: document.getElementById('q_res').value, operator: document.getElementById('q_op').value, page: historyPage, page_size: historyPageSize }) });
                const data = await res.json();
                const tbody = document.getElementById('tableBody'); tbody.innerHTML = "";
                const gallery = document.getElementById('historyGallery'); gallery.innerHTML = "";
                if(data.success) { 
                    historyTotal = data.total || 0;
                    const totalPages = Math.max(1, Math.ceil(historyTotal / historyPageSize));
                    if (historyPage > totalPages) historyPage = totalPages;
                    const pageInfo = document.getElementById('pageInfo');
                    if (pageInfo) pageInfo.innerText = `${historyPage} / ${totalPages}`;
                    const prevBtn = document.getElementById('prevPageBtn');
                    const nextBtn = document.getElementById('nextPageBtn');
                    if (prevBtn) prevBtn.disabled = historyPage <= 1;
                    if (nextBtn) nextBtn.disabled = historyPage >= totalPages;
                    data.data.forEach(r => { 
                        const isNG = r.result.includes('不合格');
                        const imgSrc = `/received_images/${r.image_path}`;
                        const badgeClass = isNG ? 'b-ng' : 'b-ok';
                        const confVal = r.confidence.toFixed(1);
                        const confColor = confVal < 90 ? '#f59e0b' : '#10b981';
                        const reviewStatus = r.is_reviewed ? '<span style="color:var(--ok-color)">已复核</span>' : '<span style="color:var(--text-sub)">未复核</span>';
                        const idStr = String(r.id);
                        const checked = selectedIds.has(idStr) ? 'checked' : '';
                        const selectCell = isAdmin ? `<td class="select-col"><input type="checkbox" class="select-box select-item" data-id="${idStr}" ${checked} onchange="onSelectToggle(this)"></td>` : '';
                        const selectBadge = isAdmin ? `<label class="gallery-select" onclick="event.stopPropagation();"><input type="checkbox" class="select-box select-item" data-id="${idStr}" ${checked} onchange="onSelectToggle(this)" onclick="event.stopPropagation();"></label>` : '';
                        
                        tbody.innerHTML += `<tr>
                            ${selectCell}
                            <td>${r.detection_time}</td>
                            <td>${r.operator}</td>
                            <td><span class="badge ${badgeClass}">${r.result}</span></td>
                            <td><div style="display:flex; align-items:center;"><div class="conf-bar-bg"><div class="conf-bar-fill" style="width:${confVal}%; background:${confColor}"></div></div><span style="font-size:12px; color:${confColor}">${confVal}%</span></div></td>
                            <td>${reviewStatus}</td>
                            <td style="display:flex; gap:10px; align-items:center;">
                                <a href="javascript:void(0)" data-id="${r.id}" data-src="${imgSrc}" onclick="openLightbox(this)" style="color:var(--accent)">查看</a>
                                <button class="btn-trash" onclick="deleteRecord('${r.id}')" title="删除记录">🗑️</button>
                            </td>
                        </tr>`;
                        
                        gallery.innerHTML += `<div class="gallery-item">
                            ${selectBadge}
                            <span class="gallery-tag ${badgeClass}" style="background:${isNG?'var(--ng-color)':'var(--ok-color)'}; color:white;">${r.result} ${confVal}%</span>
                            <img src="${imgSrc}" class="gallery-img" data-id="${r.id}" data-src="${imgSrc}" onclick="openLightbox(this)">
                            <div class="gallery-info"><span>${r.detection_time.split(' ')[0]}</span><button class="btn-trash" onclick="deleteRecord('${r.id}')" style="width:24px;height:24px;font-size:14px;">🗑️</button></div>
                        </div>`;
                    }); 
                    if (isAdmin) syncSelectionUI();
                }
            } catch(e) {}
        }

        async function loadStatus() {
            try {
                const res = await fetch('/api/system_status');
                const data = await res.json();
                if(!data.success) return;
                const s = data.data;
                document.getElementById('st_time').innerText = s.server_time || '-';
                document.getElementById('st_db').innerText = s.db_ok ? '正常' : '异常';
                document.getElementById('st_db').style.color = s.db_ok ? 'var(--ok-color)' : 'var(--ng-color)';
                document.getElementById('st_disk').innerText = s.disk_free_gb ?? '-';
                document.getElementById('st_err_count').innerText = (s.errors || []).length;
                const tagDb = document.getElementById('tag_db');
                const tagDisk = document.getElementById('tag_disk');
                const tagErr = document.getElementById('tag_err');
                if(tagDb) {
                    tagDb.innerText = s.db_ok ? 'DB: 正常' : 'DB: 异常';
                    tagDb.className = 'tag ' + (s.db_ok ? 'ok' : 'bad');
                }
                if(tagDisk) {
                    const free = s.disk_free_gb;
                    if (free == null) {
                        tagDisk.innerText = 'Disk: --';
                        tagDisk.className = 'tag';
                    } else if (free < 10) {
                        tagDisk.innerText = 'Disk: 紧张';
                        tagDisk.className = 'tag warn';
                    } else {
                        tagDisk.innerText = 'Disk: 正常';
                        tagDisk.className = 'tag ok';
                    }
                }
                if(tagErr) {
                    const count = (s.errors || []).length;
                    tagErr.innerText = `Error: ${count}`;
                    tagErr.className = 'tag ' + (count > 0 ? 'warn' : 'ok');
                }
                const tbody = document.getElementById('errorBody');
                tbody.innerHTML = '';
                const items = (s.errors || []).slice().reverse();
                if (items.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="3" style="color:var(--text-sub); text-align:center;">暂无异常记录</td></tr>`;
                } else {
                    items.forEach(e => {
                        tbody.innerHTML += `<tr><td>${e.time}</td><td>${e.where}</td><td>${e.message}</td></tr>`;
                    });
                }
            } catch(e) {}
        }

        function escapeHtml(str) {
            if (str == null) return '';
            return String(str)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function parseLogLine(line) {
            const m = line.match(/^(\d{4}-\d{2}-\d{2}[^[]*)\s+\[([A-Z]+)\]\s+(.*)$/);
            if (!m) return { time: '-', level: '-', msg: line };
            return { time: m[1].trim(), level: m[2], msg: m[3] };
        }

        async function loadLogs() {
            if (!isAdmin) return;
            try {
                const sel = document.getElementById('logLines');
                const lines = sel ? sel.value : 200;
                const res = await fetch('/api/logs?lines=' + lines);
                const data = await res.json();
                if(!data.success) { alert("加载失败: " + data.message); return; }
                const tbody = document.getElementById('logBody');
                if (!tbody) return;
                tbody.innerHTML = '';
                const items = data.lines || [];
                if (items.length === 0) {
                    tbody.innerHTML = `<tr><td colspan="3" style="color:var(--text-sub); text-align:center;">暂无日志</td></tr>`;
                    return;
                }
                items.forEach(line => {
                    const p = parseLogLine(line);
                    let levelClass = 'log-level info';
                    if (p.level === 'ERROR') levelClass = 'log-level error';
                    else if (p.level === 'WARNING' || p.level === 'WARN') levelClass = 'log-level warn';
                    tbody.innerHTML += `<tr><td>${escapeHtml(p.time)}</td><td class="${levelClass}">${escapeHtml(p.level)}</td><td class="log-msg">${escapeHtml(p.msg)}</td></tr>`;
                });
            } catch(e) { console.error(e); }
        }

        function prevPage() { if (historyPage > 1) { historyPage--; loadHistory(); } }
        function nextPage() { const totalPages = Math.max(1, Math.ceil(historyTotal / historyPageSize)); if (historyPage < totalPages) { historyPage++; loadHistory(); } }
        
        async function loadUsers() { const res = await fetch('/api/users'); const data = await res.json(); const tbody = document.getElementById('userBody'); tbody.innerHTML = ""; data.data.forEach(u => { const roleName = u.role === 'admin' ? '<span style="color:var(--accent)">系统管理员</span>' : '<span>普通操作员</span>'; tbody.innerHTML += `<tr><td>${u.username}</td><td>${roleName}</td><td><button class="btn-edit" onclick="openUserModal('${u.id}', '${u.username}', '${u.role}')">修改</button><button class="btn-del" onclick="deleteUser('${u.id}')">删除</button></td></tr>`; }); }
        function openUserModal(id=null, name='', role='operator') { document.getElementById('modal_uid').value = id || ''; document.getElementById('modal_username').value = name; document.getElementById('modal_password').value = ''; document.getElementById('modal_role').value = role; document.getElementById('modal_username').disabled = !!id; document.getElementById('modalTitle').innerText = id ? "修改用户权限/密码" : "新增用户"; document.getElementById('userModal').style.display = 'flex'; }
        function closeModal() { document.getElementById('userModal').style.display = 'none'; }
        async function saveUser() { const uid = document.getElementById('modal_uid').value; const u = document.getElementById('modal_username').value; const p = document.getElementById('modal_password').value; const r = document.getElementById('modal_role').value; if(!u) return alert("账号不能为空"); const res = await fetch('/api/users', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ id: uid, username: u, password: p, role: r }) }); const d = await res.json(); if(d.success) { closeModal(); loadUsers(); alert("保存成功"); } else alert(d.message); }
        async function deleteUser(id) { if(!confirm("确定要删除该用户吗？此操作不可恢复！")) return; const res = await fetch('/api/users?id=' + id, { method: 'DELETE' }); const d = await res.json(); if(d.success) loadUsers(); else alert(d.message); }
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    upgrade_db_structure() 
    print("🚀 软著版系统启动 (ECharts稳健版+批量删除+1年选项): http://0.0.0.0:5000")
    serve(app, host='0.0.0.0', port=5000, threads=20)
