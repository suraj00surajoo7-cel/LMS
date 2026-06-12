#!/usr/bin/env python3
"""
BCU LMS — Full Application Backend
====================================
Serves the BCU LMS frontend pages and provides all API endpoints.

Features:
  ● JWT-based authentication (login / register / refresh / logout)
  ● Google OAuth 2.0 (stub — swap in real credentials)
  ● WebSocket presence tracking (online users, last-seen)
  ● VPL code execution proxy → local vpl_backend.py  OR  emkc.org Piston
  ● SQLite user store (no external DB required)
  ● CORS-enabled for all origins (dev mode)
  ● Static file serving for all HTML/CSS/JS pages

Run:
    python3 bcu_lms_backend.py

Then open:
    http://localhost:8000/login      ← Sign-in page
    http://localhost:8000/dashboard  ← Learner Dashboard
    http://localhost:8000/vpl        ← Virtual Programming Lab
"""

import os
import re
import json
import uuid
import time
import sqlite3
import hashlib
import secrets
import threading
import requests
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import jwt
import bcrypt
from flask import (
    Flask, request, jsonify, redirect,
    url_for, g, send_from_directory, abort
)
from flask_cors import CORS
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect

# ──────────────────────────────────────────────
#  Configuration
# ──────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent
STATIC_DIR  = BASE_DIR          # HTML files live alongside this script

SECRET_KEY       = os.environ.get("SECRET_KEY", secrets.token_hex(32))
JWT_ACCESS_EXP   = 60 * 60       # 1 hour
JWT_REFRESH_EXP  = 7 * 24 * 3600 # 7 days
ALGORITHM        = "HS256"

# Google OAuth  (replace with real credentials from console.cloud.google.com)
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID",     "YOUR_GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "YOUR_GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI  = os.environ.get("GOOGLE_REDIRECT_URI",  "http://localhost:8000/auth/google/callback")

# VPL backend — try local first, fall back to public Piston API
VPL_LOCAL_URL  = os.environ.get("VPL_BACKEND_URL", "http://localhost:5000")
VPL_PISTON_URL = "https://emkc.org"

DB_PATH = BASE_DIR / "bcu_lms.db"

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="")
app.config["SECRET_KEY"] = SECRET_KEY

CORS(app, supports_credentials=True)

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ──────────────────────────────────────────────
#  Database
# ──────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(str(DB_PATH))
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db

@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    """Create tables if they don't exist."""
    with sqlite3.connect(str(DB_PATH)) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id          TEXT PRIMARY KEY,
            username    TEXT UNIQUE NOT NULL,
            email       TEXT UNIQUE,
            password_hash TEXT,               -- NULL for OAuth-only accounts
            full_name   TEXT,
            mobile      TEXT,
            uucms       TEXT UNIQUE,
            year        INTEGER,
            semester    INTEGER,
            role        TEXT DEFAULT 'student',
            avatar_url  TEXT,
            google_id   TEXT UNIQUE,
            created_at  REAL NOT NULL,
            last_login  REAL
        );

        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token       TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at  REAL NOT NULL,
            created_at  REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            sid         TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            ip          TEXT,
            ua          TEXT,
            created_at  REAL NOT NULL,
            last_active REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS presence (
            user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            socket_id   TEXT,
            online      INTEGER DEFAULT 0,
            last_seen   REAL
        );

        CREATE TABLE IF NOT EXISTS vpl_runs (
            id          TEXT PRIMARY KEY,
            user_id     TEXT REFERENCES users(id),
            language    TEXT,
            code_hash   TEXT,
            exit_code   INTEGER,
            ran_at      REAL NOT NULL
        );
        """)

        # Seed a demo student account  (password: demo1234)
        existing = db.execute("SELECT id FROM users WHERE username='demo'").fetchone()
        if not existing:
            pw_hash = bcrypt.hashpw(b"demo1234", bcrypt.gensalt()).decode()
            db.execute("""
                INSERT INTO users (id,username,email,password_hash,full_name,uucms,year,semester,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (str(uuid.uuid4()), "demo", "demo@bcu.edu.in", pw_hash,
                  "Demo Student", "UUCMS2024001", 2, 3, time.time()))
            db.commit()
            print("[BCU] Seeded demo account  →  username: demo  /  password: demo1234")


# ──────────────────────────────────────────────
#  JWT helpers
# ──────────────────────────────────────────────

