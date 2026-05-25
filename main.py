from fastmcp import FastMCP
import os
import aiosqlite
import json
from datetime import datetime, date
from typing import Optional

# ── For remote deployment use persistent path
# ── For local development use file directory
DB_PATH = os.environ.get(
    "DB_PATH",
    os.path.join(os.path.dirname(__file__), "expenses.db")
)
CATEGORIES_PATH = os.path.join(
    os.path.dirname(__file__), "categories.json"
)

print(f"Database path: {DB_PATH}")

mcp = FastMCP("ExpenseTracker Pro")

# -------------------
# DB INIT (sync — runs once at startup)
# -------------------
def init_db():
    try:
        import sqlite3
        with sqlite3.connect(DB_PATH) as c:
            c.execute("PRAGMA journal_mode=WAL")
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
            c.execute("""
                CREATE TABLE IF NOT EXISTS budgets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    category TEXT NOT NULL UNIQUE,
                    monthly_limit REAL NOT NULL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
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
            print("Database initialized successfully")
    except Exception as e:
        print(f"DB init error: {e}")
        raise

init_db()


# -------------------
# EXPENSE TOOLS (async)
# -------------------

@mcp.tool()
async def add_expense(
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
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """INSERT INTO expenses
                   (date, amount, category, subcategory,
                    note, payment_mode)
                   VALUES (?,?,?,?,?,?)""",
                (date, amount, category, subcategory,
                 note, payment_mode)
            )
            expense_id = cur.lastrowid
            await c.commit()
            return {
                "status": "success",
                "id": expense_id,
                "message": f"Added ₹{amount} for {category}"
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def list_expenses(
    start_date: str,
    end_date: str,
    category: str = None,
    payment_mode: str = None,
    limit: int = 50
) -> list:
    """
    List expenses within date range.
    Filter by category or payment_mode optionally.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            query = """
                SELECT id, date, amount, category,
                       subcategory, note, payment_mode
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
            query += " ORDER BY date DESC LIMIT ?"
            params.append(limit)

            cur = await c.execute(query, params)
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def update_expense(
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
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                f"UPDATE expenses SET {', '.join(updates)} WHERE id = ?",
                values
            )
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": "Expense not found"}
            return {"status": "success", "updated_id": expense_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def delete_expense(expense_id: int) -> dict:
    """Delete an expense entry by ID."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                "DELETE FROM expenses WHERE id = ?",
                (expense_id,)
            )
            await c.commit()
            if cur.rowcount == 0:
                return {"status": "error", "message": "Not found"}
            return {"status": "success", "deleted_id": expense_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def search_expenses(
    keyword: str,
    limit: int = 20
) -> list:
    """Search expenses by keyword in note, category, subcategory."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """SELECT id, date, amount, category,
                          subcategory, note, payment_mode
                   FROM expenses
                   WHERE note LIKE ? OR category LIKE ?
                      OR subcategory LIKE ?
                   ORDER BY date DESC LIMIT ?""",
                (f"%{keyword}%", f"%{keyword}%",
                 f"%{keyword}%", limit)
            )
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -------------------
# SUMMARY & ANALYTICS
# -------------------

@mcp.tool()
async def summarize(
    start_date: str,
    end_date: str,
    category: str = None
) -> list:
    """Summarize expenses by category with stats."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            query = """
                SELECT category,
                       COUNT(*) as transactions,
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

            cur = await c.execute(query, params)
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def monthly_summary(year: int, month: int) -> dict:
    """
    Complete monthly P&L — expenses, income,
    net savings, savings rate, top 5 expenses,
    category breakdown, payment mode breakdown.
    """
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month+1:02d}-01" if month < 12 \
          else f"{year}-12-31"
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            exp_row = await (await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses "
                "WHERE date >= ? AND date < ?",
                (start, end)
            )).fetchone()
            exp = exp_row[0]

            cred_row = await (await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM credits "
                "WHERE date >= ? AND date < ?",
                (start, end)
            )).fetchone()
            cred = cred_row[0]

            cats = await (await c.execute(
                """SELECT category, SUM(amount) as total
                   FROM expenses WHERE date >= ? AND date < ?
                   GROUP BY category ORDER BY total DESC""",
                (start, end)
            )).fetchall()

            top5 = await (await c.execute(
                """SELECT date, amount, category, note
                   FROM expenses WHERE date >= ? AND date < ?
                   ORDER BY amount DESC LIMIT 5""",
                (start, end)
            )).fetchall()

            modes = await (await c.execute(
                """SELECT payment_mode, COUNT(*) as count,
                   SUM(amount) as total
                   FROM expenses WHERE date >= ? AND date < ?
                   GROUP BY payment_mode""",
                (start, end)
            )).fetchall()

        return {
            "month": f"{year}-{month:02d}",
            "total_expenses": round(exp, 2),
            "total_income": round(cred, 2),
            "net_savings": round(cred - exp, 2),
            "savings_rate": f"{((cred-exp)/cred*100):.1f}%"
                            if cred > 0 else "N/A",
            "by_category": [
                {"category": r[0], "total": round(r[1], 2)}
                for r in cats
            ],
            "top_5_expenses": [
                {"date": r[0], "amount": r[1],
                 "category": r[2], "note": r[3]}
                for r in top5
            ],
            "payment_modes": [
                {"mode": r[0], "count": r[1],
                 "total": round(r[2], 2)}
                for r in modes
            ]
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def spending_trend(months: int = 6) -> list:
    """Month-by-month spending trend for last N months."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """SELECT strftime('%Y-%m', date) as month,
                          COUNT(*) as transactions,
                          SUM(amount) as total_spent
                   FROM expenses
                   GROUP BY month
                   ORDER BY month DESC
                   LIMIT ?""",
                (months,)
            )
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def top_spending_categories(
    start_date: str,
    end_date: str,
    top_n: int = 5
) -> list:
    """Get top N spending categories by total amount."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
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
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def daily_average(
    start_date: str,
    end_date: str
) -> dict:
    """Average daily spending in a period."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            total = (await (await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses "
                "WHERE date BETWEEN ? AND ?",
                (start_date, end_date)
            )).fetchone())[0]

            days = (await (await c.execute(
                "SELECT COUNT(DISTINCT date) FROM expenses "
                "WHERE date BETWEEN ? AND ?",
                (start_date, end_date)
            )).fetchone())[0]

        avg = total / days if days > 0 else 0
        return {
            "period": f"{start_date} to {end_date}",
            "total_spent": round(total, 2),
            "active_days": days,
            "daily_average": round(avg, 2)
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def net_worth_snapshot(
    start_date: str,
    end_date: str
) -> dict:
    """Total income vs expenses = net position."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            exp = (await (await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses "
                "WHERE date BETWEEN ? AND ?",
                (start_date, end_date)
            )).fetchone())[0]

            cred = (await (await c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM credits "
                "WHERE date BETWEEN ? AND ?",
                (start_date, end_date)
            )).fetchone())[0]

        return {
            "period": f"{start_date} to {end_date}",
            "total_income": round(cred, 2),
            "total_expenses": round(exp, 2),
            "net": round(cred - exp, 2),
            "status": "surplus" if cred >= exp else "deficit"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -------------------
# CREDIT TOOLS
# -------------------

@mcp.tool()
async def add_credit(
    date: str,
    amount: float,
    source: str,
    note: str = ""
) -> dict:
    """Add a credit/income entry."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                "INSERT INTO credits(date,amount,source,note) "
                "VALUES (?,?,?,?)",
                (date, amount, source, note)
            )
            await c.commit()
            return {"status": "success", "credit_id": cur.lastrowid}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def list_credits(
    start_date: str,
    end_date: str
) -> list:
    """List all income entries within date range."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """SELECT id, date, amount, source, note
                   FROM credits WHERE date BETWEEN ? AND ?
                   ORDER BY date DESC""",
                (start_date, end_date)
            )
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -------------------
# BUDGET TOOLS
# -------------------

@mcp.tool()
async def set_budget(
    category: str,
    monthly_limit: float
) -> dict:
    """Set or update monthly budget for a category."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            await c.execute(
                """INSERT INTO budgets(category, monthly_limit)
                   VALUES (?,?)
                   ON CONFLICT(category)
                   DO UPDATE SET monthly_limit=excluded.monthly_limit""",
                (category, monthly_limit)
            )
            await c.commit()
            return {
                "status": "success",
                "category": category,
                "monthly_limit": monthly_limit
            }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def check_budget(year: int, month: int) -> list:
    """
    Budget vs actual for all categories.
    Shows remaining, % used, ok/warning/over status.
    """
    start = f"{year}-{month:02d}-01"
    end = f"{year}-{month+1:02d}-01" if month < 12 \
          else f"{year}-12-31"
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """SELECT b.category, b.monthly_limit,
                          COALESCE(SUM(e.amount), 0) as spent
                   FROM budgets b
                   LEFT JOIN expenses e
                       ON e.category = b.category
                       AND e.date >= ? AND e.date < ?
                   GROUP BY b.category""",
                (start, end)
            )
            results = []
            for row in await cur.fetchall():
                cat, limit, spent = row
                remaining = limit - spent
                pct = (spent/limit*100) if limit > 0 else 0
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
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -------------------
# RECURRING TOOLS
# -------------------

