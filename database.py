
import os
import sqlite3
from pathlib import Path

DATABASE_PATH = Path(os.environ.get("STOCKALERT_DATABASE", Path(__file__).parent / "stockalert.db"))


def get_connection():
    """Create a connection to the StockAlert SQLite database."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    """Create all StockAlert database tables if they do not exist."""
    with get_connection() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sku TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT 'General',
                quantity INTEGER NOT NULL DEFAULT 0
                    CHECK(quantity >= 0),
                min_stock INTEGER NOT NULL DEFAULT 5
                    CHECK(min_stock >= 0),
                selling_price REAL NOT NULL DEFAULT 0
                    CHECK(selling_price >= 0),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                movement_type TEXT NOT NULL
                    CHECK(movement_type IN ('IN', 'OUT')),
                quantity INTEGER NOT NULL CHECK(quantity > 0),
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(product_id) REFERENCES products(id)
                    ON DELETE RESTRICT
            );

            CREATE TABLE IF NOT EXISTS expiry_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER NOT NULL,
                batch_number TEXT NOT NULL,
                quantity INTEGER NOT NULL CHECK(quantity >= 0),
                expiry_date TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(product_id) REFERENCES products(id)
                    ON DELETE CASCADE,
                UNIQUE(product_id, batch_number)
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL
                    CHECK(role IN ('admin', 'staff')),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                attendance_date TEXT NOT NULL,
                attendance_status TEXT NOT NULL DEFAULT 'Present',
                check_in_time TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(user_id, attendance_date)
            );
        """)

        # Add staff profile fields to existing databases without rebuilding users.
        user_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "work_location" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN work_location TEXT NOT NULL DEFAULT ''"
            )
        if "staff_status" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN staff_status TEXT NOT NULL DEFAULT 'Active' "
                "CHECK(staff_status IN ('Active', 'On Leave', 'Inactive'))"
            )
        if "age" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN age INTEGER "
                "CHECK(age IS NULL OR age BETWEEN 18 AND 100)"
            )
        if "contact_number" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN contact_number TEXT"
            )


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at: {DATABASE_PATH}")
    print("Products, stock movements, expiry batches, and users tables are ready.")
