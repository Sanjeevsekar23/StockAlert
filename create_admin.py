
from getpass import getpass

from werkzeug.security import generate_password_hash

from database import get_connection, init_db


def create_admin():
    init_db()

    print("\n=== StockAlert: Create Admin Account ===")

    username = input("Enter admin username: ").strip()

    if not username:
        print("Error: Username cannot be empty.")
        return

    password = getpass("Enter admin password: ")
    confirm_password = getpass("Confirm admin password: ")

    if len(password) < 8:
        print("Error: Password must contain at least 8 characters.")
        return

    if password != confirm_password:
        print("Error: Passwords do not match.")
        return

    password_hash = generate_password_hash(password)

    try:
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if existing:
                print("Error: This username already exists.")
                return

            connection.execute(
                """
                INSERT INTO users (username, password_hash, role)
                VALUES (?, ?, 'admin')
                """,
                (username, password_hash)
            )

        print("\nAdmin account created successfully!")
        print(f"Username: {username}")
        print("Keep your password secure.")

    except Exception as error:
        print(f"Unable to create admin account: {error}")


if __name__ == "__main__":
    create_admin()
