# -*- coding: utf-8 -*-
import os
import sys
import re
import time
import zipfile
import tempfile
import shutil
import logging
import atexit
import sqlite3
import threading
import subprocess
from datetime import datetime, timedelta

import psutil
import requests
import telebot
from telebot import types
from flask import Flask, request, jsonify

# --- Button Color Adapter (Telegram Bot API 7.10+) ---
_orig_init = types.InlineKeyboardButton.__init__
_orig_dict = types.InlineKeyboardButton.to_dict

def _patched_init(self, text, *args, style=None, **kwargs):
    _orig_init(self, text, *args, **kwargs)
    self.style = style

def _patched_dict(self):
    d = _orig_dict(self)
    if getattr(self, 'style', None):
        d['style'] = self.style
    return d

types.InlineKeyboardButton.__init__ = _patched_init
types.InlineKeyboardButton.to_dict = _patched_dict

def btn(text, callback_data=None, url=None, style=None):
    b = types.InlineKeyboardButton(text=text, callback_data=callback_data, url=url)
    if style in ['primary', 'success', 'danger']:
        b.style = style
    return b

# --- Configuration ---
TOKEN = '7978624354:AAGOkDuK_zmvo1CtFpqncZceak1BxTk7iGU'
OWNER_ID = 7569652619
ADMIN_ID = 7569652619
YOUR_USERNAME = '@nsg_cheats_onwer'
UPDATE_CHANNEL = '@nsg_cheats'
NAGAD_NUMBER = '+8801333584115'

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
DATA_DIR = os.path.join(BASE_DIR, 'inf')
DB_PATH = os.path.join(DATA_DIR, 'bot_data.db')

FREE_LIMIT = 3
VIP_LIMIT = 15
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("Host")
bot = telebot.TeleBot(TOKEN, parse_mode='Markdown')

# --- Globals & Database ---
START_TIME = time.time()
bot_scripts = {}
user_subscriptions = {}
user_files = {}
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
DB_LOCK = threading.Lock()

PYPI_MAP = {
    'telebot': 'pyTelegramBotAPI', 'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram', 'pyrogram': 'pyrogram', 'telethon': 'telethon',
    'bs4': 'beautifulsoup4', 'requests': 'requests', 'pillow': 'Pillow',
    'cv2': 'opencv-python', 'flask': 'Flask', 'psutil': 'psutil'
}

CORE_MODS = {
    'asyncio', 'json', 'datetime', 'os', 'sys', 're', 'time',
    'math', 'random', 'logging', 'threading', 'subprocess',
    'zipfile', 'tempfile', 'shutil', 'sqlite3', 'atexit'
}

