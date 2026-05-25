from fastmcp import FastMCP
import os
import sqlite3
import json
from datetime import datetime, date
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

mcp = FastMCP("ExpenseTracker Pro")

# -------------------
# DB INIT
# -------------------
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        # Expenses table
        c.execute("""
            CREATE TABLE IF NOT EXISTS expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT '',
                payment_mode TEXT DEFAULT 'cash',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Credits table
        c.execute("""
            CREATE TABLE IF NOT EXISTS credits(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                amount REAL NOT NULL,
                source TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Budgets table (NEW)
        c.execute("""
            CREATE TABLE IF NOT EXISTS budgets(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL UNIQUE,
                monthly_limit REAL NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        # Recurring expenses table (NEW)
        c.execute("""
            CREATE TABLE IF NOT EXISTS recurring(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                subcategory TEXT DEFAULT '',
                note TEXT DEFAULT '',
                frequency TEXT NOT NULL,
                next_due TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
        """)

init_db()


# -------------------
# EXPENSE TOOLS
# -------------------

@mcp.tool()
def add_expense(
    date: str,
    amount: float,
    category: str,
    subcategory: str = "",
    note: str = "",
    payment_mode: str = "cash"
) -> dict:
    """
    Add a new expense entry.
    payment_mode: cash | upi | card | netbanking
    date format: YYYY-MM-DD
    """
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """INSERT INTO expenses
               (date, amount, category, subcategory, note, payment_mode)
               VALUES (?,?,?,?,?,?)""",
            (date, amount, category, subcategory, note, payment_mode)
        )
        return {"status": "ok", "id": cur.lastrowid}


@mcp.tool()
def list_expenses(
    start_date: str,
    end_date: str,
    category: str = None,
    payment_mode: str = None,
    limit: int = 50
) -> list:
    """
    List expenses within date range.
    Optionally filter by category or payment_mode.
    """
    with sqlite3.connect(DB_PATH) as c:
        query = """
            SELECT id, date, amount, category, subcategory,
                   note, payment_mode, created_at
            FROM expenses
            WHERE date BETWEEN ? AND ?
        """
        params = [start_date, end_date]

        if category:
            query += " AND category = ?"
            params.append(category)

        if payment_mode:
            query += " AND payment_mode = ?"
            params.append(payment_mode)

        query += " ORDER BY date DESC, id DESC LIMIT ?"
        params.append(limit)

        cur = c.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@mcp.tool()
def update_expense(
    expense_id: int,
    date: str = None,
    amount: float = None,
    category: str = None,
    subcategory: str = None,
    note: str = None,
    payment_mode: str = None
) -> dict:
    """Update an existing expense entry by ID."""
    updates, values = [], []

    for field, val in [
        ("date", date), ("amount", amount),
        ("category", category), ("subcategory", subcategory),
        ("note", note), ("payment_mode", payment_mode)
    ]:
        if val is not None:
            updates.append(f"{field} = ?")
            values.append(val)

    if not updates:
        return {"status": "error", "message": "No fields provided"}

    values.append(expense_id)
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            f"UPDATE expenses SET {', '.join(updates)} WHERE id = ?",
            values
        )
        if cur.rowcount == 0:
            return {"status": "error", "message": "Expense not found"}
        return {"status": "ok", "updated_id": expense_id}


@mcp.tool()
def delete_expense(expense_id: int) -> dict:
    """Delete an expense entry by ID."""
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "DELETE FROM expenses WHERE id = ?", (expense_id,)
        )
        if cur.rowcount == 0:
            return {"status": "error", "message": "Expense not found"}
        return {"status": "ok", "deleted_id": expense_id}


# -------------------
# SUMMARY & ANALYTICS (NEW)
# -------------------

@mcp.tool()
def summarize(
    start_date: str,
    end_date: str,
    category: str = None
) -> list:
    """Summarize total expenses by category within date range."""
    with sqlite3.connect(DB_PATH) as c:
        query = """
            SELECT category,
                   COUNT(*) as num_transactions,
                   SUM(amount) as total_amount,
                   AVG(amount) as avg_amount,
                   MIN(amount) as min_amount,
                   MAX(amount) as max_amount
            FROM expenses
            WHERE date BETWEEN ? AND ?
        """
        params = [start_date, end_date]

        if category:
            query += " AND category = ?"
            params.append(category)

        query += " GROUP BY category ORDER BY total_amount DESC"
        cur = c.execute(query, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@mcp.tool()
def monthly_summary(year: int, month: int) -> dict:
    """
    Complete monthly financial summary —
    total expenses, total credits, net savings,
    breakdown by category, and top 5 expenses.
    """
    start = f"{year}-{month:02d}-01"
    # Last day of month
    if month == 12:
        end = f"{year}-12-31"
    else:
        end = f"{year}-{month+1:02d}-01"

    with sqlite3.connect(DB_PATH) as c:
        # Total expenses
        exp = c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE date >= ? AND date < ?",
            (start, end)
        ).fetchone()[0]

        # Total credits
        cred = c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM credits "
            "WHERE date >= ? AND date < ?",
            (start, end)
        ).fetchone()[0]

        # By category
        cats = c.execute(
            """SELECT category, SUM(amount) as total
               FROM expenses WHERE date >= ? AND date < ?
               GROUP BY category ORDER BY total DESC""",
            (start, end)
        ).fetchall()

        # Top 5 expenses
        top5 = c.execute(
            """SELECT date, amount, category, note
               FROM expenses WHERE date >= ? AND date < ?
               ORDER BY amount DESC LIMIT 5""",
            (start, end)
        ).fetchall()

        # Payment mode breakdown
        modes = c.execute(
            """SELECT payment_mode, COUNT(*) as count,
               SUM(amount) as total
               FROM expenses WHERE date >= ? AND date < ?
               GROUP BY payment_mode""",
            (start, end)
        ).fetchall()

    return {
        "month": f"{year}-{month:02d}",
        "total_expenses": round(exp, 2),
        "total_credits": round(cred, 2),
        "net_savings": round(cred - exp, 2),
        "savings_rate": f"{((cred-exp)/cred*100):.1f}%" if cred > 0 else "N/A",
        "by_category": [{"category": r[0], "total": round(r[1], 2)} for r in cats],
        "top_5_expenses": [
            {"date": r[0], "amount": r[1], "category": r[2], "note": r[3]}
            for r in top5
        ],
        "payment_modes": [
            {"mode": r[0], "count": r[1], "total": round(r[2], 2)}
            for r in modes
        ]
    }


@mcp.tool()
def spending_trend(
    months: int = 3
) -> list:
    """
    Show month-by-month spending trend
    for the last N months.
    """
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """
            SELECT strftime('%Y-%m', date) as month,
                   COUNT(*) as transactions,
                   SUM(amount) as total_spent
            FROM expenses
            GROUP BY month
            ORDER BY month DESC
            LIMIT ?
            """,
            (months,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@mcp.tool()
def search_expenses(keyword: str, limit: int = 20) -> list:
    """
    Search expenses by keyword in note,
    category, or subcategory.
    """
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """
            SELECT id, date, amount, category,
                   subcategory, note, payment_mode
            FROM expenses
            WHERE note LIKE ? OR category LIKE ?
               OR subcategory LIKE ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (f"%{keyword}%", f"%{keyword}%",
             f"%{keyword}%", limit)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# -------------------
# BUDGET TOOLS (NEW)
# -------------------

@mcp.tool()
def set_budget(category: str, monthly_limit: float) -> dict:
    """Set or update a monthly budget limit for a category."""
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            """INSERT INTO budgets(category, monthly_limit)
               VALUES (?, ?)
               ON CONFLICT(category)
               DO UPDATE SET monthly_limit = excluded.monthly_limit""",
            (category, monthly_limit)
        )
        return {
            "status": "ok",
            "category": category,
            "monthly_limit": monthly_limit
        }


@mcp.tool()
def check_budget(year: int, month: int) -> list:
    """
    Check budget vs actual spending for
    current month across all categories.
    Shows remaining budget and % used.
    """
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month+1:02d}-01" if month < 12 else f"{year}-12-31"

    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """
            SELECT b.category,
                   b.monthly_limit,
                   COALESCE(SUM(e.amount), 0) as spent
            FROM budgets b
            LEFT JOIN expenses e
                ON e.category = b.category
                AND e.date >= ? AND e.date < ?
            GROUP BY b.category
            ORDER BY b.category
            """,
            (start, end)
        )
        results = []
        for row in cur.fetchall():
            cat, limit, spent = row
            remaining = limit - spent
            pct = (spent / limit * 100) if limit > 0 else 0
            status = "over budget" if spent > limit else \
                     "warning" if pct > 80 else "ok"
            results.append({
                "category": cat,
                "monthly_limit": round(limit, 2),
                "spent": round(spent, 2),
                "remaining": round(remaining, 2),
                "percent_used": f"{pct:.1f}%",
                "status": status
            })
        return results


# -------------------
# CREDIT TOOLS
# -------------------

@mcp.tool()
def add_credit(
    date: str,
    amount: float,
    source: str,
    note: str = ""
) -> dict:
    """Add a credit/income entry."""
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT INTO credits(date, amount, source, note) VALUES (?,?,?,?)",
            (date, amount, source, note)
        )
        return {"status": "ok", "credit_id": cur.lastrowid}


