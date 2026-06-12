#!/usr/bin/env python3
"""
VPL Real Compiler Backend
=========================
Mimics the Piston API (emkc.org) so the VPL frontend works with zero JS changes
except pointing to http://localhost:5000 instead of https://emkc.org.

Supported languages: Python, C, C++, Java, JavaScript (Node.js), Bash, R

Run:
    python3 vpl_backend.py

Endpoints:
    GET  /api/v2/piston/runtimes   → list available runtimes
    POST /api/v2/piston/execute    → compile + run code, return output
"""

import os
import sys
import uuid
import shutil
import subprocess
import tempfile
import threading
import time
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # Allow all origins so the HTML frontend can call this from any port/host

# ─── Runtime table ────────────────────────────────────────────────────────────
# Piston language name → { version, ext, compile_cmd, run_cmd }
# Placeholders {src}, {exe}, {dir} are filled in at execution time.

def _bin(name):
    """Return absolute path of binary or None if not found."""
    return shutil.which(name)

RUNTIMES = {}

def _register_runtimes():
    """Discover which languages are actually available on this machine."""
    global RUNTIMES

    candidates = [
        {
            "language": "python",
            "aliases": ["python3", "py"],
            "version": "3.x",
            "ext": ".py",
            "bin": "python3",
            "compile": None,  # interpreted
            "run": ["{bin}", "{src}"],
        },
        {
            "language": "c",
            "aliases": [],
            "version": "gcc-latest",
            "ext": ".c",
            "bin": "gcc",
            "compile": ["{bin}", "{src}", "-o", "{exe}", "-lm"],
            "run": ["{exe}"],
        },
        {
            "language": "c++",
            "aliases": ["cpp"],
            "version": "g++-latest",
            "ext": ".cpp",
            "bin": "g++",
            "compile": ["{bin}", "{src}", "-o", "{exe}"],
            "run": ["{exe}"],
        },
        {
            "language": "java",
            "aliases": [],
            "version": "openjdk-latest",
            "ext": ".java",
            "bin": "java",
            "compile_bin": "javac",
            "compile": ["{compile_bin}", "{src}"],
            "run": ["java", "-cp", "{dir}", "{classname}"],
        },
        {
            "language": "javascript",
            "aliases": ["js", "node"],
            "version": "node-latest",
            "ext": ".js",
            "bin": "node",
            "compile": None,
            "run": ["{bin}", "{src}"],
        },
        {
            "language": "bash",
            "aliases": ["shell", "sh"],
            "version": "bash-latest",
            "ext": ".sh",
            "bin": "bash",
            "compile": None,
            "run": ["{bin}", "{src}"],
        },
        {
            "language": "r",
            "aliases": [],
            "version": "r-latest",
            "ext": ".r",
            "bin": "Rscript",
            "compile": None,
            "run": ["{bin}", "{src}"],
        },
    ]

    for rt in candidates:
        main_bin = rt["bin"]
        compile_bin = rt.get("compile_bin")

        if not _bin(main_bin):
            continue
        if compile_bin and not _bin(compile_bin):
            continue

        # Attempt to auto-detect real version
        try:
            result = subprocess.run(
                [main_bin, "--version"],
                capture_output=True, text=True, timeout=3
            )
            ver_line = (result.stdout or result.stderr or "").split("\n")[0].strip()
            if ver_line:
                rt["version"] = ver_line[:60]
        except Exception:
            pass

        RUNTIMES[rt["language"]] = rt
        for alias in rt.get("aliases", []):
            RUNTIMES[alias] = rt

    print(f"[VPL] Available runtimes: {list({r['language'] for r in RUNTIMES.values()})}")

_register_runtimes()

# ─── Helpers ──────────────────────────────────────────────────────────────────

TIMEOUT_COMPILE = 15   # seconds
TIMEOUT_RUN     = 10   # seconds
MAX_OUTPUT      = 50_000  # characters

def _run_proc(cmd, stdin_data="", cwd=None, timeout=TIMEOUT_RUN):
    """Run a subprocess and return (stdout, stderr, exit_code, timed_out)."""
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return proc.stdout[:MAX_OUTPUT], proc.stderr[:MAX_OUTPUT], proc.returncode, False
    except subprocess.TimeoutExpired:
        return "", f"[VPL] Execution timed out after {timeout}s", -1, True
    except FileNotFoundError as e:
        return "", f"[VPL] Command not found: {e}", -1, False
    except Exception as e:
        return "", f"[VPL] Internal error: {e}", -1, False


def _fill(template_list, **kwargs):
    """Replace {placeholder} tokens in each element of a command list."""
    return [tok.format(**kwargs) for tok in template_list]


