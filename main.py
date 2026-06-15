# ============================================================
# Expense Tracker MCP Server
# Built with FastMCP — exposes tools for Claude to add,
# list, and summarize expenses stored in a local SQLite DB.
# Designed for remote deployment (Docker/cloud containers).
# ============================================================

from fastmcp import FastMCP
import os
import aiosqlite  # Async SQLite driver — required for async MCP tools
import json


# ------------------------------------------------------------
# DATABASE PATH SETUP
# In Docker/cloud containers, most directories are read-only.
# This function tries multiple locations and picks the first
# one it can actually write to, so the server never silently
# fails with a "readonly database" error.
# ------------------------------------------------------------
def get_writable_db_path():
    candidates = [
        "/tmp/expenses.db",                                     # Best option in Linux/Docker — always writable
        os.path.join(os.path.expanduser("~"), "expenses.db"),  # Home directory fallback
        os.path.join(os.getcwd(), "expenses.db"),              # Current working directory fallback
    ]

    for path in candidates:
        try:
            dir_path = os.path.dirname(path)
            os.makedirs(dir_path, exist_ok=True)  # Create directory if it doesn't exist

            # Actually test write access by creating and deleting a temp file
            # just checking permissions is not enough — some cloud envs lie
            test_file = path + ".writetest"
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)

            print(f"Using database path: {path}")
            return path
        except (OSError, PermissionError) as e:
            # This path is not writable — try the next one
            print(f"Path not writable: {path} ({e})")
            continue

    # If no path is writable, crash early with a clear message
    raise RuntimeError("No writable path found for database!")


# Resolve DB and categories paths once at startup
DB_PATH = get_writable_db_path()
CATEGORIES_PATH = os.path.join(os.path.dirname(__file__), "categories.json")

# ------------------------------------------------------------
# MCP SERVER INSTANCE
# "ExpenseTracker" is the name Claude sees when connecting
# ------------------------------------------------------------
mcp = FastMCP("ExpenseTracker")


# ------------------------------------------------------------
# DATABASE INITIALIZATION
# Runs once at startup using synchronous sqlite3 (not async)
# because module-level code can't be async in Python.
# Creates the expenses table if it doesn't already exist,
# and verifies write access with a test insert + delete.
# ------------------------------------------------------------
def init_db():
    try:
        import sqlite3
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        with sqlite3.connect(DB_PATH) as c:
            # WAL mode allows concurrent reads while writing — safer in cloud environments
            c.execute("PRAGMA journal_mode=WAL")

            # Create expenses table — id is auto-assigned, subcategory and note are optional
            c.execute("""
                CREATE TABLE IF NOT EXISTS expenses(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    amount REAL NOT NULL,
                    category TEXT NOT NULL,
                    subcategory TEXT DEFAULT '',
                    note TEXT DEFAULT ''
                )
            """)

            # Verify write access with a dummy row — if this fails, DB is read-only
            c.execute("INSERT OR IGNORE INTO expenses(date, amount, category) VALUES ('2000-01-01', 0, 'test')")
            c.execute("DELETE FROM expenses WHERE category = 'test'")
            c.commit()

            print(f"Database initialized successfully at {DB_PATH}")
    except Exception as e:
        print(f"Database initialization error: {e}")
        raise  # Re-raise so the server doesn't start in a broken state


# Run DB init before any tool is called
init_db()


# ------------------------------------------------------------
# TOOL: add_expense
# Inserts a new expense row into the database.
# Claude calls this when the user says something like
# "Add ₹500 for groceries on 2026-06-15"
# ------------------------------------------------------------
@mcp.tool()
async def add_expense(date: str, amount: float, category: str, subcategory: str = "", note: str = ""):
    """Add a new expense entry to the database."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            # Re-apply WAL mode for each async connection (doesn't persist across connections)
            await c.execute("PRAGMA journal_mode=WAL")

            cur = await c.execute(
                "INSERT INTO expenses(date, amount, category, subcategory, note) VALUES (?,?,?,?,?)",
                (date, amount, category, subcategory, note)
            )
            expense_id = cur.lastrowid  # Get the auto-generated ID of the new row
            await c.commit()

            return {"status": "success", "id": expense_id, "message": "Expense added successfully"}
    except Exception as e:
        return {"status": "error", "message": f"Database error: {str(e)}"}


# ------------------------------------------------------------
# TOOL: list_expenses
# Fetches all expenses between two dates (inclusive).
# Returns a list of dicts — one per expense row.
# Claude calls this when the user asks to see their expenses.
# ------------------------------------------------------------
@mcp.tool()
async def list_expenses(start_date: str, end_date: str):
    """List expense entries within an inclusive date range."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            cur = await c.execute(
                """
                SELECT id, date, amount, category, subcategory, note
                FROM expenses
                WHERE date BETWEEN ? AND ?
                ORDER BY date DESC, id DESC
                """,
                (start_date, end_date)
            )
            # Build list of dicts using column names from cursor description
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]
    except Exception as e:
        return {"status": "error", "message": f"Error listing expenses: {str(e)}"}


# ------------------------------------------------------------
# TOOL: summarize
# Groups expenses by category and returns totals.
# Optionally filter by a specific category.
# Claude calls this when the user asks "how much did I spend
# on food this month?" or "summarize my June expenses"
# ------------------------------------------------------------
@mcp.tool()
async def summarize(start_date: str, end_date: str, category: str = None):
    """Summarize expenses by category within an inclusive date range."""
    try:
        async with aiosqlite.connect(DB_PATH) as c:
            # Base query — always filter by date range
            query = """
                SELECT category, SUM(amount) AS total_amount, COUNT(*) as count
                FROM expenses
                WHERE date BETWEEN ? AND ?
            """
            params = [start_date, end_date]

            # Optionally narrow down to a single category
            if category:
                query += " AND category = ?"
                params.append(category)

            # Sort by highest spend first
            query += " GROUP BY category ORDER BY total_amount DESC"

            cur = await c.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]
    except Exception as e:
        return {"status": "error", "message": f"Error summarizing expenses: {str(e)}"}


# ------------------------------------------------------------
# RESOURCE: categories
# Exposes the list of valid expense categories to Claude.
# Reads from categories.json if it exists, otherwise returns
# a hardcoded default list so the server always works even
# without the file present.
# ------------------------------------------------------------
@mcp.resource("expense:///categories", mime_type="application/json")
def categories():
    """Return available expense categories."""

    # Fallback categories used when categories.json is missing
    default_categories = {
        "categories": [
            "Food & Dining",
            "Transportation",
            "Shopping",
            "Entertainment",
            "Bills & Utilities",
            "Healthcare",
            "Travel",
            "Education",
            "Business",
            "Other"
        ]
    }

    try:
        # Try to load custom categories from the JSON file first
        with open(CATEGORIES_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        # File doesn't exist — use defaults silently
        return json.dumps(default_categories, indent=2)
    except Exception as e:
        # Any other error — return a valid JSON error message
        return json.dumps({"error": f"Could not load categories: {str(e)}"})


# ------------------------------------------------------------
# SERVER ENTRYPOINT
# Starts the HTTP server when run directly (python main.py).
# host="0.0.0.0" makes it accessible from outside the container.
# port=8000 is the standard port FastMCP cloud expects.
# ------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000)