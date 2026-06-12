# BCU LMS — Backend Setup

## Project Structure
```
bcu_lms/
├── app.py            # Flask application & API routes
├── database.py       # SQLite schema & connection helpers
├── bcu_lms.db        # SQLite database (auto-created on first run)
├── requirements.txt  # Python dependencies
└── login_page.html   # Updated frontend (connects to real API)
```

## Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the server
```bash
python app.py
```
Server starts at **http://localhost:5000**  
The SQLite database (`bcu_lms.db`) is created automatically.

### 3. Open the login page
Open `login_page.html` in your browser. It will talk to `http://localhost:5000/api`.

---

## API Endpoints

| Method | Endpoint            | Description                  |
|--------|---------------------|------------------------------|
| POST   | `/api/register`     | Create a new student account |
| POST   | `/api/login`        | Authenticate a user          |
| POST   | `/api/logout`       | Clear the session            |
| GET    | `/api/me`           | Get current logged-in user   |
| GET    | `/api/admin/users`  | List all users (dev/admin)   |

### Register — POST `/api/register`
```json
{
  "name": "Ravi Kumar",
  "email": "ravi@university.edu",
  "mobile": "9876543210",
  "uucms": "UUCMS2024001",
  "year": "2",
  "sem": "3",
  "username": "ravikumar",
  "password": "secret123",
  "confirm_password": "secret123"
}
```

### Login — POST `/api/login`
```json
{
  "username": "ravikumar",
  "password": "secret123"
}
```

---

## Database Schema

### `users`
| Column        | Type    | Notes                        |
|---------------|---------|------------------------------|
| id            | INTEGER | Primary key, auto-increment  |
| full_name     | TEXT    | Student's full name          |
| email         | TEXT    | Unique                       |
| mobile        | TEXT    | 10-digit number              |
| uucms_number  | TEXT    | Unique university ID         |
| year          | INTEGER | 1–4                          |
| semester      | INTEGER | 1–8                          |
| username      | TEXT    | Unique login handle          |
| password_hash | TEXT    | bcrypt via Werkzeug          |
| is_active     | INTEGER | 1 = active, 0 = disabled     |
| created_at    | DATETIME| Auto-set on insert           |

### `login_logs`
| Column     | Type    | Notes                     |
|------------|---------|---------------------------|
| id         | INTEGER | Primary key               |
| user_id    | INTEGER | FK → users.id             |
| ip_address | TEXT    | Client IP                 |
| logged_at  | DATETIME| Auto-set on insert        |

---

## Security Notes
- Passwords are hashed with **Werkzeug's `generate_password_hash`** (PBKDF2-SHA256).
- Change `SECRET_KEY` via environment variable before deploying:
  ```bash
  export SECRET_KEY="your-strong-random-key"
  ```
- For production, replace SQLite with PostgreSQL/MySQL and run behind HTTPS.
