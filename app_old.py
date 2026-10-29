
import os
import secrets
from functools import wraps
from datetime import date

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    abort
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import init_db, get_connection


app = Flask(__name__)
app.secret_key = os.environ.get(
    "STOCKALERT_SECRET_KEY",
    secrets.token_urlsafe(32)
)

init_db()


# ---------------- AUTHENTICATION HELPERS ----------------

def login_required(view):
    """Require a signed-in user."""
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please sign in to continue.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped_view


def role_required(required_role):
    """Restrict access to a specific role."""
    def decorator(view):
        @wraps(view)
        def wrapped_view(*args, **kwargs):
            if "user_id" not in session:
                flash("Please sign in to continue.", "warning")
                return redirect(url_for("login"))

            if session.get("role") != required_role:
                abort(403)

            return view(*args, **kwargs)
        return wrapped_view
    return decorator


@app.errorhandler(403)
def forbidden(error):
    return (
        "<h1>403 - Access denied</h1>"
        "<p>You do not have permission to access this page.</p>"
        '<p><a href="/">Return to StockAlert</a></p>',
        403
    )


# ---------------- LOGIN ----------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with get_connection() as connection:
            user = connection.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ).fetchone()

        if user and check_password_hash(
            user["password_hash"], password
        ):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]

            flash("Welcome to StockAlert!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("You have signed out successfully.", "success")
    return redirect(url_for("login"))


# ---------------- DASHBOARD ----------------

@app.route("/")
@login_required
def dashboard():
    """Display inventory totals and stock alerts."""
    with get_connection() as connection:
        total_products = connection.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        total_stock = connection.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM products"
        ).fetchone()[0]

        low_stock = connection.execute("""
            SELECT * FROM products
            WHERE quantity <= min_stock
            ORDER BY quantity ASC
        """).fetchall()

        expiry_batches = connection.execute("""
            SELECT b.*, p.name AS product_name
            FROM expiry_batches b
            JOIN products p ON p.id = b.product_id
            WHERE b.expiry_date <= date('now', '+30 days')
            ORDER BY b.expiry_date ASC
        """).fetchall()

    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_stock=total_stock,
        low_stock=low_stock,
        expiry_batches=expiry_batches
    )


# ---------------- PRODUCTS ----------------