def _make_access_token(user_id: str, username: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_ACCESS_EXP,
        "type": "access",
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _make_refresh_token(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    with sqlite3.connect(str(DB_PATH)) as db:
        db.execute(
            "INSERT INTO refresh_tokens (token,user_id,expires_at,created_at) VALUES (?,?,?,?)",
            (token, user_id, time.time() + JWT_REFRESH_EXP, time.time()),
        )
    return token


def _decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def require_auth(f):
    """Decorator — validates Bearer JWT, injects g.user_id / g.username / g.role."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        token = None
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        if not token:
            token = request.cookies.get("access_token")
        if not token:
            return jsonify({"error": "Unauthorized"}), 401
        payload = _decode_access_token(token)
        if not payload:
            return jsonify({"error": "Token expired or invalid"}), 401
        g.user_id  = payload["sub"]
        g.username = payload["username"]
        g.role     = payload.get("role", "student")
        return f(*args, **kwargs)
    return wrapper


# ──────────────────────────────────────────────
#  Static page routes
# ──────────────────────────────────────────────

FILE_MAP = {
    "/":          "home.html",
    "/home":      "home.html",
    "/login":     "login_page.html",
    "/dashboard": "Dashboard_v3.html",
    "/vpl":       "vpl.html",
}

@app.route("/")
@app.route("/home")
def home():
    return send_from_directory(str(STATIC_DIR), "home.html")

@app.route("/login")
def login_page():
    return send_from_directory(str(STATIC_DIR), "login_page.html")

@app.route("/dashboard")
def dashboard():
    return send_from_directory(str(STATIC_DIR), "Dashboard_v3.html")

@app.route("/vpl")
def vpl():
    return send_from_directory(str(STATIC_DIR), "vpl.html")

@app.route("/styles.css")
def styles():
    return send_from_directory(str(STATIC_DIR), "styles.css")

@app.route("/app.js")
def appjs():
    return send_from_directory(str(STATIC_DIR), "app.js")


# ──────────────────────────────────────────────
#  Auth — Register
# ──────────────────────────────────────────────

@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(force=True) or {}

    required = ["username", "password", "full_name", "email"]
    missing  = [k for k in required if not data.get(k)]
    if missing:
        return jsonify({"error": f"Missing fields: {', '.join(missing)}"}), 400

    username  = data["username"].strip().lower()
    password  = data["password"]
    full_name = data["full_name"].strip()
    email     = data["email"].strip().lower()
    mobile    = data.get("mobile", "").strip()
    uucms     = data.get("uucms", "").strip()
    year      = int(data.get("year") or 1)
    semester  = int(data.get("semester") or 1)

    if len(username) < 3:
        return jsonify({"error": "Username must be ≥ 3 characters"}), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be ≥ 6 characters"}), 400
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return jsonify({"error": "Invalid email address"}), 400

    db = get_db()
    if db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
        return jsonify({"error": "Username already taken"}), 409
    if db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
        return jsonify({"error": "Email already registered"}), 409

    user_id   = str(uuid.uuid4())
    pw_hash   = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now       = time.time()

    db.execute("""
        INSERT INTO users (id,username,email,password_hash,full_name,mobile,uucms,year,semester,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (user_id, username, email, pw_hash, full_name, mobile, uucms or None, year, semester, now))
    db.commit()

    access  = _make_access_token(user_id, username, "student")
    refresh = _make_refresh_token(user_id)

    resp = jsonify({
        "message":  "Account created successfully",
        "user":     {"id": user_id, "username": username, "full_name": full_name, "role": "student"},
        "access_token":  access,
        "refresh_token": refresh,
    })
    resp.set_cookie("access_token",  access,  httponly=True, samesite="Lax", max_age=JWT_ACCESS_EXP)
    resp.set_cookie("refresh_token", refresh, httponly=True, samesite="Lax", max_age=JWT_REFRESH_EXP)
    return resp, 201


# ──────────────────────────────────────────────
#  Auth — Login
# ──────────────────────────────────────────────

@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True) or {}
    username = (data.get("username") or "").strip().lower()
    password = (data.get("password") or "")

    if not username or not password:
        return jsonify({"error": "Username and password required"}), 400

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        user = db.execute("SELECT * FROM users WHERE email=?", (username,)).fetchone()
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401

    if not user["password_hash"]:
        return jsonify({"error": "This account uses Google Sign-In"}), 401

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid credentials"}), 401

    now = time.time()
    db.execute("UPDATE users SET last_login=? WHERE id=?", (now, user["id"]))
    db.commit()

    access  = _make_access_token(user["id"], user["username"], user["role"])
    refresh = _make_refresh_token(user["id"])

    resp = jsonify({
        "message": "Signed in successfully",
        "user": {
            "id":        user["id"],
            "username":  user["username"],
            "full_name": user["full_name"],
            "email":     user["email"],
            "role":      user["role"],
            "avatar_url": user["avatar_url"],
        },
        "access_token":  access,
        "refresh_token": refresh,
    })
    resp.set_cookie("access_token",  access,  httponly=True, samesite="Lax", max_age=JWT_ACCESS_EXP)
    resp.set_cookie("refresh_token", refresh, httponly=True, samesite="Lax", max_age=JWT_REFRESH_EXP)
    return resp


# ──────────────────────────────────────────────
#  Auth — Refresh
# ──────────────────────────────────────────────

@app.route("/api/auth/refresh", methods=["POST"])
def refresh_token():
    token = (request.get_json(force=True) or {}).get("refresh_token") \
            or request.cookies.get("refresh_token")
    if not token:
        return jsonify({"error": "Refresh token missing"}), 400

    db  = get_db()
    row = db.execute(
        "SELECT rt.*, u.username, u.role FROM refresh_tokens rt "
        "JOIN users u ON u.id=rt.user_id WHERE rt.token=?", (token,)
    ).fetchone()

    if not row or row["expires_at"] < time.time():
        return jsonify({"error": "Refresh token expired or invalid"}), 401

    new_access = _make_access_token(row["user_id"], row["username"], row["role"])
    resp = jsonify({"access_token": new_access})
    resp.set_cookie("access_token", new_access, httponly=True, samesite="Lax", max_age=JWT_ACCESS_EXP)
    return resp


# ──────────────────────────────────────────────
#  Auth — Logout
# ──────────────────────────────────────────────

@app.route("/api/auth/logout", methods=["POST"])
def logout():
    token = (request.get_json(force=True) or {}).get("refresh_token") \
            or request.cookies.get("refresh_token")
    if token:
        db = get_db()
        db.execute("DELETE FROM refresh_tokens WHERE token=?", (token,))
        db.commit()
    resp = jsonify({"message": "Signed out"})
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token")
    return resp


# ──────────────────────────────────────────────
#  Auth — Google OAuth 2.0
# ──────────────────────────────────────────────

GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO  = "https://www.googleapis.com/oauth2/v3/userinfo"

_oauth_states: dict[str, float] = {}  # state → expiry

def _clean_states():
    now = time.time()
    expired = [k for k, v in _oauth_states.items() if v < now]
    for k in expired:
        del _oauth_states[k]

@app.route("/auth/google")
def google_auth_start():
    if GOOGLE_CLIENT_ID == "YOUR_GOOGLE_CLIENT_ID":
        return jsonify({
            "error": "Google OAuth not configured",
            "hint":  "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables"
        }), 501

    _clean_states()
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = time.time() + 600  # 10-min window

    params = (
        f"response_type=code"
        f"&client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={GOOGLE_REDIRECT_URI}"
        f"&scope=openid%20email%20profile"
        f"&state={state}"
        f"&access_type=offline"
        f"&prompt=select_account"
    )
    return redirect(f"{GOOGLE_AUTH_URL}?{params}")


@app.route("/auth/google/callback")
def google_auth_callback():
    error = request.args.get("error")
    if error:
        return redirect(f"/login?error={error}")

    code  = request.args.get("code")
    state = request.args.get("state")

    if state not in _oauth_states or _oauth_states[state] < time.time():
        return redirect("/login?error=invalid_state")
    del _oauth_states[state]

    # Exchange code for tokens
    try:
        token_resp = requests.post(GOOGLE_TOKEN_URL, data={
            "code":          code,
            "client_id":     GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri":  GOOGLE_REDIRECT_URI,
            "grant_type":    "authorization_code",
        }, timeout=10)
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
    except Exception as e:
        return redirect(f"/login?error=token_exchange_failed")

    # Fetch user profile
    try:
        profile_resp = requests.get(
            GOOGLE_USERINFO,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        profile = profile_resp.json()
    except Exception:
        return redirect("/login?error=profile_fetch_failed")

    google_id  = profile.get("sub")
    email      = profile.get("email", "")
    full_name  = profile.get("name", "")
    avatar_url = profile.get("picture", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()

    if not user:
        # Check if email already registered
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user:
            # Link Google account to existing user
            db.execute("UPDATE users SET google_id=?, avatar_url=? WHERE id=?",
                       (google_id, avatar_url, user["id"]))
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        else:
            # Create new account
            user_id  = str(uuid.uuid4())
            username = re.sub(r"[^a-z0-9_]", "", email.split("@")[0].lower()) or f"user_{user_id[:8]}"
            # Ensure unique username
            base, i = username, 0
            while db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                i += 1; username = f"{base}{i}"
            db.execute("""
                INSERT INTO users (id,username,email,full_name,avatar_url,google_id,role,created_at)
                VALUES (?,?,?,?,?,?,'student',?)
            """, (user_id, username, email, full_name, avatar_url, google_id, time.time()))
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    db.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), user["id"]))
    db.commit()

    access  = _make_access_token(user["id"], user["username"], user["role"])
    refresh = _make_refresh_token(user["id"])

    resp = redirect("/dashboard")
    resp.set_cookie("access_token",  access,  httponly=True, samesite="Lax", max_age=JWT_ACCESS_EXP)
    resp.set_cookie("refresh_token", refresh, httponly=True, samesite="Lax", max_age=JWT_REFRESH_EXP)
    return resp


