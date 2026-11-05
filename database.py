import os, sqlite3, json
from contextlib import contextmanager
from config import DATABASE_PATH

def _connect():
    os.makedirs(os.path.dirname(DATABASE_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

@contextmanager
def db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT, first_name TEXT,
          is_blocked INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS quizzes(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          description TEXT DEFAULT '', creator_id INTEGER,
          is_published INTEGER DEFAULT 1, question_seconds INTEGER DEFAULT 30,
          shuffle_questions INTEGER DEFAULT 0, shuffle_options INTEGER DEFAULT 0,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS questions(
          id INTEGER PRIMARY KEY AUTOINCREMENT, quiz_id INTEGER NOT NULL,
          question TEXT NOT NULL, options_json TEXT NOT NULL,
          correct_index INTEGER NOT NULL, explanation TEXT DEFAULT '',
          FOREIGN KEY(quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT, quiz_id INTEGER NOT NULL,
          user_id INTEGER NOT NULL, score INTEGER NOT NULL, total INTEGER NOT NULL,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(quiz_id) REFERENCES quizzes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS materials(
          id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          filename TEXT NOT NULL, uploaded_by INTEGER,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        """)

def upsert_user(user):
    with db() as c:
        c.execute("""INSERT INTO users(id,username,first_name) VALUES(?,?,?)
        ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name""",
        (user.id, user.username or "", user.first_name or ""))

def create_quiz(title, description, creator_id, seconds=30, published=True):
    with db() as c:
        cur = c.execute("INSERT INTO quizzes(title,description,creator_id,question_seconds,is_published) VALUES(?,?,?,?,?)",
                        (title, description, creator_id, seconds, 1 if published else 0))
        return cur.lastrowid


def set_quiz_published(quiz_id, published=True):
    with db() as c:
        c.execute("UPDATE quizzes SET is_published=? WHERE id=?", (1 if published else 0, quiz_id))


def delete_quiz(quiz_id):
    with db() as c:
        c.execute("DELETE FROM quizzes WHERE id=?", (quiz_id,))

def add_question(quiz_id, question, options, correct_index, explanation=""):
    with db() as c:
        c.execute("INSERT INTO questions(quiz_id,question,options_json,correct_index,explanation) VALUES(?,?,?,?,?)",
                  (quiz_id, question, json.dumps(options, ensure_ascii=False), correct_index, explanation))

def get_quiz(quiz_id):
    with db() as c:
        q = c.execute("SELECT * FROM quizzes WHERE id=? AND is_published=1", (quiz_id,)).fetchone()
        if not q: return None, []
        qs = c.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY id", (quiz_id,)).fetchall()
        return dict(q), [dict(x) for x in qs]

def list_quizzes(limit=20):
    with db() as c:
        return [dict(x) for x in c.execute(
            "SELECT q.*, (SELECT COUNT(*) FROM questions x WHERE x.quiz_id=q.id) question_count "
            "FROM quizzes q WHERE q.is_published=1 ORDER BY q.id DESC LIMIT ?", (limit,))]

def save_attempt(quiz_id, user_id, score, total):
    with db() as c:
        c.execute("INSERT INTO attempts(quiz_id,user_id,score,total) VALUES(?,?,?,?)",
                  (quiz_id,user_id,score,total))

def dashboard_stats():
    with db() as c:
        return {
            "users": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "quizzes": c.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0],
            "questions": c.execute("SELECT COUNT(*) FROM questions").fetchone()[0],
            "attempts": c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
        }

def dashboard_users(limit=100):
    with db() as c:
        return [dict(x) for x in c.execute(
            "SELECT id,username,first_name,is_blocked,created_at FROM users ORDER BY created_at DESC LIMIT ?", (limit,))]

def dashboard_quizzes(limit=100):
    with db() as c:
        return [dict(x) for x in c.execute(
            "SELECT q.id,q.title,q.description,q.is_published,q.created_at,"
            "(SELECT COUNT(*) FROM questions x WHERE x.quiz_id=q.id) question_count "
            "FROM quizzes q ORDER BY q.id DESC LIMIT ?", (limit,))]

def add_material(title, filename, uploaded_by=None):
    with db() as c:
        c.execute("INSERT INTO materials(title,filename,uploaded_by) VALUES(?,?,?)",
                  (title,filename,uploaded_by))

def list_materials(limit=50):
    with db() as c:
        return [dict(x) for x in c.execute("SELECT * FROM materials ORDER BY id DESC LIMIT ?", (limit,))]


def dashboard_quiz_questions(quiz_id):
    with db() as c:
        rows = c.execute("SELECT * FROM questions WHERE quiz_id=? ORDER BY id", (quiz_id,)).fetchall()
        return [dict(row) for row in rows]


def get_setting(key, default=None):
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default


def set_setting(key, value):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def leaderboard(limit=10):
    with db() as c:
        rows = c.execute("""SELECT u.id, COALESCE(u.first_name,'') first_name,
            COALESCE(u.username,'') username, SUM(a.score) score, SUM(a.total) total,
            COUNT(a.id) attempts
            FROM attempts a JOIN users u ON u.id=a.user_id
            GROUP BY u.id,u.first_name,u.username
            ORDER BY score DESC, (CAST(SUM(a.score) AS REAL)/NULLIF(SUM(a.total),0)) DESC, attempts DESC
            LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]