@mcp.tool()
async def add_recurring(
    amount: float,
    category: str,
    frequency: str,
    next_due: str,
    subcategory: str = "",
    note: str = ""
) -> dict:
    """
    Add recurring expense.
    frequency: daily | weekly | monthly | yearly
    next_due: YYYY-MM-DD
    """
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """INSERT INTO recurring
                   (amount,category,subcategory,note,
                    frequency,next_due)
                   VALUES (?,?,?,?,?,?)""",
                (amount, category, subcategory,
                 note, frequency, next_due)
            )
            await c.commit()
            return {"status": "success", "id": cur.lastrowid}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def list_recurring(active_only: bool = True) -> list:
    """List all recurring expenses."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            query = "SELECT * FROM recurring"
            if active_only:
                query += " WHERE active = 1"
            query += " ORDER BY next_due ASC"
            cur = await c.execute(query)
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


@mcp.tool()
async def due_reminders() -> list:
    """Recurring expenses due today or overdue."""
    today = date.today().isoformat()
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """SELECT id, amount, category, note,
                          frequency, next_due
                   FROM recurring
                   WHERE next_due <= ? AND active = 1
                   ORDER BY next_due ASC""",
                (today,)
            )
            cols = [d[0] for d in cur.description]
            rows = await cur.fetchall()
            return [dict(zip(cols, r)) for r in rows]
    except Exception as e:
        return {"status": "error", "message": str(e)}


# -------------------
# RESOURCES
# -------------------

@mcp.resource(
    "expense:///categories",
    mime_type="application/json"
)
def categories():
    """Live expense categories from JSON."""
    default = {
        "categories": [
            "Food & Dining", "Transportation",
            "Shopping", "Entertainment",
            "Bills & Utilities", "Healthcare",
            "Travel", "Education", "Business", "Other"
        ]
    }
    try:
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return json.dumps(default, indent=2)


@mcp.resource(
    "expense:///summary/today",
    mime_type="application/json"
)
def today_summary():
    """Live today's expense summary."""
    import sqlite3 as sqlite_sync
    today = date.today().isoformat()
    try:
        with sqlite_sync.connect(DB_PATH) as c:
            rows = c.execute(
                """SELECT category, SUM(amount) as total
                   FROM expenses WHERE date = ?
                   GROUP BY category""",
                (today,)
            ).fetchall()
            total = c.execute(
                "SELECT COALESCE(SUM(amount),0) FROM expenses "
                "WHERE date = ?",
                (today,)
            ).fetchone()[0]
        return json.dumps({
            "date": today,
            "total_spent_today": round(total, 2),
            "by_category": [
                {"category": r[0], "total": round(r[1], 2)}
                for r in rows
            ]
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


# -------------------
# RUN — LOCAL or REMOTE
# -------------------

if __name__ == "__main__":
    import sys

    # Pass "remote" as argument to run as HTTP server
    # python server.py remote → deploys on port 8000
    # python server.py        → runs locally via STDIO

    if len(sys.argv) > 1 and sys.argv[1] == "remote":
        print("Starting REMOTE MCP server on port 8000...")
        mcp.run(transport="streamable-http",
                host="0.0.0.0",
                port=8000)
    else:
        print("Starting LOCAL MCP server via STDIO...")
        mcp.run()