@app.route("/products", methods=["GET", "POST"])
@login_required
def products():
    """List products; only Admin can add products."""
    if request.method == "POST":
        if session.get("role") != "admin":
            abort(403)

        sku = request.form.get("sku", "").strip()
        name = request.form.get("name", "").strip()
        category = request.form.get("category", "General").strip()
        min_stock_text = request.form.get("min_stock", "5").strip()
        price_text = request.form.get("selling_price", "0").strip()

        try:
            min_stock = int(min_stock_text)
            price = float(price_text)

            if not sku or not name:
                raise ValueError("SKU and product name are required.")

            if min_stock < 0 or price < 0:
                raise ValueError(
                    "Threshold and price cannot be negative."
                )

            with get_connection() as connection:
                connection.execute("""
                    INSERT INTO products
                    (sku, name, category, min_stock, selling_price)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    sku,
                    name,
                    category or "General",
                    min_stock,
                    price
                ))

            flash("Product added successfully.", "success")
            return redirect(url_for("products"))

        except ValueError as error:
            flash(
                str(error) or "Enter valid threshold and price values.",
                "danger"
            )
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                flash(
                    "That SKU already exists. Use a unique SKU.",
                    "danger"
                )
            else:
                flash(
                    "Could not save the product. Check the entered values.",
                    "danger"
                )

    with get_connection() as connection:
        product_list = connection.execute("""
            SELECT * FROM products ORDER BY name
        """).fetchall()

    return render_template("products.html", products=product_list)


# ---------------- STOCK MOVEMENTS ----------------

@app.route("/stock/<int:product_id>", methods=["POST"])
@login_required
def stock_movement(product_id):
    """Allow Admin and Staff to record stock movements."""
    movement_type = request.form.get("movement_type", "")
    reason = request.form.get("reason", "").strip()

    try:
        quantity = int(request.form.get("quantity", "0"))

        if movement_type not in ("IN", "OUT") or quantity <= 0:
            raise ValueError

        with get_connection() as connection:
            product = connection.execute(
                "SELECT quantity FROM products WHERE id = ?",
                (product_id,)
            ).fetchone()

            if product is None:
                flash("Product not found.", "danger")
                return redirect(url_for("products"))

            current_quantity = product["quantity"]

            new_quantity = (
                current_quantity + quantity
                if movement_type == "IN"
                else current_quantity - quantity
            )

            if new_quantity < 0:
                flash(
                    "Insufficient stock. Stock cannot become negative.",
                    "danger"
                )
                return redirect(url_for("products"))

            connection.execute(
                "UPDATE products SET quantity = ? WHERE id = ?",
                (new_quantity, product_id)
            )

            connection.execute("""
                INSERT INTO stock_movements
                (product_id, movement_type, quantity, reason)
                VALUES (?, ?, ?, ?)
            """, (
                product_id,
                movement_type,
                quantity,
                reason
            ))

        flash("Stock movement recorded successfully.", "success")

    except (ValueError, TypeError):
        flash(
            "Enter a valid positive quantity and movement type.",
            "danger"
        )

    return redirect(url_for("products"))


# ---------------- EXPIRY BATCHES ----------------

@app.route("/batches", methods=["POST"])
@login_required
@role_required("admin")
def add_batch():
    """Allow only Admin to add an expiry batch."""
    try:
        product_id = int(request.form.get("product_id", "0"))
        batch_number = request.form.get("batch_number", "").strip()
        expiry_date = request.form.get("expiry_date", "").strip()
        quantity = int(request.form.get("quantity", "0"))

        if not batch_number or not expiry_date or quantity < 0:
            raise ValueError

        date.fromisoformat(expiry_date)

        with get_connection() as connection:
            product = connection.execute(
                "SELECT id FROM products WHERE id = ?",
                (product_id,)
            ).fetchone()

            if product is None:
                flash("Select a valid product.", "danger")
                return redirect(url_for("products"))

            connection.execute("""
                INSERT INTO expiry_batches
                (product_id, batch_number, quantity, expiry_date)
                VALUES (?, ?, ?, ?)
            """, (
                product_id,
                batch_number,
                quantity,
                expiry_date
            ))

        flash("Expiry batch added successfully.", "success")

    except ValueError:
        flash(
            "Enter a valid product, batch, date, and quantity.",
            "danger"
        )
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            flash(
                "That batch number already exists for this product.",
                "danger"
            )
        else:
            flash("Could not save the batch.", "danger")

    return redirect(url_for("products"))


# ---------------- REPORTS ----------------

@app.route("/reports")
@login_required
def reports():
    """Display stock movement history and expiry information."""
    with get_connection() as connection:
        movements = connection.execute("""
            SELECT m.*, p.name AS product_name, p.sku
            FROM stock_movements m
            JOIN products p ON p.id = m.product_id
            ORDER BY m.created_at DESC, m.id DESC
        """).fetchall()

        batches = connection.execute("""
            SELECT b.*, p.name AS product_name,
                   COALESCE((
                       SELECT SUM(m.quantity)
                       FROM stock_movements m
                       WHERE m.product_id = p.id
                         AND m.movement_type = 'OUT'
                         AND m.created_at >= datetime('now', '-30 days')
                   ), 0) / 30.0 AS daily_usage
            FROM expiry_batches b
            JOIN products p ON p.id = b.product_id
            WHERE b.quantity > 0
            ORDER BY b.expiry_date
        """).fetchall()

    return render_template(
        "products.html",
        products=[],
        movements=movements,
        batches=batches,
        report_mode=True
    )


# ---------------- STAFF MANAGEMENT ----------------

@app.route("/staff", methods=["GET", "POST"])
@login_required
@role_required("admin")
def staff_management():
    """Allow Admin to create and view Staff accounts."""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not username:
            flash("Username is required.", "danger")
        elif len(password) < 8:
            flash(
                "Password must contain at least 8 characters.",
                "danger"
            )
        elif password != confirm_password:
            flash("Passwords do not match.", "danger")
        else:
            try:
                password_hash = generate_password_hash(password)

                with get_connection() as connection:
                    connection.execute("""
                        INSERT INTO users
                        (username, password_hash, role)
                        VALUES (?, ?, 'staff')
                    """, (username, password_hash))

                flash("Staff account created successfully.", "success")
                return redirect(url_for("staff_management"))

            except Exception as error:
                if "UNIQUE constraint failed" in str(error):
                    flash("That username already exists.", "danger")
                else:
                    flash(
                        "Could not create the staff account.",
                        "danger"
                    )

    with get_connection() as connection:
        staff_members = connection.execute("""
            SELECT id, username, role, created_at
            FROM users
            ORDER BY username
        """).fetchall()

    return render_template(
        "users.html",
        staff_members=staff_members
    )


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(debug=True)
