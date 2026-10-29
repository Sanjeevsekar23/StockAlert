
import secrets

from flask import Flask, render_template, request, redirect, url_for, flash
from database import init_db, get_connection

app = Flask(__name__)
app.secret_key = secrets.token_urlsafe(32)

# Create the database tables when the application starts.
init_db()


@app.route("/")
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


@app.route("/products", methods=["GET", "POST"])
def products():
    """List products and allow new products to be added."""
    if request.method == "POST":
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
                raise ValueError("Threshold and price cannot be negative.")

            with get_connection() as connection:
                connection.execute("""
                    INSERT INTO products
                    (sku, name, category, min_stock, selling_price)
                    VALUES (?, ?, ?, ?, ?)
                """, (sku, name, category or "General", min_stock, price))

            flash("Product added successfully.", "success")
            return redirect(url_for("products"))

        except ValueError as error:
            flash(str(error) or "Enter valid threshold and price values.", "error")
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                flash("That SKU already exists. Use a unique SKU.", "error")
            else:
                flash("Could not save the product. Check the entered values.", "error")

    with get_connection() as connection:
        product_list = connection.execute("""
            SELECT * FROM products ORDER BY name
        """).fetchall()

    return render_template("products.html", products=product_list)


@app.route("/stock/<int:product_id>", methods=["POST"])
def stock_movement(product_id):
    """Record stock-in or stock-out and update the inventory."""
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
                flash("Product not found.", "error")
                return redirect(url_for("products"))

            current_quantity = product["quantity"]
            new_quantity = (
                current_quantity + quantity
                if movement_type == "IN"
                else current_quantity - quantity
            )

            if new_quantity < 0:
                flash("Insufficient stock. Stock cannot become negative.", "error")
                return redirect(url_for("products"))

            connection.execute(
                "UPDATE products SET quantity = ? WHERE id = ?",
                (new_quantity, product_id)
            )

            connection.execute("""
                INSERT INTO stock_movements
                (product_id, movement_type, quantity, reason)
                VALUES (?, ?, ?, ?)
            """, (product_id, movement_type, quantity, reason))

        flash("Stock movement recorded successfully.", "success")

    except (ValueError, TypeError):
        flash("Enter a valid positive quantity and movement type.", "error")

    return redirect(url_for("products"))


@app.route("/batches", methods=["POST"])
def add_batch():
    """Add an expiry-tracked batch for a product."""
    try:
        product_id = int(request.form.get("product_id", "0"))
        batch_number = request.form.get("batch_number", "").strip()
        expiry_date = request.form.get("expiry_date", "").strip()
        quantity = int(request.form.get("quantity", "0"))

        if not batch_number or not expiry_date or quantity < 0:
            raise ValueError

        # Validate the ISO date before storing it.
        from datetime import date
        date.fromisoformat(expiry_date)

        with get_connection() as connection:
            product = connection.execute(
                "SELECT id FROM products WHERE id = ?", (product_id,)
            ).fetchone()
            if product is None:
                flash("Select a valid product.", "error")
                return redirect(url_for("products"))

            connection.execute("""
                INSERT INTO expiry_batches
                (product_id, batch_number, quantity, expiry_date)
                VALUES (?, ?, ?, ?)
            """, (product_id, batch_number, quantity, expiry_date))

        flash("Expiry batch added successfully.", "success")

    except ValueError:
        flash("Enter a valid product, batch, date, and quantity.", "error")
    except Exception as error:
        if "UNIQUE constraint failed" in str(error):
            flash("That batch number already exists for this product.", "error")
        else:
            flash("Could not save the batch.", "error")

    return redirect(url_for("products"))


@app.route("/reports")
def reports():
    """Display movement history and expiry risk information."""
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


if __name__ == "__main__":
    app.run(debug=True)