@mcp.tool()
def list_credits(start_date: str, end_date: str) -> list:
    """List all credit/income entries within date range."""
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """SELECT id, date, amount, source, note
               FROM credits WHERE date BETWEEN ? AND ?
               ORDER BY date DESC""",
            (start_date, end_date)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# -------------------
# RECURRING TOOLS (NEW)
# -------------------

@mcp.tool()
def add_recurring(
    amount: float,
    category: str,
    frequency: str,
    next_due: str,
    subcategory: str = "",
    note: str = ""
) -> dict:
    """
    Add a recurring expense.
    frequency: daily | weekly | monthly | yearly
    next_due format: YYYY-MM-DD
    """
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """INSERT INTO recurring
               (amount, category, subcategory, note, frequency, next_due)
               VALUES (?,?,?,?,?,?)""",
            (amount, category, subcategory, note, frequency, next_due)
        )
        return {"status": "ok", "recurring_id": cur.lastrowid}


@mcp.tool()
def list_recurring(active_only: bool = True) -> list:
    """List all recurring expenses."""
    with sqlite3.connect(DB_PATH) as c:
        query = "SELECT * FROM recurring"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY next_due ASC"
        cur = c.execute(query)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@mcp.tool()
def due_reminders() -> list:
    """
    Get recurring expenses due today or overdue.
    Use this to log reminders each morning.
    """
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """SELECT id, amount, category, note,
                      frequency, next_due
               FROM recurring
               WHERE next_due <= ? AND active = 1
               ORDER BY next_due ASC""",
            (today,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# -------------------
# UTILITY TOOLS (NEW)
# -------------------

@mcp.tool()
def net_worth_snapshot(
    start_date: str,
    end_date: str
) -> dict:
    """
    Quick financial snapshot —
    total income vs total expenses
    vs net position for any period.
    """
    with sqlite3.connect(DB_PATH) as c:
        total_exp = c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE date BETWEEN ? AND ?",
            (start_date, end_date)
        ).fetchone()[0]

        total_cred = c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM credits "
            "WHERE date BETWEEN ? AND ?",
            (start_date, end_date)
        ).fetchone()[0]

    return {
        "period": f"{start_date} to {end_date}",
        "total_income": round(total_cred, 2),
        "total_expenses": round(total_exp, 2),
        "net": round(total_cred - total_exp, 2),
        "status": "surplus" if total_cred >= total_exp else "deficit"
    }


