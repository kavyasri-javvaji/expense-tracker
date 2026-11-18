# database.py - all SQLite code lives here
import sqlite3
import random
from datetime import date, timedelta

DB_NAME = "expenses.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row  # lets us read rows like dictionaries
    return conn


def init_db():
    # Create all tables the first time the app runs
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,        -- 'income' or 'expense'
            amount REAL NOT NULL,
            date TEXT NOT NULL,        -- stored as YYYY-MM-DD
            note TEXT,
            category TEXT DEFAULT 'Other',
            payment_method TEXT DEFAULT 'UPI'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS budgets (
            category TEXT PRIMARY KEY, -- one monthly budget per category
            amount REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS recurring (   -- templates like rent, EMI, subscriptions
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT DEFAULT 'Other',
            due_day INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goals (       -- savings goals
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            target REAL NOT NULL,
            saved REAL DEFAULT 0,
            deadline TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS category_rules (  -- categories learned from user corrections
            keyword TEXT PRIMARY KEY,
            category TEXT NOT NULL
        )
    """)
    # Upgrade an older database: add the new columns if they are missing
    for table, column, ddl in [("transactions", "payment_method", "TEXT DEFAULT 'UPI'"),
                               ("recurring", "due_day", "INTEGER DEFAULT 1")]:
        existing = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    conn.commit()
    conn.close()


def normalize(text):
    # lowercase and single spaces, so "Pizza  Hut" and "pizza hut" match
    return " ".join((text or "").lower().split())


# ---------- Transactions ----------
def add_transaction(t_type, amount, date, note, category, payment_method="UPI"):
    conn = get_connection()
    conn.execute(
        "INSERT INTO transactions (type, amount, date, note, category, payment_method) VALUES (?, ?, ?, ?, ?, ?)",
        (t_type, amount, date, note, category, payment_method),
    )  # the ? placeholders protect against SQL injection
    conn.commit()
    conn.close()


def get_transactions():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM transactions ORDER BY date DESC, id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]  # convert rows to plain dicts for JSON


def get_transaction(t_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM transactions WHERE id=?", (t_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def find_duplicate(t_type, amount, date, note):
    # True if a transaction with the same type, amount, date and note already exists
    conn = get_connection()
    count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE type=? AND amount=? AND date=? AND LOWER(IFNULL(note,''))=?",
        (t_type, amount, date, normalize(note))).fetchone()[0]
    conn.close()
    return count > 0


def update_transaction(t_id, t_type, amount, date, note, category, payment_method="UPI"):
    conn = get_connection()
    conn.execute(
        "UPDATE transactions SET type=?, amount=?, date=?, note=?, category=?, payment_method=? WHERE id=?",
        (t_type, amount, date, note, category, payment_method, t_id),
    )
    conn.commit()
    conn.close()


def delete_transaction(t_id):
    conn = get_connection()
    conn.execute("DELETE FROM transactions WHERE id=?", (t_id,))
    conn.commit()
    conn.close()


# ---------- Learned category rules ----------
def learn_rule(note, category):
    # Remember the user's correction: this note text -> this category
    key = normalize(note)
    if key:
        conn = get_connection()
        conn.execute("INSERT OR REPLACE INTO category_rules (keyword, category) VALUES (?, ?)", (key, category))
        conn.commit()
        conn.close()


def find_rule(note):
    # Look for a learned rule inside the note. The longest matching rule wins.
    text = normalize(note)
    if not text:
        return None
    conn = get_connection()
    rules = conn.execute("SELECT keyword, category FROM category_rules").fetchall()
    conn.close()
    matches = [r for r in rules if r["keyword"] in text]
    return max(matches, key=lambda r: len(r["keyword"]))["category"] if matches else None


# ---------- Budgets ----------
def set_budget(category, amount):
    # INSERT OR REPLACE = add the budget, or overwrite it if it already exists
    conn = get_connection()
    conn.execute("INSERT OR REPLACE INTO budgets (category, amount) VALUES (?, ?)", (category, amount))
    conn.commit()
    conn.close()


def get_budgets():
    conn = get_connection()
    rows = conn.execute("SELECT category, amount FROM budgets").fetchall()
    conn.close()
    return {r["category"]: r["amount"] for r in rows}  # {"Food": 4000, ...}


# ---------- Recurring templates ----------
def add_recurring(name, t_type, amount, category, due_day=1):
    conn = get_connection()
    conn.execute("INSERT INTO recurring (name, type, amount, category, due_day) VALUES (?, ?, ?, ?, ?)",
                 (name, t_type, amount, category, due_day))
    conn.commit()
    conn.close()


def get_recurring():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM recurring ORDER BY due_day, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_recurring(r_id):
    conn = get_connection()
    conn.execute("DELETE FROM recurring WHERE id=?", (r_id,))
    conn.commit()
    conn.close()


# ---------- Savings goals ----------
def add_goal(name, target, deadline, saved=0):
    conn = get_connection()
    conn.execute("INSERT INTO goals (name, target, saved, deadline) VALUES (?, ?, ?, ?)",
                 (name, target, saved, deadline))
    conn.commit()
    conn.close()


def get_goals():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM goals ORDER BY id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_to_goal(g_id, amount):
    conn = get_connection()
    conn.execute("UPDATE goals SET saved = saved + ? WHERE id=?", (amount, g_id))
    conn.commit()
    conn.close()


def delete_goal(g_id):
    conn = get_connection()
    conn.execute("DELETE FROM goals WHERE id=?", (g_id,))
    conn.commit()
    conn.close()


# ---------- Demo helpers ----------
def clear_all():
    conn = get_connection()
    for table in ("transactions", "budgets", "recurring", "goals", "category_rules"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


def load_sample_data():
    # Fills 3 full months + the current month so charts and the forecast look good in the demo
    clear_all()
    random.seed(42)  # same data every time
    pay_rng = random.Random(7)  # separate generator, so payment methods do not change the amounts
    today = date.today()

    def pay():
        return pay_rng.choices(["UPI", "Cash", "Card", "Net Banking"], weights=[60, 20, 15, 5])[0]

    # (note, category, min amount, max amount, how many times per month)
    expense_templates = [
        ("Lunch at canteen", "Food", 80, 160, 8),
        ("Pizza with friends", "Food", 250, 450, 2),
        ("Tea and snacks", "Food", 30, 90, 6),
        ("Auto to college", "Transport", 40, 120, 8),
        ("Cab ride", "Transport", 60, 120, 2),
        ("New shirt", "Shopping", 600, 1500, 1),
        ("Amazon order", "Shopping", 300, 1800, 2),
        ("Mobile recharge", "Bills", 299, 299, 1),
        ("Wifi bill", "Bills", 600, 600, 1),
        ("Pharmacy", "Health", 100, 400, 1),
        ("Movie tickets", "Entertainment", 200, 400, 2),
        ("Netflix", "Entertainment", 199, 199, 1),
        ("Gift for friend", "Other", 200, 600, 1),
    ]

    for months_back in (3, 2, 1, 0):
        year, month = today.year, today.month - months_back
        while month <= 0:
            month += 12
            year -= 1
        last_day = today.day if months_back == 0 else 28  # current month: only up to today

        add_transaction("income", 25000, date(year, month, 1).isoformat(), "Monthly stipend", "Other", "Net Banking")
        add_transaction("income", random.choice([1500, 2500, 3000]), date(year, month, min(5, last_day)).isoformat(),
                        "Freelance work", "Other", "UPI")
        for note, cat, low, high, times in expense_templates:
            for _ in range(times):
                d = date(year, month, random.randint(1, last_day))
                add_transaction("expense", random.randint(low, high), d.isoformat(), note, cat, pay())

        # A few unusual purchases, so the unusual-spending alert has something to show
        if months_back == 2:
            add_transaction("expense", 3200, date(year, month, 14).isoformat(), "Concert tickets", "Entertainment", "Card")
        if months_back == 1:
            add_transaction("expense", 6500, date(year, month, 12).isoformat(), "Smartwatch", "Shopping", "Card")
        if months_back == 0:
            add_transaction("expense", 2400, date(year, month, min(3, last_day)).isoformat(),
                            "Birthday party dinner", "Food", "Card")

    for cat, amt in {"Food": 4000, "Transport": 1500, "Shopping": 2500, "Bills": 1500,
                     "Health": 800, "Entertainment": 800, "Other": 1000}.items():
        set_budget(cat, amt)

    # (name, type, amount, category, due day of the month)
    for name, t_type, amt, cat, due in [("Gym membership", "expense", 500, "Health", 1),
                                        ("Education loan EMI", "expense", 2000, "Bills", 3),
                                        ("Netflix subscription", "expense", 199, "Entertainment", 5),
                                        ("Mobile recharge", "expense", 299, "Bills", 10)]:
        add_recurring(name, t_type, amt, cat, due)

    add_goal("New laptop", 60000, (today + timedelta(days=180)).isoformat(), saved=18000)