# ──────────────────────────────────────────────
#  User Profile
# ──────────────────────────────────────────────

@app.route("/api/me", methods=["GET"])
@require_auth
def me():
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (g.user_id,)).fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Presence
    presence = db.execute("SELECT * FROM presence WHERE user_id=?", (g.user_id,)).fetchone()

    return jsonify({
        "id":        user["id"],
        "username":  user["username"],
        "full_name": user["full_name"],
        "email":     user["email"],
        "mobile":    user["mobile"],
        "uucms":     user["uucms"],
        "year":      user["year"],
        "semester":  user["semester"],
        "role":      user["role"],
        "avatar_url":user["avatar_url"],
        "created_at":user["created_at"],
        "last_login":user["last_login"],
        "online":    bool(presence and presence["online"]) if presence else False,
        "last_seen": presence["last_seen"] if presence else None,
    })


@app.route("/api/me", methods=["PATCH"])
@require_auth
def update_me():
    data    = request.get_json(force=True) or {}
    allowed = {"full_name", "mobile", "year", "semester", "avatar_url"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return jsonify({"error": "Nothing to update"}), 400

    db = get_db()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    db.execute(f"UPDATE users SET {set_clause} WHERE id=?",
               (*updates.values(), g.user_id))
    db.commit()
    return jsonify({"message": "Profile updated"})


@app.route("/api/me/change-password", methods=["POST"])
@require_auth
def change_password():
    data     = request.get_json(force=True) or {}
    old_pw   = data.get("old_password", "")
    new_pw   = data.get("new_password", "")

    if len(new_pw) < 6:
        return jsonify({"error": "New password must be ≥ 6 characters"}), 400

    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (g.user_id,)).fetchone()
    if not user["password_hash"]:
        return jsonify({"error": "OAuth account — no password to change"}), 400
    if not bcrypt.checkpw(old_pw.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Current password incorrect"}), 401

    new_hash = bcrypt.hashpw(new_pw.encode(), bcrypt.gensalt()).decode()
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (new_hash, g.user_id))
    db.commit()
    return jsonify({"message": "Password changed"})