def _execute(lang_key, code, filename, stdin_data, compile_timeout, run_timeout):
    """
    Core execution logic.
    Returns dict: { compile: {stdout, stderr, code}, run: {stdout, stderr, code} }
    """
    rt = RUNTIMES.get(lang_key)
    if rt is None:
        return {
            "compile": {"stdout": "", "stderr": f"Runtime '{lang_key}' not available on this server.", "code": 1},
            "run":     {"stdout": "", "stderr": "", "code": 0},
        }

    work_dir = tempfile.mkdtemp(prefix="vpl_")
    try:
        ext = rt["ext"]
        # Derive a safe source filename
        safe_name = filename if filename.endswith(ext) else (os.path.splitext(filename)[0] + ext)
        src_path  = os.path.join(work_dir, safe_name)
        exe_path  = os.path.join(work_dir, "program")
        classname = os.path.splitext(safe_name)[0]  # For Java

        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)

        compile_result = {"stdout": "", "stderr": "", "code": 0}

        # ── Compile phase ──
        if rt.get("compile"):
            kwargs = {
                "bin": _bin(rt["bin"]) or rt["bin"],
                "compile_bin": _bin(rt.get("compile_bin", "")) or rt.get("compile_bin", ""),
                "src": src_path,
                "exe": exe_path,
                "dir": work_dir,
                "classname": classname,
            }
            cmd = _fill(rt["compile"], **kwargs)
            out, err, code, _ = _run_proc(cmd, cwd=work_dir, timeout=compile_timeout)
            compile_result = {"stdout": out, "stderr": err, "code": code}
            if code != 0:
                return {"compile": compile_result, "run": {"stdout": "", "stderr": "", "code": 0}}

        # ── Run phase ──
        kwargs = {
            "bin": _bin(rt["bin"]) or rt["bin"],
            "src": src_path,
            "exe": exe_path,
            "dir": work_dir,
            "classname": classname,
        }
        cmd = _fill(rt["run"], **kwargs)
        out, err, code, timed_out = _run_proc(cmd, stdin_data=stdin_data, cwd=work_dir, timeout=run_timeout)
        run_result = {"stdout": out, "stderr": err, "code": code}

        return {"compile": compile_result, "run": run_result}

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/api/v2/piston/runtimes", methods=["GET"])
def runtimes():
    """Return list of available runtimes (Piston-compatible format)."""
    seen = set()
    result = []
    for rt in RUNTIMES.values():
        lang = rt["language"]
        if lang in seen:
            continue
        seen.add(lang)
        result.append({
            "language": lang,
            "version":  rt["version"],
            "aliases":  rt.get("aliases", []),
            "runtime":  lang,
        })
    return jsonify(result)


@app.route("/api/v2/piston/execute", methods=["POST"])
def execute():
    """Execute code. Accepts Piston-compatible JSON body."""
    body = request.get_json(force=True)
    if not body:
        return jsonify({"error": "No JSON body"}), 400

    language         = (body.get("language") or "").lower()
    files            = body.get("files", [])
    stdin_data       = body.get("stdin", "") or ""
    compile_timeout  = min(int(body.get("compile_timeout", 10000)) // 1000, 30)
    run_timeout      = min(int(body.get("run_timeout", 5000)) // 1000, 15)

    if not files:
        return jsonify({"error": "No files provided"}), 400

    first_file = files[0]
    code     = first_file.get("content", "")
    filename = first_file.get("name", "main")

    # Map common aliases
    lang_map = {
        "python3": "python",
        "py":      "python",
        "cpp":     "c++",
        "js":      "javascript",
        "node":    "javascript",
        "shell":   "bash",
        "sh":      "bash",
    }
    language = lang_map.get(language, language)

    result = _execute(language, code, filename, stdin_data, compile_timeout, run_timeout)

    # Log summary
    run_code = result.get("run", {}).get("code", 0)
    cmp_err  = bool((result.get("compile") or {}).get("stderr", "").strip())
    print(f"[VPL] lang={language!r}  file={filename!r}  "
          f"compile_err={cmp_err}  exit={run_code}")

    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    langs = list({rt["language"] for rt in RUNTIMES.values()})
    return jsonify({"status": "ok", "available_languages": langs})


@app.route("/", methods=["GET"])
def index():
    langs = list({rt["language"] for rt in RUNTIMES.values()})
    return jsonify({
        "service": "VPL Real Compiler Backend",
        "version": "1.0.0",
        "endpoints": {
            "runtimes": "GET /api/v2/piston/runtimes",
            "execute":  "POST /api/v2/piston/execute",
            "health":   "GET /health",
        },
        "available_languages": langs,
    })


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  VPL Real Compiler Backend  —  http://localhost:5000")
    print("=" * 60)
    print()
    app.run(host="0.0.0.0", port=5000, debug=False)
