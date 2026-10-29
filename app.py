
import os
import re
import secrets
import sqlite3
from functools import wraps
from datetime import date, datetime
from math import ceil

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
IS_PRODUCTION = os.environ.get("STOCKALERT_ENV", "").lower() == "production"
secret_key = os.environ.get("STOCKALERT_SECRET_KEY")
if IS_PRODUCTION and not secret_key:
    raise RuntimeError("STOCKALERT_SECRET_KEY must be configured in production.")
if IS_PRODUCTION and not os.environ.get("STOCKALERT_DATABASE"):
    raise RuntimeError(
        "STOCKALERT_DATABASE must point to persistent storage in production."
    )

app.config.update(
    SECRET_KEY=secret_key or secrets.token_urlsafe(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=IS_PRODUCTION,
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
            WHERE b.quantity > 0
            ORDER BY b.expiry_date ASC
        """).fetchall()
        today = date.today()
        expired_batches = [b for b in expiry_batches if _parse_date(b["expiry_date"]) and _parse_date(b["expiry_date"]) < today]
        expiring_soon_batches = [b for b in expiry_batches if _parse_date(b["expiry_date"]) and today <= _parse_date(b["expiry_date"]) <= date.fromordinal(today.toordinal() + 30)]

    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_stock=total_stock,
        low_stock=low_stock,
        expiry_batches=expiry_batches,
        expired_batches=expired_batches,
        expiring_soon_batches=expiring_soon_batches
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

        if not batch_number or len(batch_number) > 100 or not expiry_date or quantity < 1:
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
            SELECT b.*, p.name AS product_name, p.sku,
                   COALESCE((
                       SELECT SUM(m.quantity)
                       FROM stock_movements m
                       WHERE m.product_id = p.id
                         AND m.movement_type = 'OUT'
                         AND m.created_at >= datetime('now', '-30 days')
                   ), 0) AS units_used_30d,
                   EXISTS (
                       SELECT 1 FROM stock_movements history
                       WHERE history.product_id = p.id
                         AND history.movement_type = 'OUT'
                         AND history.created_at >= datetime('now', '-30 days')
                   ) AS has_recent_out_history
            FROM expiry_batches b
            JOIN products p ON p.id = b.product_id
            WHERE b.quantity > 0
            ORDER BY b.expiry_date
        """).fetchall()

    today = date.today()
    risk_batches = []
    for batch in batches:
        row = dict(batch)
        daily_usage = row["units_used_30d"] / 30.0
        days_remaining, risk, status = calculate_expiry_risk(
            row["quantity"], row["expiry_date"], daily_usage, today
        )
        row.update(days_remaining=days_remaining, daily_usage=daily_usage,
                   estimated_at_risk=risk, risk_status=status)
        risk_batches.append(row)

    report_summary = {
        "remaining_units": sum(b["quantity"] for b in risk_batches),
        "units_at_risk": sum(b["estimated_at_risk"] for b in risk_batches),
        "high_risk_batches": sum(b["risk_status"] in ("High Risk", "Expired", "Expires Today") for b in risk_batches),
    }

    return render_template(
        "products.html",
        products=[],
        movements=movements,
        batches=risk_batches,
        report_summary=report_summary,
        report_mode=True
    )


def _parse_date(value):
    """Parse stored ISO dates safely; malformed legacy values remain reportable."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def calculate_expiry_risk(quantity, expiry_value, daily_usage, today=None):
    """Return a bounded product-level estimate for one non-empty batch."""
    today = today or date.today()
    expiry = _parse_date(expiry_value)
    days_remaining = (expiry - today).days if expiry else None
    if expiry is None:
        status, risk = "Invalid Date", quantity
    elif days_remaining < 0:
        status, risk = "Expired", quantity
    elif days_remaining == 0:
        status, risk = "Expires Today", quantity
    else:
        risk = max(0, min(quantity, ceil(quantity - daily_usage * days_remaining)))
        ratio = risk / quantity if quantity else 0
        # High: at least 75% at risk; At Risk: at least 25%; otherwise Low.
        status = "High Risk" if ratio >= .75 else "At Risk" if ratio >= .25 else "Low Risk"
    return days_remaining, risk, status


# ---------------- STAFF MANAGEMENT ----------------

@app.route("/staff", methods=["GET", "POST"])
@login_required
def staff_management():
    """Show staff profiles and admin staff account management."""
    is_admin = session.get("role") == "admin"
    if session.get("role") not in ("admin", "staff"):
        abort(403)

    if request.method == "POST":
        if not is_admin:
            abort(403)
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        age_text = request.form.get("age", "").strip()
        contact_number = request.form.get("contact_number", "").strip()
        work_location = request.form.get("work_location", "").strip()

        if not username:
            flash("Username is required.", "danger")
        elif _parse_staff_age(age_text) is None:
            flash("Enter a valid age from 18 to 100.", "danger")
        elif not _valid_indian_mobile(contact_number):
            flash("Enter a valid 10-digit Indian mobile number, optionally prefixed with +91.", "danger")
        elif len(work_location) > 120:
            flash("Work location must be 120 characters or fewer.", "danger")
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
                        (username, password_hash, role, age, contact_number, work_location)
                        VALUES (?, ?, 'staff', ?, ?, ?)
                    """, (username, password_hash, _parse_staff_age(age_text), contact_number, work_location))

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
        if is_admin:
            staff_members = connection.execute("""
                SELECT u.id, u.username, u.role, u.created_at,
                       u.work_location, u.staff_status, u.age, u.contact_number
                FROM users u
                ORDER BY u.username
            """).fetchall()
            current_staff = None
        else:
            current_staff = connection.execute("""
                SELECT id, username, role, work_location, staff_status
                FROM users WHERE id = ? AND role = 'staff'
            """, (session["user_id"],)).fetchone()
            staff_members = []

    return render_template(
        "users.html",
        staff_members=staff_members,
        current_staff=current_staff,
        is_admin=is_admin
    )


