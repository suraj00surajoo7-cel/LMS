# VPL Real Compiler Backend — Setup Guide

## What You Get

| File | Purpose |
|------|---------|
| `vpl_backend.py` | Python Flask server — executes code locally |
| `vpl_with_backend.html` | Your VPL frontend, wired to the local backend |

---

## Quick Start (3 steps)

### 1. Install dependencies
```bash
pip install flask flask-cors
```

### 2. Start the backend
```bash
python3 vpl_backend.py
```
You should see:
```
============================================================
  VPL Real Compiler Backend  —  http://localhost:5000
============================================================
[VPL] Available runtimes: ['python', 'c', 'c++', 'javascript', 'bash']
```

### 3. Open the frontend
Open `vpl_with_backend.html` in your browser.

The **Backend** badge in the top bar will turn **green** once connected.

---

## Supported Languages

| Language | Requires |
|----------|---------|
| Python 3 | `python3` (usually pre-installed) |
| C | `gcc` — `sudo apt install gcc` |
| C++ | `g++` — `sudo apt install g++` |
| JavaScript | `node` — `sudo apt install nodejs` |
| Bash | `bash` (pre-installed) |
| Java | `java` + `javac` — `sudo apt install default-jdk` |
| R | `Rscript` — `sudo apt install r-base` |

---

## How It Works

```
Browser (vpl_with_backend.html)
    │
    │  POST /api/v2/piston/execute
    │  { language, code, stdin }
    ▼
Python Flask Server (vpl_backend.py :5000)
    │
    ├─ Python  →  python3 script.py  < stdin
    ├─ C       →  gcc src.c -o exe  →  ./exe < stdin
    ├─ C++     →  g++ src.cpp -o exe  →  ./exe < stdin
    ├─ JS      →  node script.js  < stdin
    ├─ Bash    →  bash script.sh  < stdin
    └─ Java    →  javac Main.java  →  java -cp . Main < stdin
    │
    └─ Returns { compile: {stdout,stderr,code}, run: {stdout,stderr,code} }
```

Each execution runs in a **temporary directory** that is deleted after the run.

---

## API Reference

### GET /health
Returns server status and available languages.

### GET /api/v2/piston/runtimes
Returns list of installed runtimes (Piston-compatible format).

### POST /api/v2/piston/execute
**Request body:**
```json
{
  "language": "python",
  "version": "3.x",
  "files": [{ "name": "main.py", "content": "print('Hello')" }],
  "stdin": "optional input",
  "compile_timeout": 10000,
  "run_timeout": 5000
}
```

**Response:**
```json
{
  "compile": { "stdout": "", "stderr": "", "code": 0 },
  "run":     { "stdout": "Hello\n", "stderr": "", "code": 0 }
}
```

---

## Security Notes

- Code runs **as your user** locally — treat this as a dev tool, not a public server.
- Execution is sandboxed in a temp directory with configurable timeouts (default 10s compile, 10s run).
- For production / multi-user use, add Docker sandboxing or use a dedicated service like Judge0.

---

## Changing the Backend URL

Edit the first line of the `pistonRun` function in `vpl_with_backend.html`:

```js
const VPL_BACKEND_URL = 'http://localhost:5000';
// Change to your server, e.g.:
// const VPL_BACKEND_URL = 'http://192.168.1.100:5000';
```