# ──────────────────────────────────────────────
#  Presence — REST fallback
# ──────────────────────────────────────────────

@app.route("/api/presence/online", methods=["GET"])
@require_auth
def online_users():
    """Returns list of users currently online (within last 5 minutes via socket or heartbeat)."""
    db   = get_db()
    rows = db.execute("""
        SELECT u.id, u.username, u.full_name, u.avatar_url,
               p.last_seen, p.online
        FROM presence p
        JOIN users u ON u.id = p.user_id
        WHERE p.online = 1 AND p.last_seen > ?
    """, (time.time() - 300,)).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/presence/heartbeat", methods=["POST"])
@require_auth
def heartbeat():
    """REST heartbeat for clients that can't use WebSocket."""
    db  = get_db()
    now = time.time()
    db.execute("""
        INSERT INTO presence (user_id, online, last_seen)
        VALUES (?,1,?)
        ON CONFLICT(user_id) DO UPDATE SET online=1, last_seen=?
    """, (g.user_id, now, now))
    db.commit()
    return jsonify({"ok": True, "server_time": now})


# ──────────────────────────────────────────────
#  VPL — Code Execution Proxy
# ──────────────────────────────────────────────

def _vpl_backend_available() -> bool:
    try:
        r = requests.get(f"{VPL_LOCAL_URL}/health", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


@app.route("/api/v2/piston/runtimes", methods=["GET"])
def vpl_runtimes():
    """Returns available runtimes — tries local backend, falls back to public Piston."""
    if _vpl_backend_available():
        try:
            r = requests.get(f"{VPL_LOCAL_URL}/api/v2/piston/runtimes", timeout=4)
            return (r.content, r.status_code, {"Content-Type": "application/json"})
        except Exception:
            pass
    try:
        r = requests.get(f"{VPL_PISTON_URL}/api/v2/piston/runtimes", timeout=8)
        return (r.content, r.status_code, {"Content-Type": "application/json"})
    except Exception:
        return jsonify([]), 503


@app.route("/api/v2/piston/execute", methods=["POST"])
def vpl_execute():
    """
    Proxies code execution.
    Priority: local vpl_backend.py → public Piston API (emkc.org).
    Logs the run (no code stored — only hash + metadata).
    """
    body = request.get_json(force=True)
    if not body:
        return jsonify({"error": "No JSON body"}), 400

    # Log run metadata (optional — no sensitive code stored)
    try:
        code = (body.get("files") or [{}])[0].get("content", "")
        code_hash = hashlib.sha256(code.encode()).hexdigest()[:16]
        language  = body.get("language", "unknown")

        # Get user from token if present
        user_id = None
        token = request.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            payload = _decode_access_token(token[7:])
            if payload:
                user_id = payload["sub"]
        if not user_id:
            token = request.cookies.get("access_token")
            if token:
                payload = _decode_access_token(token)
                if payload:
                    user_id = payload["sub"]

        with sqlite3.connect(str(DB_PATH)) as _db:
            _db.execute(
                "INSERT INTO vpl_runs (id,user_id,language,code_hash,exit_code,ran_at) VALUES (?,?,?,?,?,?)",
                (str(uuid.uuid4()), user_id, language, code_hash, -99, time.time())
            )
    except Exception:
        pass  # Logging is best-effort

    # Try local backend
    if _vpl_backend_available():
        try:
            r = requests.post(
                f"{VPL_LOCAL_URL}/api/v2/piston/execute",
                json=body,
                timeout=35,
            )
            result = r.json()
            _log_exit_code(code_hash, language, result)
            return jsonify(result)
        except Exception:
            pass

    # Fall back to public Piston API
    try:
        r = requests.post(
            f"{VPL_PISTON_URL}/api/v2/piston/execute",
            json=body,
            timeout=35,
        )
        result = r.json()
        _log_exit_code(code_hash, language, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "compile": {"stdout": "", "stderr": "", "code": 0},
            "run": {
                "stdout": "",
                "stderr": f"[BCU Backend] Code execution unavailable: {e}\n"
                          "Start vpl_backend.py locally or check your internet connection.",
                "code": -1,
            }
        }), 503