# --- Database Layer ---
def init_db():
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('PRAGMA journal_mode=WAL;')
            c.execute('CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT NOT NULL)')
            c.execute('CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, PRIMARY KEY (user_id, file_name))')
            c.execute('CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)')
            c.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
            c.execute('''CREATE TABLE IF NOT EXISTS pending_vip_requests (
                            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
                            plan_name TEXT, days INTEGER, amount INTEGER, status TEXT DEFAULT "pending"
                        )''')
            c.execute('INSERT OR IGNORE INTO admins VALUES (?)', (OWNER_ID,))
            if ADMIN_ID != OWNER_ID:
                c.execute('INSERT OR IGNORE INTO admins VALUES (?)', (ADMIN_ID,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.critical(f"DB Init Error: {e}")

def load_data():
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('SELECT user_id, expiry FROM subscriptions')
            for uid, exp in c.fetchall():
                try: user_subscriptions[uid] = {'expiry': datetime.fromisoformat(exp)}
                except Exception: pass
            c.execute('SELECT user_id, file_name, file_type FROM user_files')
            for uid, fn, ft in c.fetchall():
                user_files.setdefault(uid, []).append((fn, ft))
            c.execute('SELECT user_id FROM active_users')
            active_users.update(uid for (uid,) in c.fetchall())
            c.execute('SELECT user_id FROM admins')
            admin_ids.update(uid for (uid,) in c.fetchall())
            conn.close()
        except Exception as e:
            logger.error(f"Data Load Error: {e}")

init_db()
load_data()

def save_user_file_db(uid, fn, ft='py'):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_files VALUES (?, ?, ?)', (uid, fn, ft))
            conn.commit()
            conn.close()
            user_files.setdefault(uid, [])
            user_files[uid] = [(f, t) for f, t in user_files[uid] if f != fn]
            user_files[uid].append((fn, ft))
        except Exception as e:
            logger.error(f"Save file DB error: {e}")

def remove_user_file_db(uid, fn):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (uid, fn))
            conn.commit()
            conn.close()
            if uid in user_files:
                user_files[uid] = [f for f in user_files[uid] if f[0] != fn]
                if not user_files[uid]: del user_files[uid]
        except Exception as e:
            logger.error(f"Remove file DB error: {e}")

def register_active_user(uid):
    active_users.add(uid)
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO active_users VALUES (?)', (uid,))
            conn.commit()
            conn.close()
        except Exception:
            pass

def save_subscription_db(uid, expiry):
    with DB_LOCK:
        try:
            conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO subscriptions VALUES (?, ?)', (uid, expiry.isoformat()))
            conn.commit()
            conn.close()
            user_subscriptions[uid] = {'expiry': expiry}
        except Exception as e:
            logger.error(f"Save sub error: {e}")

def get_user_folder(uid):
    f = os.path.join(UPLOAD_BOTS_DIR, str(uid))
    os.makedirs(f, exist_ok=True)
    return f

def get_user_quota(uid):
    if uid == OWNER_ID: return OWNER_LIMIT
    if uid in admin_ids: return ADMIN_LIMIT
    if uid in user_subscriptions and user_subscriptions[uid]['expiry'] > datetime.now():
        return VIP_LIMIT
    return FREE_LIMIT

def get_user_tier_name(uid):
    if uid == OWNER_ID: return "👑 Owner"
    if uid in admin_ids: return "🛡️ Admin"
    if uid in user_subscriptions:
        exp = user_subscriptions[uid].get('expiry')
        if exp and exp > datetime.now(): return "⭐ VIP"
    return "🆓 Free"

# --- Process Execution & Supervisor Engine ---
def is_bot_running(owner_id, fn):
    key = f"{owner_id}_{fn}"
    info = bot_scripts.get(key)
    if info and info.get('process'):
        try:
            p = psutil.Process(info['process'].pid)
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                return True
        except Exception:
            pass
        if 'log_file' in info and not info['log_file'].closed:
            try: info['log_file'].close()
            except Exception: pass
        bot_scripts.pop(key, None)
    return False

def terminate_process_tree(key):
    info = bot_scripts.get(key)
    if not info: return
    if 'log_file' in info and not info['log_file'].closed:
        try: info['log_file'].close()
        except Exception: pass
    proc = info.get('process')
    if proc and proc.pid:
        try:
            p = psutil.Process(proc.pid)
            for c in p.children(recursive=True):
                try: c.terminate()
                except Exception: pass
            p.terminate()
            p.wait(timeout=1)
        except Exception:
            try: p.kill()
            except Exception: pass
    bot_scripts.pop(key, None)

def resolve_pip(mod, reply_target=None):
    pkg = PYPI_MAP.get(mod.lower(), mod)
    if mod.lower() in CORE_MODS: return False
    try:
        if reply_target and hasattr(reply_target, 'chat'):
            bot.reply_to(reply_target, f"📦 Auto-installing: `{pkg}`...", parse_mode='Markdown')
        res = subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', pkg], capture_output=True)
        return res.returncode == 0
    except Exception:
        return False

def resolve_npm(mod, cwd, reply_target=None):
    try:
        if reply_target and hasattr(reply_target, 'chat'):
            bot.reply_to(reply_target, f"📦 Auto-installing npm: `{mod}`...", parse_mode='Markdown')
        res = subprocess.run(['npm', 'install', '--no-audit', '--no-fund', mod], cwd=cwd, capture_output=True)
        return res.returncode == 0
    except Exception:
        return False

def launch_py(path, owner_id, folder, fn, reply_target=None, attempt=1):
    if attempt > 2: return
    key = f"{owner_id}_{fn}"
    if attempt == 1:
        try:
            chk = subprocess.Popen([sys.executable, path], cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            _, err = chk.communicate(timeout=4)
            if chk.returncode != 0 and err:
                m = re.search(r"ModuleNotFoundError: No module named '(.+?)'", err)
                if m and resolve_pip(m.group(1).strip().strip("'\""), reply_target):
                    time.sleep(1)
                    launch_py(path, owner_id, folder, fn, reply_target, attempt=2)
                    return
        except subprocess.TimeoutExpired: chk.kill()
        except Exception: pass

    log_p = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")
    try:
        lf = open(log_p, 'w', encoding='utf-8', errors='ignore')
        p = subprocess.Popen([sys.executable, path], cwd=folder, stdout=lf, stderr=lf, stdin=subprocess.PIPE)
        bot_scripts[key] = {'process': p, 'log_file': lf, 'file_name': fn, 'owner_id': owner_id, 'start_time': datetime.now()}
        if reply_target and hasattr(reply_target, 'chat'):
            bot.reply_to(reply_target, f"✅ **Script Started:** `{fn}`\n🔹 **PID:** `{p.pid}` | **Type:** Python")
    except Exception as e:
        if reply_target and hasattr(reply_target, 'chat'):
            bot.reply_to(reply_target, f"❌ Execution Error: {e}")

def launch_js(path, owner_id, folder, fn, reply_target=None, attempt=1):
    if attempt > 2: return
    key = f"{owner_id}_{fn}"
    if attempt == 1:
        try:
            chk = subprocess.Popen(['node', path], cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            _, err = chk.communicate(timeout=4)
            if chk.returncode != 0 and err:
                m = re.search(r"Cannot find module '(.+?)'", err)
                if m and not m.group(1).startswith(('.', '/')) and resolve_npm(m.group(1).strip().strip("'\""), folder, reply_target):
                    time.sleep(1)
                    launch_js(path, owner_id, folder, fn, reply_target, attempt=2)
                    return
        except subprocess.TimeoutExpired: chk.kill()
        except Exception: pass

    log_p = os.path.join(folder, f"{os.path.splitext(fn)[0]}.log")
    try:
        lf = open(log_p, 'w', encoding='utf-8', errors='ignore')
        p = subprocess.Popen(['node', path], cwd=folder, stdout=lf, stderr=lf, stdin=subprocess.PIPE)
        bot_scripts[key] = {'process': p, 'log_file': lf, 'file_name': fn, 'owner_id': owner_id, 'start_time': datetime.now()}
        if reply_target and hasattr(reply_target, 'chat'):
            bot.reply_to(reply_target, f"✅ **Script Started:** `{fn}`\n🔹 **PID:** `{p.pid}` | **Type:** Node.js")
    except Exception as e:
        if reply_target and hasattr(reply_target, 'chat'):
            bot.reply_to(reply_target, f"❌ Execution Error: {e}")

def extract_and_deploy_zip(file_bytes, zip_name, uid, reply_target=None):
    user_f = get_user_folder(uid)
    temp_d = tempfile.mkdtemp(prefix=f"zip_{uid}_")
    try:
        zip_p = os.path.join(temp_d, zip_name)
        with open(zip_p, 'wb') as f: f.write(file_bytes)
        with zipfile.ZipFile(zip_p, 'r') as arc:
            for mem in arc.infolist():
                dst = os.path.abspath(os.path.join(temp_d, mem.filename))
                if not dst.startswith(os.path.abspath(temp_d)):
                    raise zipfile.BadZipFile("Unsafe path traversal detected.")
            arc.extractall(temp_d)

        items = os.listdir(temp_d)
        if 'requirements.txt' in items:
            subprocess.run([sys.executable, '-m', 'pip', 'install', '--quiet', '-r', os.path.join(temp_d, 'requirements.txt')], check=False)
        if 'package.json' in items:
            subprocess.run(['npm', 'install', '--no-audit', '--no-fund'], cwd=temp_d, check=False)

        py_f = [f for f in items if f.endswith('.py')]
        js_f = [f for f in items if f.endswith('.js')]
        target, ftype = None, None
        for p in ['main.py', 'bot.py', 'app.py', 'index.js', 'main.js']:
            if p in py_f: target, ftype = p, 'py'; break
            if p in js_f: target, ftype = p, 'js'; break
        if not target:
            if py_f: target, ftype = py_f[0], 'py'
            elif js_f: target, ftype = js_f[0], 'js'
        if not target:
            return False, "No executable script (.py / .js) found in archive root."

        for it in os.listdir(temp_d):
            s = os.path.join(temp_d, it)
            d = os.path.join(user_f, it)
            if os.path.isdir(d): shutil.rmtree(d)
            elif os.path.exists(d): os.remove(d)
            shutil.move(s, d)

        save_user_file_db(uid, target, ftype)
        entry = os.path.join(user_f, target)
        if ftype == 'py':
            threading.Thread(target=launch_py, args=(entry, uid, user_f, target, reply_target), daemon=True).start()
        else:
            threading.Thread(target=launch_js, args=(entry, uid, user_f, target, reply_target), daemon=True).start()
        return True, target
    except Exception as e:
        return False, str(e)
    finally:
        shutil.rmtree(temp_d, ignore_errors=True)

# ==============================================================================
# 🌐 FLASK REST API FOR TELEGRAM MINI APP (BIDIRECTIONAL SYNC)
# ==============================================================================
app = Flask("CloudHostDaemon")

@app.after_request
def enable_cors(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type,Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET,PUT,POST,DELETE,OPTIONS'
    return response

# Serve Mini App directly if index.html is in root
@app.route('/')
def serve_app():
    html_path = os.path.join(BASE_DIR, 'index.html')
    if os.path.exists(html_path):
        with open(html_path, 'r', encoding='utf-8') as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    return {"status": "ONLINE", "running_bots": len(bot_scripts)}, 200

# 1. Real User Telemetry & Files
@app.route('/api/user_info', methods=['GET', 'OPTIONS'])
def api_user_info():
    if request.method == 'OPTIONS': return {}, 200
    uid = int(request.args.get('user_id', 0))
    register_active_user(uid)

    quota = get_user_quota(uid)
    quota_str = str(quota) if quota != float('inf') else "INF"
    tier = get_user_tier_name(uid)

    flist = user_files.get(uid, [])
    payload_files = []
    for fn, ft in sorted(flist):
        run = is_bot_running(uid, fn)
        pid = None
        if run:
            info = bot_scripts.get(f"{uid}_{fn}")
            if info and info.get('process'): pid = info['process'].pid
        payload_files.append({"name": fn, "type": ft, "running": run, "pid": pid})

    uptime = str(timedelta(seconds=int(time.time() - START_TIME)))
    return jsonify({
        "success": True,
        "user_id": uid,
        "tier": tier,
        "quota": quota,
        "quota_str": quota_str,
        "is_admin": uid in admin_ids,
        "is_owner": uid == OWNER_ID,
        "platform_locked": bot_locked,
        "metrics": {
            "cpu": psutil.cpu_percent(interval=None),
            "ram": psutil.virtual_memory().percent,
            "active_instances": sum(1 for (k, v) in bot_scripts.items() if is_bot_running(v['owner_id'], v['file_name'])),
            "uptime": uptime
        },
        "files": payload_files
    })

# 2. File Upload / Hosting via Mini App
@app.route('/api/upload', methods=['POST', 'OPTIONS'])
def api_upload():
    if request.method == 'OPTIONS': return {}, 200
    uid = int(request.form.get('user_id', 0))
    if not uid: return jsonify({"success": False, "error": "Missing user_id"}), 400

    if bot_locked and uid not in admin_ids:
        return jsonify({"success": False, "error": "Platform is locked by administrator."}), 403

    if len(user_files.get(uid, [])) >= get_user_quota(uid):
        return jsonify({"success": False, "error": "Storage quota exceeded. Delete a file or acquire VIP."}), 400

    file = request.files.get('file')
    if not file: return jsonify({"success": False, "error": "No file uploaded"}), 400

    fn = file.filename or "script"
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        return jsonify({"success": False, "error": "Only .py, .js, and .zip are supported."}), 400

    file_bytes = file.read()
    user_f = get_user_folder(uid)

    # Forward alert to owner
    try:
        f_kb = round(len(file_bytes) / 1024, 2)
        bot.send_message(
            OWNER_ID,
            f"📥 **NEW UPLOAD (MINI APP)**\n━━━━━━━━━━━━\n👤 **From:** `{uid}`\n📄 **File:** `{fn}`\n📦 **Size:** `{f_kb} KB`"
        )
    except Exception: pass

    if ext == '.zip':
        ok, res = extract_and_deploy_zip(file_bytes, fn, uid)
        if not ok: return jsonify({"success": False, "error": res}), 500
        main_entry = res
    else:
        ft = 'js' if ext == '.js' else 'py'
        dest = os.path.join(user_f, fn)
        with open(dest, 'wb') as f: f.write(file_bytes)
        save_user_file_db(uid, fn, ft)
        if ft == 'py':
            threading.Thread(target=launch_py, args=(dest, uid, user_f, fn), daemon=True).start()
        else:
            threading.Thread(target=launch_js, args=(dest, uid, user_f, fn), daemon=True).start()
        main_entry = fn

    return jsonify({"success": True, "message": f"Successfully launched {main_entry}!", "file_name": main_entry})

# 3. Instance Process Control (Start/Stop/Restart/Delete)
@app.route('/api/action', methods=['POST', 'OPTIONS'])
def api_action():
    if request.method == 'OPTIONS': return {}, 200
    data = request.get_json(silent=True) or request.form
    uid = int(data.get('user_id', 0))
    fn = data.get('file_name', '')
    act = data.get('action', '')

    if not uid or not fn: return jsonify({"success": False, "error": "Invalid params"}), 400

    user_f = get_user_folder(uid)
    path = os.path.join(user_f, fn)
    ext = os.path.splitext(fn)[1].lower()

    if act == 'start':
        if not os.path.exists(path): return jsonify({"success": False, "error": "File missing on disk"}), 404
        if is_bot_running(uid, fn): return jsonify({"success": False, "error": "Already running"}), 400
        if ext == '.py': threading.Thread(target=launch_py, args=(path, uid, user_f, fn), daemon=True).start()
        else: threading.Thread(target=launch_js, args=(path, uid, user_f, fn), daemon=True).start()
        time.sleep(1)
        return jsonify({"success": True, "running": True})

    elif act == 'stop':
        terminate_process_tree(f"{uid}_{fn}")
        return jsonify({"success": True, "running": False})

    elif act == 'restart':
        terminate_process_tree(f"{uid}_{fn}")
        time.sleep(1)
        if ext == '.py': threading.Thread(target=launch_py, args=(path, uid, user_f, fn), daemon=True).start()
        else: threading.Thread(target=launch_js, args=(path, uid, user_f, fn), daemon=True).start()
        time.sleep(1)
        return jsonify({"success": True, "running": True})

    elif act == 'delete':
        terminate_process_tree(f"{uid}_{fn}")
        for del_item in [fn, f"{os.path.splitext(fn)[0]}.log"]:
            target_p = os.path.join(user_f, del_item)
            if os.path.exists(target_p):
                try: os.remove(target_p)
                except Exception: pass
        remove_user_file_db(uid, fn)
        return jsonify({"success": True, "deleted": True})

    return jsonify({"success": False, "error": "Unknown action"}), 400

# 4. Stream Logs API
@app.route('/api/logs', methods=['GET', 'OPTIONS'])
def api_logs():
    if request.method == 'OPTIONS': return {}, 200
    uid = int(request.args.get('user_id', 0))
    fn = request.args.get('file_name', '')
    lp = os.path.join(get_user_folder(uid), f"{os.path.splitext(fn)[0]}.log")
    if not os.path.exists(lp):
        return jsonify({"success": True, "logs": "[LOGS EMPTY: No execution stream recorded yet]"})
    try:
        with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            tail = "".join(lines[-40:]) if lines else "[LOG BUFFER EMPTY]"
            return jsonify({"success": True, "logs": tail})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# 5. VIP Nagad Verification Dispatcher
@app.route('/api/vip_proof', methods=['POST', 'OPTIONS'])
def api_vip_proof():
    if request.method == 'OPTIONS': return {}, 200
    uid = int(request.form.get('user_id', 0))
    plan = request.form.get('plan', '1 Month')
    days = int(request.form.get('days', 30))
    amt = int(request.form.get('amount', 450))
    tx_id = request.form.get('tx_id', 'None Provided')
    shot = request.files.get('screenshot')

    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT INTO pending_vip_requests (user_id, plan_name, days, amount) VALUES (?, ?, ?, ?)', (uid, plan, days, amt))
        conn.commit()
        req_id = c.lastrowid
        conn.close()

    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn("✅ Approve", callback_data=f"vip_ok_{req_id}", style="success"), btn("❌ Reject", callback_data=f"vip_no_{req_id}", style="danger"))
    cap = f"🔔 **VIP PAYMENT SUBMITTED (MINI APP)**\n━━━━━━━━━━━━\n👤 **Operator:** `{uid}`\n📦 **Plan:** `{plan}` ({days} Days)\n💵 **Amount:** `{amt} BDT`\n📝 **TxID:** `{tx_id}`"

    for aid in admin_ids:
        try:
            if shot:
                shot_bytes = shot.read()
                bot.send_photo(aid, shot_bytes, caption=cap, reply_markup=m)
            else:
                bot.send_message(aid, cap, reply_markup=m)
        except Exception: pass

    return jsonify({"success": True, "message": "Verification transmitted successfully."})

# 6. Admin Overrides
@app.route('/api/admin_action', methods=['POST', 'OPTIONS'])
def api_admin_action():
    if request.method == 'OPTIONS': return {}, 200
    global bot_locked
    data = request.get_json(silent=True) or request.form
    aid = int(data.get('admin_id', 0))
    act = data.get('action', '')

    if aid not in admin_ids:
        return jsonify({"success": False, "error": "Unauthorized"}), 403

    if act == 'toggle_lock':
        bot_locked = not bot_locked
        return jsonify({"success": True, "locked": bot_locked})
    elif act == 'run_all':
        started = 0
        for u, flist in dict(user_files).items():
            user_f = get_user_folder(u)
            for fn, ft in flist:
                if not is_bot_running(u, fn):
                    fp = os.path.join(user_f, fn)
                    if os.path.exists(fp):
                        if ft == 'py': threading.Thread(target=launch_py, args=(fp, u, user_f, fn), daemon=True).start()
                        else: threading.Thread(target=launch_js, args=(fp, u, user_f, fn), daemon=True).start()
                        started += 1
                        time.sleep(0.3)
        return jsonify({"success": True, "started": started})

    return jsonify({"success": False, "error": "Invalid action"}), 400

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

def keep_alive():
    t = threading.Thread(target=run_flask, daemon=True)
    t.start()

# ==============================================================================
# 🎮 TELEGRAM BOT HANDLERS & INLINE MENUS
# ==============================================================================
def render_main_dashboard_markup(uid):
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn("📢 Updates", url=f"https://t.me/{UPDATE_CHANNEL.replace('@', '')}", style="primary"), btn("💬 Support", url=f"https://t.me/{YOUR_USERNAME.replace('@', '')}", style="primary"))
    m.row(btn("📤 Upload File", callback_data="ui_upload", style="success"), btn("📂 Check Files", callback_data="ui_check_files", style="primary"))
    m.row(btn("⭐ Buy VIP", callback_data="buy_vip_menu", style="success"), btn("⚡ Bot Speed", callback_data="ui_speed", style="primary"))
    m.row(btn("📊 Statistics", callback_data="ui_stats", style="primary"))

    if uid in admin_ids:
        m.row(btn("🟢 Run All Code", callback_data="ui_run_all", style="success"), btn("📢 Broadcast", callback_data="ui_broadcast", style="primary"))
        m.row(btn("💳 Subscriptions", callback_data="ui_subs", style="primary"), btn("👑 Admin Panel", callback_data="ui_admin_panel", style="primary"))
        l_title = "🔓 Unlock Bot" if bot_locked else "🔒 Lock Bot"
        l_act = "ui_unlock" if bot_locked else "ui_lock"
        l_style = "success" if bot_locked else "danger"
        m.row(btn(l_title, callback_data=l_act, style=l_style))
    return m

def render_vip_plans_markup():
    m = types.InlineKeyboardMarkup(row_width=1)
    m.row(btn("💎 1 Month — 450 BDT", callback_data="vip_p_30_450", style="primary"))
    m.row(btn("💎 2 Months — 800 BDT", callback_data="vip_p_60_800", style="primary"))
    m.row(btn("👑 1 Year — 5000 BDT", callback_data="vip_p_365_5000", style="success"))
    m.row(btn("🔙 Back to Menu", callback_data="ui_main", style="danger"))
    return m

def render_instance_control_markup(owner_id, fn, is_running):
    m = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        m.row(btn("🛑 Stop", callback_data=f"p_stop_{owner_id}_{fn}", style="danger"), btn("🔄 Restart", callback_data=f"p_res_{owner_id}_{fn}", style="primary"))
        m.row(btn("📜 View Logs", callback_data=f"p_log_{owner_id}_{fn}", style="primary"), btn("🗑️ Delete", callback_data=f"p_del_{owner_id}_{fn}", style="danger"))
    else:
        m.row(btn("🟢 Start", callback_data=f"p_start_{owner_id}_{fn}", style="success"), btn("🔄 Restart", callback_data=f"p_res_{owner_id}_{fn}", style="primary"))
        m.row(btn("📜 View Logs", callback_data=f"p_log_{owner_id}_{fn}", style="primary"), btn("🗑️ Delete", callback_data=f"p_del_{owner_id}_{fn}", style="danger"))
    m.row(btn("🔙 Back to Files", callback_data="ui_check_files", style="primary"))
    return m

def build_welcome_text(uid, name, username):
    tier = get_user_tier_name(uid)
    quota = get_user_quota(uid)
    q_str = str(quota) if quota != float('inf') else "Unlimited"
    flist = user_files.get(uid, [])
    c_files = len(flist)
    r_files = sum(1 for (fn, _) in flist if is_bot_running(uid, fn))
    gate = "🔒 Locked" if bot_locked else "🟢 Online"
    exp_info = ""
    if uid in user_subscriptions:
        exp = user_subscriptions[uid].get('expiry')
        if exp and exp > datetime.now():
            d_left = (exp - datetime.now()).days
            exp_info = f"\n⏳ **VIP Validity:** `{d_left} days left`"
    return f"⚡ **CLOUD HOSTING NODE**\n━━━━━━━━━━━━━━━━━━━━━\n👤 **User:** `{name}` (@{username or 'N/A'})\n🆔 **ID:** `{uid}` • **Tier:** {tier}{exp_info}\n📁 **Files:** `{c_files}/{q_str}` • **Running:** `{r_files}`\n🚦 **Platform:** {gate}\n━━━━━━━━━━━━━━━━━━━━━\nUpload `.py`, `.js`, or `.zip` files to host."

@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    uid = message.from_user.id
    register_active_user(uid)
    text = build_welcome_text(uid, message.from_user.first_name, message.from_user.username)
    bot.send_message(message.chat.id, text, reply_markup=render_main_dashboard_markup(uid))

@bot.message_handler(commands=['status', 'stats'])
def handle_stats(message):
    send_stats(message.chat.id, message.from_user.id)

@bot.message_handler(commands=['ping'])
def handle_ping(message):
    t0 = time.time()
    msg = bot.reply_to(message, "Testing latency...")
    ms = round((time.time() - t0) * 1000, 2)
    bot.edit_message_text(f"🏓 **Pong!** Latency: `{ms} ms`", message.chat.id, msg.message_id)

@bot.message_handler(content_types=['document'])
def handle_doc(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    doc = message.document

    if bot_locked and uid not in admin_ids:
        bot.reply_to(message, "⚠️ Bot is locked by administrator.")
        return

    quota = get_user_quota(uid)
    if len(user_files.get(uid, [])) >= quota:
        bot.reply_to(message, "⚠️ Limit reached. Delete files first or buy VIP.")
        return

    fn = doc.file_name or "file"
    ext = os.path.splitext(fn)[1].lower()
    if ext not in ['.py', '.js', '.zip']:
        bot.reply_to(message, "⚠️ Only `.py`, `.js`, and `.zip` supported.")
        return

    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ Exceeds 20 MB size limit.")
        return

    try:
        bot.forward_message(OWNER_ID, chat_id, message.message_id)
        u_tag = f"@{message.from_user.username}" if message.from_user.username else "No Tag"
        f_kb = round(doc.file_size / 1024, 2)
        bot.send_message(OWNER_ID, f"📥 **NEW FILE FORWARDED**\n━━━━━━━━━━━━\n👤 **From:** {message.from_user.first_name} (`{uid}`)\n🏷️ **Tag:** {u_tag}\n📄 **File:** `{fn}`\n📦 **Size:** `{f_kb} KB`")
    except Exception as e:
        logger.error(f"Forward error: {e}")

    status = bot.reply_to(message, f"⏳ Downloading `{fn}`...")
    try:
        f_info = bot.get_file(doc.file_id)
        downloaded = bot.download_file(f_info.file_path)
        bot.edit_message_text(f"✅ Downloaded `{fn}`. Initializing...", chat_id, status.message_id)
        user_f = get_user_folder(uid)

        if ext == '.zip':
            ok, res = extract_and_deploy_zip(downloaded, fn, uid, message)
            if not ok: bot.reply_to(message, f"❌ Archive deployment failed: {res}")
        else:
            ft = 'js' if ext == '.js' else 'py'
            dest = os.path.join(user_f, fn)
            with open(dest, 'wb') as f: f.write(downloaded)
            save_user_file_db(uid, fn, ft)
            if ft == 'py':
                threading.Thread(target=launch_py, args=(dest, uid, user_f, fn, message), daemon=True).start()
            else:
                threading.Thread(target=launch_js, args=(dest, uid, user_f, fn, message), daemon=True).start()
    except Exception as e:
        bot.reply_to(message, f"❌ Upload error: {e}")

# Callbacks for Inline Buttons
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.from_user.id
    d = call.data
    chat_id = call.message.chat.id
    mid = call.message.message_id

    if bot_locked and uid not in admin_ids and d not in ['ui_main', 'ui_speed', 'ui_stats']:
        bot.answer_callback_query(call.id, "⚠️ Platform locked by admin.", show_alert=True)
        return

    try:
        if d == 'ui_main':
            text = build_welcome_text(uid, call.from_user.first_name, call.from_user.username)
            try: bot.edit_message_text(text, chat_id, mid, reply_markup=render_main_dashboard_markup(uid))
            except Exception: bot.send_message(chat_id, text, reply_markup=render_main_dashboard_markup(uid))
            bot.answer_callback_query(call.id)

        elif d == 'buy_vip_menu':
            bot.answer_callback_query(call.id)
            v_text = "⭐ **ENTERPRISE VIP SUBSCRIPTION**\n━━━━━━━━━━━━━━━━━━━━━\n• একসাথে **১৫টি** স্ক্রিপ্ট রান করার সুবিধা\n• হাই-প্রায়োরিটি 24/7 হোস্টিং রানটাইম\n\n📌 **মূল্য তালিকা:**\n• **১ মাস:** ৪৫০ টাকা\n• **২ মাস:** ৮০০ টাকা\n• **১ বছর:** ৫০০০ টাকা\n━━━━━━━━━━━━━━━━━━━━━\nপ্ল্যান সিলেক্ট করুন:"
            bot.edit_message_text(v_text, chat_id, mid, reply_markup=render_vip_plans_markup())

        elif d == 'vip_p_30_450':
            bot.answer_callback_query(call.id)
            ask_vip_payment(call, "1 Month", 30, 450)

        elif d == 'vip_p_60_800':
            bot.answer_callback_query(call.id)
            ask_vip_payment(call, "2 Months", 60, 800)

        elif d == 'vip_p_365_5000':
            bot.answer_callback_query(call.id)
            ask_vip_payment(call, "1 Year", 365, 5000)

        elif d.startswith('vip_ok_'):
            require_admin(call, handle_vip_ok)

        elif d.startswith('vip_no_'):
            require_admin(call, handle_vip_no)

        elif d == 'ui_upload':
            if len(user_files.get(uid, [])) >= get_user_quota(uid):
                bot.answer_callback_query(call.id, "Limit reached!", show_alert=True)
                return
            bot.answer_callback_query(call.id)
            m = types.InlineKeyboardMarkup(row_width=1)
            m.row(btn("🔙 Back", callback_data="ui_main", style="danger"))
            bot.send_message(chat_id, "📤 Send your `.py`, `.js`, or `.zip` file.", reply_markup=m)

        elif d == 'ui_check_files':
            render_files_view(chat_id, mid, uid)
            bot.answer_callback_query(call.id)

        elif d == 'ui_speed':
            send_speed(chat_id, mid, uid)
            bot.answer_callback_query(call.id)

        elif d == 'ui_stats':
            send_stats(chat_id, uid, mid)
            bot.answer_callback_query(call.id)

        elif d.startswith('sel_f_'):
            _, _, o_str, fn = d.split('_', 3)
            render_single_file(chat_id, mid, int(o_str), fn, uid)
            bot.answer_callback_query(call.id)

        elif d.startswith('p_start_'):
            _, _, o_str, fn = d.split('_', 3)
            proc_start(call, int(o_str), fn)

        elif d.startswith('p_stop_'):
            _, _, o_str, fn = d.split('_', 3)
            proc_stop(call, int(o_str), fn)

        elif d.startswith('p_res_'):
            _, _, o_str, fn = d.split('_', 3)
            proc_restart(call, int(o_str), fn)

        elif d.startswith('p_del_'):
            _, _, o_str, fn = d.split('_', 3)
            proc_delete(call, int(o_str), fn)

        elif d.startswith('p_log_'):
            _, _, o_str, fn = d.split('_', 3)
            proc_logs(call, int(o_str), fn)

        elif d == 'ui_lock':
            require_admin(call, toggle_lock, lock=True)

        elif d == 'ui_unlock':
            require_admin(call, toggle_lock, lock=False)

        elif d == 'ui_run_all':
            require_admin(call, run_all_code)

    except Exception as e:
        logger.error(f"Callback error '{d}': {e}")
        bot.answer_callback_query(call.id, "Action failed.", show_alert=True)

def ask_vip_payment(call, plan_name, days, amount):
    chat_id = call.message.chat.id
    text = f"💳 **NAGAD PAYMENT INSTRUCTIONS**\n━━━━━━━━━━━━━━━━━━━━━\n📦 **Plan:** `{plan_name}`\n💵 **Amount:** `{amount}` BDT\n📱 **Nagad:** `{NAGAD_NUMBER}` (Personal)\n⚠️ **পদ্ধতি:** শুধুমাত্র **সেন্ড মানি (Send Money)** করুন।\n━━━━━━━━━━━━━━━━━━━━━\n১. নম্বরে ঠিক **{amount}** টাকা সেন্ড মানি করুন।\n২. সফল হলে পেমেন্টের **স্ক্রিনশট** বা TxID এখানে পাঠান।\n\n📸 **পেমেন্টের স্ক্রিনশটটি পাঠান:** (বাতিল করতে `/cancel`)"
    m = types.InlineKeyboardMarkup(row_width=1)
    m.row(btn("🔙 Cancel", callback_data="buy_vip_menu", style="danger"))
    sent = bot.send_message(chat_id, text, reply_markup=m)
    bot.register_next_step_handler(sent, process_vip_proof, plan_name, days, amount)

def process_vip_proof(message, plan_name, days, amount):
    uid = message.from_user.id
    if message.text and message.text.strip().lower() == '/cancel':
        bot.reply_to(message, "❌ VIP request cancelled.")
        return

    has_photo = message.photo is not None
    has_doc = message.document is not None
    info = message.text or message.caption or "Proof Attachment"

    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT INTO pending_vip_requests (user_id, plan_name, days, amount) VALUES (?, ?, ?, ?)', (uid, plan_name, days, amount))
        conn.commit()
        req_id = c.lastrowid
        conn.close()

    bot.reply_to(message, "✅ **পেমেন্ট রিকোয়েস্ট জমা হয়েছে!** অ্যাডমিন যাচাই করে দ্রুত অনুমোদন করবে।")
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn("✅ Approve", callback_data=f"vip_ok_{req_id}", style="success"), btn("❌ Reject", callback_data=f"vip_no_{req_id}", style="danger"))
    cap = f"🔔 **VIP PAYMENT REQUEST**\n━━━━━━━━━━━━\n👤 **User:** `{message.from_user.first_name}` (`{uid}`)\n📦 **Plan:** `{plan_name}` ({days} Days)\n💵 **Amount:** `{amount} BDT`\n📝 **TxID/Info:** `{info}`"
    for aid in admin_ids:
        try:
            if has_photo:
                bot.send_photo(aid, message.photo[-1].file_id, caption=cap, reply_markup=m)
            elif has_doc:
                bot.send_document(aid, message.document.file_id, caption=cap, reply_markup=m)
            else:
                bot.send_message(aid, cap, reply_markup=m)
        except Exception: pass

def handle_vip_ok(call):
    req_id = int(call.data.split('_')[2])
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, plan_name, days, amount, status FROM pending_vip_requests WHERE id = ?', (req_id,))
        row = c.fetchone()
        if not row or row[4] != 'pending':
            bot.answer_callback_query(call.id, "Already processed!", show_alert=True)
            conn.close()
            return
        u_id, p_name, days, amt, _ = row
        c.execute('UPDATE pending_vip_requests SET status = "approved" WHERE id = ?', (req_id,))
        conn.commit()
        conn.close()

    base = datetime.now()
    if u_id in user_subscriptions and user_subscriptions[u_id].get('expiry', base) > base:
        base = user_subscriptions[u_id]['expiry']
    new_exp = base + timedelta(days=days)
    save_subscription_db(u_id, new_exp)

    try:
        bot.send_message(u_id, f"🎉 **VIP ACTIVATED!**\n━━━━━━━━━━━━\nআপনার **{p_name}** সাবস্ক্রিপশন অনুমোদিত হয়েছে!\n📅 মেয়াদ: `{new_exp.strftime('%Y-%m-%d')}`\n📁 সীমা: ১৫টি স্ক্রিপ্ট হোস্টিং।")
    except Exception: pass

    bot.answer_callback_query(call.id, "VIP Approved!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"✅ **APPROVED:** `{p_name}` for User `{u_id}`.")
    except Exception: pass

def handle_vip_no(call):
    req_id = int(call.data.split('_')[2])
    with DB_LOCK:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, plan_name, status FROM pending_vip_requests WHERE id = ?', (req_id,))
        row = c.fetchone()
        if not row or row[2] != 'pending':
            bot.answer_callback_query(call.id, "Already processed!", show_alert=True)
            conn.close()
            return
        u_id, p_name, _ = row
        c.execute('UPDATE pending_vip_requests SET status = "rejected" WHERE id = ?', (req_id,))
        conn.commit()
        conn.close()

    try:
        bot.send_message(u_id, f"❌ **VIP রিকোয়েস্ট বাতিল করা হয়েছে।** সমস্যা হলে যোগাযোগ করুন: {YOUR_USERNAME}")
    except Exception: pass

    bot.answer_callback_query(call.id, "Rejected.")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        bot.send_message(call.message.chat.id, f"❌ **REJECTED:** User `{u_id}` request.")
    except Exception: pass

def require_admin(call, func, **kwargs):
    if call.from_user.id not in admin_ids:
        bot.answer_callback_query(call.id, "⚠️ Admin only.", show_alert=True)
        return
    func(call, **kwargs)

def send_speed(chat_id, mid, uid):
    t0 = time.time()
    bot.send_chat_action(chat_id, 'typing')
    ms = round((time.time() - t0) * 1000, 2)
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory()
    up = str(timedelta(seconds=int(time.time() - START_TIME)))
    text = f"⚡ **SYSTEM BENCHMARK**\n━━━━━━━━━━━━━━━━━━━━━\n⏱️ **API Response:** `{ms} ms`\n🖥️ **CPU Load:** `{cpu}%`\n💾 **RAM Used:** `{ram.percent}%`\n⌛ **Uptime:** `{up}`\n⚙️ **Active Bots:** `{len(bot_scripts)}`\n━━━━━━━━━━━━━━━━━━━━━"
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn("⚡ Re-Test", callback_data="ui_speed", style="primary"), btn("🔙 Main Menu", callback_data="ui_main", style="primary"))
    try: bot.edit_message_text(text, chat_id, mid, reply_markup=m)
    except Exception: bot.send_message(chat_id, text, reply_markup=m)

def send_stats(chat_id, uid, mid=None):
    t_users = len(active_users)
    t_files = sum(len(f) for f in user_files.values())
    act_bots = sum(1 for (k, v) in bot_scripts.items() if is_bot_running(v['owner_id'], v['file_name']))
    u_run = sum(1 for (fn, _) in user_files.get(uid, []) if is_bot_running(uid, fn))
    st = "🔴 Locked" if bot_locked else "🟢 Unlocked"
    text = f"📊 **STATISTICS**\n━━━━━━━━━━━━━━━━━━━━━\n👥 **Users:** `{t_users}` | 📁 **Files:** `{t_files}`\n🟢 **Total Running:** `{act_bots}`\n🤖 **Your Running:** `{u_run}`\n🚦 **Platform Gate:** `{st}`\n━━━━━━━━━━━━━━━━━━━━━"
    m = types.InlineKeyboardMarkup(row_width=2)
    m.row(btn("🔄 Refresh", callback_data="ui_stats", style="primary"), btn("🔙 Main Menu", callback_data="ui_main", style="primary"))
    if mid:
        try:
            bot.edit_message_text(text, chat_id, mid, reply_markup=m)
            return
        except Exception: pass
    bot.send_message(chat_id, text, reply_markup=m)

def render_files_view(chat_id, mid, uid):
    flist = user_files.get(uid, [])
    m = types.InlineKeyboardMarkup(row_width=2)
    if not flist:
        m.row(btn("📤 Upload File", callback_data="ui_upload", style="success"))
        m.row(btn("🔙 Main Menu", callback_data="ui_main", style="primary"))
        bot.edit_message_text("📂 **Your Files:**\n\n(No files uploaded yet)", chat_id, mid, reply_markup=m)
        return

    for fn, ft in sorted(flist):
        run = is_bot_running(uid, fn)
        tag = "🟢" if run else "🔴"
        col = "success" if run else "danger"
        m.add(btn(f"{tag} {fn} ({ft})", callback_data=f"sel_f_{uid}_{fn}", style=col))

    m.row(btn("📤 Upload File", callback_data="ui_upload", style="success"), btn("🔙 Main Menu", callback_data="ui_main", style="primary"))
    bot.edit_message_text("📂 **Your Files:**\nClick a file to control its instance:", chat_id, mid, reply_markup=m)

def render_single_file(chat_id, mid, owner_id, fn, req_id):
    if req_id != owner_id and req_id not in admin_ids: return
    flist = user_files.get(owner_id, [])
    item = next((x for x in flist if x[0] == fn), None)
    if not item:
        bot.edit_message_text("❌ File not found.", chat_id, mid, reply_markup=render_main_dashboard_markup(req_id))
        return
    run = is_bot_running(owner_id, fn)
    st = "🟢 Running" if run else "🔴 Stopped"
    info = bot_scripts.get(f"{owner_id}_{fn}")
    pid = str(info['process'].pid) if run and info else "N/A"
    text = f"⚙️ **CONTROLS:** `{fn}`\n━━━━━━━━━━━━━━━━━━━━━\n🔹 **Status:** {st}\n🔹 **Type:** {item[1].upper()}\n🔹 **PID:** `{pid}`\n━━━━━━━━━━━━━━━━━━━━━"
    bot.edit_message_text(text, chat_id, mid, reply_markup=render_instance_control_markup(owner_id, fn, run))

def proc_start(call, owner_id, fn):
    uid = call.from_user.id
    if uid != owner_id and uid not in admin_ids: return
    if is_bot_running(owner_id, fn):
        bot.answer_callback_query(call.id, "Already running!", show_alert=True)
        return
    u_folder = get_user_folder(owner_id)
    p = os.path.join(u_folder, fn)
    if not os.path.exists(p):
        bot.answer_callback_query(call.id, "File missing from disk.", show_alert=True)
        remove_user_file_db(owner_id, fn)
        render_files_view(call.message.chat.id, call.message.message_id, uid)
        return
    bot.answer_callback_query(call.id, "Starting...")
    ext = os.path.splitext(fn)[1].lower()
    if ext == '.py':
        threading.Thread(target=launch_py, args=(p, owner_id, u_folder, fn, call.message), daemon=True).start()
    else:
        threading.Thread(target=launch_js, args=(p, owner_id, u_folder, fn, call.message), daemon=True).start()
    time.sleep(1.2)
    render_single_file(call.message.chat.id, call.message.message_id, owner_id, fn, uid)

def proc_stop(call, owner_id, fn):
    if call.from_user.id != owner_id and call.from_user.id not in admin_ids: return
    terminate_process_tree(f"{owner_id}_{fn}")
    bot.answer_callback_query(call.id, "Stopped.")
    render_single_file(call.message.chat.id, call.message.message_id, owner_id, fn, call.from_user.id)

def proc_restart(call, owner_id, fn):
    uid = call.from_user.id
    if uid != owner_id and uid not in admin_ids: return
    bot.answer_callback_query(call.id, "Restarting...")
    terminate_process_tree(f"{owner_id}_{fn}")
    time.sleep(1.0)
    u_folder = get_user_folder(owner_id)
    p = os.path.join(u_folder, fn)
    ext = os.path.splitext(fn)[1].lower()
    if ext == '.py':
        threading.Thread(target=launch_py, args=(p, owner_id, u_folder, fn, call.message), daemon=True).start()
    else:
        threading.Thread(target=launch_js, args=(p, owner_id, u_folder, fn, call.message), daemon=True).start()
    time.sleep(1.2)
    render_single_file(call.message.chat.id, call.message.message_id, owner_id, fn, uid)

def proc_delete(call, owner_id, fn):
    uid = call.from_user.id
    if uid != owner_id and uid not in admin_ids: return
    terminate_process_tree(f"{owner_id}_{fn}")
    u_folder = get_user_folder(owner_id)
    for ext_del in [fn, f"{os.path.splitext(fn)[0]}.log"]:
        fp = os.path.join(u_folder, ext_del)
        if os.path.exists(fp):
            try: os.remove(fp)
            except Exception: pass
    remove_user_file_db(owner_id, fn)
    bot.answer_callback_query(call.id, "Deleted.")
    render_files_view(call.message.chat.id, call.message.message_id, uid)

def proc_logs(call, owner_id, fn):
    uid = call.from_user.id
    if uid != owner_id and uid not in admin_ids: return
    lp = os.path.join(get_user_folder(owner_id), f"{os.path.splitext(fn)[0]}.log")
    if not os.path.exists(lp):
        bot.answer_callback_query(call.id, "No logs yet.", show_alert=True)
        return
    bot.answer_callback_query(call.id, "Reading logs...")
    try:
        with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            tail = "".join(lines[-30:]) if lines else "(Empty log)"
            if len(tail) > 3500: tail = tail[-3500:]
        t = f"📜 **Logs for `{fn}`:**\n```text\n{tail}\n```"
        m = types.InlineKeyboardMarkup(row_width=2)
        m.row(btn("🔄 Refresh", callback_data=f"p_log_{owner_id}_{fn}", style="primary"), btn("🔙 Back", callback_data=f"sel_f_{owner_id}_{fn}", style="primary"))
        bot.send_message(call.message.chat.id, t, reply_markup=m)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ Failed to read log: {e}")

def toggle_lock(call, lock):
    global bot_locked
    bot_locked = lock
    bot.answer_callback_query(call.id, f"Platform {'locked' if lock else 'unlocked'}.")
    bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=render_main_dashboard_markup(call.from_user.id))

def run_all_code(call):
    bot.answer_callback_query(call.id, "Starting all...")
    wait_m = bot.send_message(call.message.chat.id, "⏳ Launching all stopped scripts...")
    started = 0
    for u, flist in dict(user_files).items():
        u_folder = get_user_folder(u)
        for fn, ft in flist:
            if not is_bot_running(u, fn):
                fp = os.path.join(u_folder, fn)
                if os.path.exists(fp):
                    if ft == 'py': threading.Thread(target=launch_py, args=(fp, u, u_folder, fn, wait_m), daemon=True).start()
                    else: threading.Thread(target=launch_js, args=(fp, u, u_folder, fn, wait_m), daemon=True).start()
                    started += 1
                    time.sleep(0.4)
    bot.send_message(call.message.chat.id, f"✅ Started `{started}` inactive scripts.")

def cleanup():
    for k in list(bot_scripts.keys()):
        try: terminate_process_tree(k)
        except Exception: pass

atexit.register(cleanup)

if __name__ == '__main__':
    print("=" * 40)
    print(" ⚡ HOSTING ENGINE ONLINE & API READY")
    print(f" 👑 Owner ID : {OWNER_ID}")
    print("=" * 40)

    keep_alive()

    while True:
        try:
            bot.infinity_polling(logger_level=logging.INFO, timeout=60, long_polling_timeout=30)
        except requests.exceptions.ReadTimeout: time.sleep(3)
        except requests.exceptions.ConnectionError: time.sleep(10)
        except Exception as e:
            logger.critical(f"Polling loop error: {e}")
            time.sleep(15)
