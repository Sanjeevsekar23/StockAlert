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
    abort,
)
from werkzeug.security import check_password_hash, generate_password_hash

from database import init_db, get_connection


app = Flask(__name__)
app.secret_key = os.environ.get(
    "STOCKALERT_SECRET_KEY",
    secrets.token_urlsafe(32),
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
        403,
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
                (username,),
            ).fetchone()

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            flash("Welcome to StockAlert!", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid username or password.", "danger")

    return render_template("login.html")
    if __name__ == "__main__":
        app.run(debug=True)