def _log_exit_code(code_hash: str, language: str, result: dict):
    try:
        exit_code = (result.get("run") or {}).get("code", -1)
        with sqlite3.connect(str(DB_PATH)) as _db:
            _db.execute(
                "UPDATE vpl_runs SET exit_code=? WHERE code_hash=? AND language=? AND exit_code=-99",
                (exit_code, code_hash, language)
            )
    except Exception:
        pass


# ──────────────────────────────────────────────
#  Admin — VPL run stats (bonus)
# ──────────────────────────────────────────────

@app.route("/api/admin/vpl-stats", methods=["GET"])
@require_auth
def vpl_stats():
    if g.role != "admin":
        return jsonify({"error": "Admin only"}), 403
    db = get_db()
    rows = db.execute("""
        SELECT language, COUNT(*) as runs,
               SUM(CASE WHEN exit_code=0 THEN 1 ELSE 0 END) as passed
        FROM vpl_runs GROUP BY language ORDER BY runs DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


# ──────────────────────────────────────────────
#  Health check
# ──────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":         "ok",
        "service":        "BCU LMS Backend",
        "version":        "1.0.0",
        "vpl_local_up":   _vpl_backend_available(),
        "server_time":    datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/health", methods=["GET"])
def api_health():
    return health()


# ──────────────────────────────────────────────
#  WebSocket — Presence & Real-time
# ──────────────────────────────────────────────

_online_users: dict[str, dict] = {}  # socket_id → {user_id, username, joined_at}
_lock = threading.Lock()


def _sync_presence_db(user_id: str, online: bool, socket_id: str | None = None):
    try:
        now = time.time()
        with sqlite3.connect(str(DB_PATH)) as db:
            db.execute("""
                INSERT INTO presence (user_id, socket_id, online, last_seen)
                VALUES (?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET socket_id=?, online=?, last_seen=?
            """, (user_id, socket_id, int(online), now,
                  socket_id, int(online), now))
    except Exception as e:
        print(f"[WS] DB presence sync error: {e}")


@socketio.on("connect")
def ws_connect():
    token = request.args.get("token") or request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token:
        disconnect(); return False

    payload = _decode_access_token(token)
    if not payload:
        disconnect(); return False

    uid      = payload["sub"]
    uname    = payload["username"]
    sid      = request.sid

    with _lock:
        _online_users[sid] = {"user_id": uid, "username": uname, "joined_at": time.time()}

    _sync_presence_db(uid, True, sid)
    join_room(f"user_{uid}")
    join_room("global")

    emit("presence_update", {
        "event":    "joined",
        "user_id":  uid,
        "username": uname,
        "online_count": len(_online_users),
    }, to="global")

    # Send current online list to the new user
    with _lock:
        online = [v for v in _online_users.values()]
    emit("online_list", {"users": online, "count": len(online)})

    print(f"[WS] {uname} connected  (sid={sid[:8]}…)")


@socketio.on("disconnect")
def ws_disconnect():
    sid = request.sid
    with _lock:
        info = _online_users.pop(sid, None)

    if info:
        _sync_presence_db(info["user_id"], False)
        emit("presence_update", {
            "event":    "left",
            "user_id":  info["user_id"],
            "username": info["username"],
            "online_count": len(_online_users),
        }, to="global")
        print(f"[WS] {info['username']} disconnected")


@socketio.on("heartbeat")
def ws_heartbeat(data):
    sid = request.sid
    with _lock:
        info = _online_users.get(sid)
    if info:
        _sync_presence_db(info["user_id"], True, sid)
        emit("heartbeat_ack", {"server_time": time.time()})


@socketio.on("notify")
def ws_notify(data):
    """Send a notification to a specific user room."""
    sid = request.sid
    with _lock:
        info = _online_users.get(sid)
    if not info:
        return
    target_uid = data.get("to_user_id")
    if target_uid:
        emit("notification", {
            "from":    info["username"],
            "message": data.get("message", ""),
            "at":      time.time(),
        }, to=f"user_{target_uid}")


# ──────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    init_db()

    banner = """
╔══════════════════════════════════════════════════════╗
║         BCU LMS — Application Backend                ║
╠══════════════════════════════════════════════════════╣
║  HTTP  →  http://localhost:8000                      ║
║  Pages →  /login  /dashboard  /vpl                   ║
╠══════════════════════════════════════════════════════╣
║  API Endpoints                                       ║
║  POST /api/auth/register    ← create account         ║
║  POST /api/auth/login       ← sign in (JWT)          ║
║  POST /api/auth/refresh     ← refresh access token   ║
║  POST /api/auth/logout      ← revoke refresh token   ║
║  GET  /auth/google          ← Google OAuth start     ║
║  GET  /api/me               ← profile (auth)         ║
║  GET  /api/presence/online  ← online users (auth)    ║
║  POST /api/v2/piston/execute← code execution proxy   ║
║  GET  /health               ← server health          ║
╠══════════════════════════════════════════════════════╣
║  WebSocket (Socket.IO)                               ║
║  ws://localhost:8000  ?token=<access_token>          ║
║  Events: connect / disconnect / heartbeat / notify   ║
╠══════════════════════════════════════════════════════╣
║  Demo account:  demo / demo1234                      ║
╚══════════════════════════════════════════════════════╝
"""
    print(banner)
    socketio.run(app, host="0.0.0.0", port=8000, debug=False, allow_unsafe_werkzeug=True)