def _attendance_rows_for_date(connection, selected_date, staff_id=None):
    """Return staff and their record for the same date formats used by marking."""
    iso_date = selected_date.isoformat()
    legacy_date = selected_date.strftime("%d-%m-%Y")
    staff_filter = " AND u.id = ?" if staff_id is not None else ""
    parameters = [iso_date, legacy_date, iso_date]
    if staff_id is not None:
        parameters.append(staff_id)
    return connection.execute(f"""
        SELECT u.id, u.username, u.work_location, u.staff_status,
               a.id AS attendance_id, a.attendance_status, a.attendance_date,
               a.check_in_time
        FROM users AS u
        LEFT JOIN attendance AS a
          ON a.id = (
            SELECT matched.id
            FROM attendance AS matched
            WHERE matched.user_id = u.id
              AND matched.attendance_date IN (?, ?)
            ORDER BY CASE WHEN matched.attendance_date = ? THEN 0 ELSE 1 END
            LIMIT 1
          )
        WHERE u.role = 'staff'{staff_filter}
        ORDER BY u.username
    """, parameters).fetchall()


@app.route("/attendance/view", methods=["GET"])
@login_required
@role_required("admin")
def view_attendance():
    """Show saved attendance for a selected date without changing records."""
    selected_date = datetime.now().date()
    requested_date = request.args.get("attendance_date")
    if requested_date is not None:
        parsed_date = _parse_date(requested_date)
        if parsed_date is None or parsed_date.isoformat() != requested_date:
            flash("Select a valid attendance date.", "danger")
        else:
            selected_date = parsed_date

    with get_connection() as connection:
        attendance_rows = _attendance_rows_for_date(connection, selected_date)

    return render_template(
        "attendance_view.html",
        attendance_rows=attendance_rows,
        selected_date=selected_date.isoformat()
    )


@app.route("/attendance/mark", methods=["GET", "POST"])
@login_required
def mark_attendance():
    """Show today's eligible staff and record attendance for an authorized user."""
    role = session.get("role")
    if role not in ("admin", "staff"):
        abort(403)

    now = datetime.now()
    today = now.date()
    attendance_date = today.isoformat()
    is_admin = role == "admin"
    with get_connection() as connection:
        staff_members = _attendance_rows_for_date(
            connection,
            today,
            staff_id=None if is_admin else session["user_id"]
        )

    if request.method == "POST":
        submitted_date_text = request.form.get("attendance_date")
        if submitted_date_text is not None:
            submitted_date = _parse_date(submitted_date_text)
            if submitted_date is None or submitted_date.isoformat() != submitted_date_text:
                flash("Choose a valid date.", "danger")
                return redirect(url_for("mark_attendance"))
            if submitted_date != today:
                flash("Attendance can only be marked for today's date.", "danger")
                return redirect(url_for("mark_attendance"))

        if is_admin:
            try:
                target_staff_id = int(request.form.get("staff_id", ""))
            except (TypeError, ValueError):
                target_staff_id = None
            target_staff = next(
                (member for member in staff_members if member["id"] == target_staff_id),
                None
            )
        else:
            target_staff = staff_members[0] if staff_members else None

        if target_staff is None:
            flash("Select a valid staff member.", "danger")
            return redirect(url_for("mark_attendance"))
        if target_staff["staff_status"] != "Active":
            flash("Only Active staff can mark attendance.", "danger")
            return redirect(url_for("mark_attendance"))
        if request.form.get("attendance_status", "Present").strip() != "Present":
            flash("Attendance can only be marked as Present.", "danger")
            return redirect(url_for("mark_attendance"))
        if target_staff["attendance_id"] is not None:
            flash("Attendance has already been marked for today.", "warning")
            return redirect(url_for("mark_attendance"))

        try:
            with get_connection() as connection:
                connection.execute("""
                    INSERT INTO attendance
                        (user_id, attendance_date, attendance_status, check_in_time)
                    VALUES (?, ?, 'Present', ?)
                """, (target_staff["id"], attendance_date, now.strftime("%H:%M:%S")))
        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed" in str(error):
                flash("Attendance has already been marked for today.", "warning")
            else:
                raise
        else:
            flash(f"Attendance marked successfully for {target_staff['username']}.", "success")
        return redirect(url_for("mark_attendance"))

    return render_template(
        "attendance_mark.html",
        staff_members=staff_members,
        is_admin=is_admin,
        today=attendance_date
    )