@mcp.tool()
def top_spending_categories(
    start_date: str,
    end_date: str,
    top_n: int = 5
) -> list:
    """Get top N spending categories by total amount."""
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            """SELECT category,
                      SUM(amount) as total,
                      COUNT(*) as transactions
               FROM expenses
               WHERE date BETWEEN ? AND ?
               GROUP BY category
               ORDER BY total DESC
               LIMIT ?""",
            (start_date, end_date, top_n)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


@mcp.tool()
def daily_average(
    start_date: str,
    end_date: str
) -> dict:
    """Calculate average daily spending in a period."""
    with sqlite3.connect(DB_PATH) as c:
        total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses "
            "WHERE date BETWEEN ? AND ?",
            (start_date, end_date)
        ).fetchone()[0]

        days = c.execute(
            "SELECT COUNT(DISTINCT date) FROM expenses "
            "WHERE date BETWEEN ? AND ?",
            (start_date, end_date)
        ).fetchone()[0]

    avg = total / days if days > 0 else 0
    return {
        "period": f"{start_date} to {end_date}",
        "total_spent": round(total, 2),
        "active_days": days,
        "daily_average": round(avg, 2)
    }


# -------------------
# RESOURCES
# -------------------

@mcp.resource("expense://categories", mime_type="application/json")
def categories():
    """Read expense categories from JSON file."""
    with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
        return f.read()


@mcp.resource("expense://summary/today", mime_type="application/json")
def today_summary():
    """Quick summary of today's expenses."""
    today = date.today().isoformat()
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute(
            """SELECT category, SUM(amount) as total
               FROM expenses WHERE date = ?
               GROUP BY category""",
            (today,)
        ).fetchall()
        total = c.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE date = ?",
            (today,)
        ).fetchone()[0]

    return json.dumps({
        "date": today,
        "total_spent_today": round(total, 2),
        "by_category": [{"category": r[0], "total": round(r[1], 2)}
                        for r in rows]
    })

# Start the server
if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)
    # mcp.run()