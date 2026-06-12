# BCU LMS — Full Application Backend

`bcu_lms_backend.py` is a single-file Flask server that brings together
authentication, presence tracking, VPL code execution, and static file
serving for the entire BCU LMS frontend suite.

---

## Quick Start

### 1. Install dependencies

```bash
pip install flask flask-cors flask-socketio pyjwt bcrypt requests eventlet
```

### 2. Place files together

```
your-project/
├── bcu_lms_backend.py   ← this file
├── login_page.html
├── Dashboard_v3.html
├── vpl.html
├── home.html
├── styles.css
└── app.js
```

### 3. Start the server

```bash
python3 bcu_lms_backend.py
```

### 4. Open in browser

| Page      | URL                            |
|-----------|-------------------------------|
| Home      | http://localhost:8000/         |
| Login     | http://localhost:8000/login    |
| Dashboard | http://localhost:8000/dashboard|
| VPL       | http://localhost:8000/vpl      |
| Health    | http://localhost:8000/health   |

> **Demo account** — username: `demo` / password: `demo1234`

---

## What's Inside

### Authentication (JWT)

| Endpoint                    | Method | Description                  |
|-----------------------------|--------|------------------------------|
| `/api/auth/register`        | POST   | Create student account       |
| `/api/auth/login`           | POST   | Sign in, get JWT tokens      |
| `/api/auth/refresh`         | POST   | Get new access token         |
| `/api/auth/logout`          | POST   | Revoke refresh token         |
| `/api/me`                   | GET    | Fetch own profile (auth)     |
| `/api/me`                   | PATCH  | Update profile (auth)        |
| `/api/me/change-password`   | POST   | Change password (auth)       |

Tokens are returned in both the JSON body and as `HttpOnly` cookies
so they work for both SPA-style fetches and normal page loads.

**Access tokens** expire in 1 hour. **Refresh tokens** expire in 7 days
and are stored in SQLite for revocation support.

### Google OAuth 2.0

```
GET /auth/google           ← redirects to Google consent screen
GET /auth/google/callback  ← handles the code exchange
```

To enable, set environment variables:
```bash
export GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
export GOOGLE_CLIENT_SECRET="your-client-secret"
export GOOGLE_REDIRECT_URI="http://localhost:8000/auth/google/callback"
```
Get credentials at https://console.cloud.google.com → APIs & Services → Credentials.
Add `http://localhost:8000/auth/google/callback` as an authorized redirect URI.

### WebSocket Presence (Socket.IO)

Connect with the access token:
```javascript
const socket = io("http://localhost:8000", {
  auth: { token: accessToken }
});

socket.on("presence_update", ({ event, username, online_count }) => { … });
socket.on("online_list",     ({ users, count }) => { … });
socket.on("notification",    ({ from, message }) => { … });

// Keep presence alive
setInterval(() => socket.emit("heartbeat"), 30_000);
```

### VPL Code Execution

`/api/v2/piston/execute` is a smart proxy:

1. **Tries local `vpl_backend.py`** (if running on port 5000)
2. **Falls back to public Piston API** (emkc.org) if local is unavailable

This means VPL works even without the local compiler — just with slightly
higher latency and subject to external rate limits.

```
GET  /api/v2/piston/runtimes   → list available languages
POST /api/v2/piston/execute    → run code (Piston-compatible body)
```

The frontend's `VPL_BACKEND_URL` should now point to `http://localhost:8000`
instead of `http://localhost:5000` or `https://emkc.org`.

---

## Connecting the Frontend

In `vpl.html`, change the backend URL constant (near the top of the `<script>`):

```javascript
// Before
const VPL_BACKEND_URL = 'https://emkc.org';   // or localhost:5000

// After
const VPL_BACKEND_URL = 'http://localhost:8000';
```

For login, wire the `doLogin()` function to call the real API:

```javascript
async function doLogin() {
  const resp = await fetch('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    credentials: 'include',
    body: JSON.stringify({
      username: document.getElementById('login-uname').value,
      password: document.getElementById('login-pw').value,
    })
  });
  const data = await resp.json();
  if (resp.ok) {
    localStorage.setItem('access_token', data.access_token);
    window.location.href = '/dashboard';
  } else {
    showToast('toast-login', 'toast-login-msg', data.error, false);
  }
}
```

---

## Storage

Everything is stored in `bcu_lms.db` (SQLite — created automatically):

| Table            | Purpose                                  |
|------------------|------------------------------------------|
| `users`          | Accounts (local + Google OAuth)          |
| `refresh_tokens` | Active refresh tokens (revocable)        |
| `sessions`       | Browser session metadata                 |
| `presence`       | Online/offline status + last-seen        |
| `vpl_runs`       | Code run log (language, hash, exit code) |

---

## Environment Variables

| Variable               | Default                         | Purpose             |
|------------------------|---------------------------------|---------------------|
| `SECRET_KEY`           | random (changes on restart)     | JWT signing key     |
| `GOOGLE_CLIENT_ID`     | `YOUR_GOOGLE_CLIENT_ID`         | Google OAuth        |
| `GOOGLE_CLIENT_SECRET` | `YOUR_GOOGLE_CLIENT_SECRET`     | Google OAuth        |
| `GOOGLE_REDIRECT_URI`  | `http://localhost:8000/auth/…`  | Google OAuth        |
| `VPL_BACKEND_URL`      | `http://localhost:5000`         | Local VPL compiler  |

> **Production note**: Set `SECRET_KEY` to a fixed value in production
> so tokens survive server restarts.

---

## Architecture

```
Browser
  │  HTTP (pages + REST API)
  │  WebSocket (Socket.IO presence)
  ▼
bcu_lms_backend.py  :8000
  ├── Static pages   → login / dashboard / vpl / home
  ├── JWT Auth       → register / login / refresh / logout
  ├── Google OAuth   → /auth/google → callback
  ├── Profile API    → GET/PATCH /api/me
  ├── Presence       → WebSocket + REST heartbeat
  └── VPL Proxy      → localhost:5000 (vpl_backend.py)
                           └── fallback: emkc.org Piston
```