@app.route("/staff/<int:user_id>/location", methods=["POST"])
@login_required
@role_required("admin")
def update_staff_location(user_id):
    """Allocate or update a staff member's work location."""
    work_location = request.form.get("work_location", "").strip()
    if len(work_location) > 120:
        flash("Work location must be 120 characters or fewer.", "danger")
        return redirect(url_for("staff_management"))

    with get_connection() as connection:
        result = connection.execute(
            "UPDATE users SET work_location = ? WHERE id = ? AND role = 'staff'",
            (work_location, user_id)
        )
        if result.rowcount == 0:
            flash("Staff member not found.", "danger")
        else:
            flash("Work location saved.", "success")
    return redirect(url_for("staff_management"))


@app.route("/staff/<int:user_id>/status", methods=["POST"])
@login_required
@role_required("admin")
def update_staff_status(user_id):
    """Change a staff member's Active / On Leave / Inactive status."""
    staff_status = request.form.get("staff_status", "").strip()
    if staff_status not in ("Active", "On Leave", "Inactive"):
        flash("Select a valid staff status.", "danger")
        return redirect(url_for("staff_management"))

    with get_connection() as connection:
        result = connection.execute(
            "UPDATE users SET staff_status = ? WHERE id = ? AND role = 'staff'",
            (staff_status, user_id)
        )
        if result.rowcount == 0:
            flash("Staff member not found.", "danger")
        else:
            flash("Staff status updated.", "success")
    return redirect(url_for("staff_management"))


@app.route("/staff/<int:user_id>/update", methods=["POST"])
@login_required
@role_required("admin")
def update_staff(user_id):
    """Update a staff member's age and contact profile details."""
    age_text = request.form.get("age", "").strip()
    contact_number = request.form.get("contact_number", "").strip()
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT age, contact_number FROM users WHERE id = ? AND role = 'staff'",
            (user_id,)
        ).fetchone()
        if existing is None:
            flash("Staff member not found.", "danger")
            return redirect(url_for("staff_management"))

        age = existing["age"]
        if "age" in request.form:
            if not age_text:
                age = None
            else:
                age = _parse_staff_age(age_text)
                if age is None:
                    flash("Enter a valid age from 18 to 100.", "danger")
                    return redirect(url_for("staff_management"))
        if contact_number:
            if not _valid_indian_mobile(contact_number):
                flash("Enter a valid 10-digit Indian mobile number, optionally prefixed with +91.", "danger")
                return redirect(url_for("staff_management"))
        elif "contact_number" in request.form:
            contact_number = None
        else:
            contact_number = existing["contact_number"]

        result = connection.execute("""
            UPDATE users SET age = ?, contact_number = ?
            WHERE id = ? AND role = 'staff'
        """, (age, contact_number, user_id))
        if result.rowcount == 0:
            flash("Staff member not found.", "danger")
        else:
            flash("Staff profile updated.", "success")
    return redirect(url_for("staff_management"))


def _valid_indian_mobile(value):
    """Accept ten-digit Indian mobile numbers, with an optional +91 prefix."""
    return bool(re.fullmatch(r"(?:\+91)?[6-9][0-9]{9}", value or ""))


def _parse_staff_age(value):
    """Return a validated staff age or None."""
    if not re.fullmatch(r"[0-9]{1,3}", value or ""):
        return None
    age = int(value)
    return age if 18 <= age <= 100 else None


# ---------------- RUN APPLICATION ----------------

if __name__ == "__main__":
    app.run(
        host=os.environ.get("STOCKALERT_HOST", "127.0.0.1"),
        port=int(os.environ.get("STOCKALERT_PORT", "80")),
        debug=False,
    )
