"""
BCU LMS — Flask Backend
Run: python app.py
API runs on http://localhost:5000
"""

from flask import Flask, request, jsonify, session
from flask_cors import CORS
from database import init_db, get_db
from werkzeug.security import generate_password_hash, check_password_hash
import re, os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "bcu-lms-secret-key-change-in-production")
CORS(app, supports_credentials=True, origins=["*"])

# ── Initialise DB on startup ──────────────────────────────────────────────────
with app.app_context():
    init_db()


# ── Helper ────────────────────────────────────────────────────────────────────
def ok(data=None, msg="Success", code=200):
    body = {"success": True, "message": msg}
    if data is not None:
        body["data"] = data
    return jsonify(body), code


def err(msg="Error", code=400):
    return jsonify({"success": False, "message": msg}), code


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return jsonify({"service": "BCU LMS API", "version": "1.0"})


# ---------- REGISTER ----------------------------------------------------------
@app.route("/api/register", methods=["POST"])
def register():
    d = request.get_json(silent=True) or {}

    # Required fields
    name   = (d.get("name")   or "").strip()
    email  = (d.get("email")  or "").strip().lower()
    mobile = re.sub(r"\D", "", d.get("mobile") or "")
    uucms  = (d.get("uucms")  or "").strip().upper()
    year   = d.get("year")
    sem    = d.get("sem")
    uname  = (d.get("username") or "").strip().lower()
    pw     = d.get("password") or ""
    pw2    = d.get("confirm_password") or ""

    # Validation
    if not name:
        return err("Full name is required.")
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        return err("Please enter a valid email address.")
    if len(mobile) < 10:
        return err("Please enter a valid 10-digit mobile number.")
    if len(uucms) < 5:
        return err("Please enter a valid UUCMS number.")
    if not year:
        return err("Please select your year.")
    if not sem:
        return err("Please select your semester.")
    if len(uname) < 3:
        return err("Username must be at least 3 characters.")
    if len(pw) < 6:
        return err("Password must be at least 6 characters.")
    if pw != pw2:
        return err("Passwords do not match.")

    db = get_db()

    # Duplicate checks
    if db.execute("SELECT id FROM users WHERE username = ?", (uname,)).fetchone():
        return err("Username already taken. Please choose another.")
    if db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone():
        return err("An account with this email already exists.")
    if db.execute("SELECT id FROM users WHERE uucms_number = ?", (uucms,)).fetchone():
        return err("An account with this UUCMS number already exists.")

    pw_hash = generate_password_hash(pw)
    db.execute(
        """INSERT INTO users
           (full_name, email, mobile, uucms_number, year, semester, username, password_hash)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (name, email, mobile, uucms, int(year), int(sem), uname, pw_hash)
    )
    db.commit()

    return ok(msg="Account created successfully! You can now log in.", code=201)


# ---------- LOGIN -------------------------------------------------------------
@app.route("/api/login", methods=["POST"])
def login():
    d = request.get_json(silent=True) or {}
    uname = (d.get("username") or "").strip().lower()
    pw    = d.get("password") or ""

    if len(uname) < 3:
        return err("Username must be at least 3 characters.")
    if len(pw) < 4:
        return err("Password must be at least 4 characters.")

    db   = get_db()
    user = db.execute(
        "SELECT * FROM users WHERE username = ?", (uname,)
    ).fetchone()

    if not user or not check_password_hash(user["password_hash"], pw):
        return err("Invalid username or password.", 401)

    # Store session
    session["user_id"]  = user["id"]
    session["username"] = user["username"]

    # Log the login event
    db.execute(
        "INSERT INTO login_logs (user_id, ip_address) VALUES (?, ?)",
        (user["id"], request.remote_addr)
    )
    db.commit()

    return ok(
        data={
            "id":       user["id"],
            "username": user["username"],
            "name":     user["full_name"],
            "email":    user["email"],
            "year":     user["year"],
            "semester": user["semester"],
        },
        msg="Signed in successfully! Redirecting…"
    )


# ---------- LOGOUT ------------------------------------------------------------
@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return ok(msg="Logged out.")


# ---------- SESSION CHECK -----------------------------------------------------
@app.route("/api/me", methods=["GET"])
def me():
    uid = session.get("user_id")
    if not uid:
        return err("Not authenticated.", 401)
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not user:
        return err("User not found.", 404)
    return ok(data={
        "id":       user["id"],
        "username": user["username"],
        "name":     user["full_name"],
        "email":    user["email"],
        "year":     user["year"],
        "semester": user["semester"],
    })


# ---------- ADMIN: list users (demo only) ------------------------------------
@app.route("/api/admin/users", methods=["GET"])
def admin_users():
    rows = get_db().execute(
        "SELECT id, full_name, email, uucms_number, year, semester, username, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    return ok(data=[dict(r) for r in rows])


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=5000)
