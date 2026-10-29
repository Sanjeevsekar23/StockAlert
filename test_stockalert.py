"""Focused regression tests for inventory expiry calculations and routes."""
import os
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, timedelta

_test_dir = tempfile.TemporaryDirectory(prefix="stockalert-test-")
os.environ["STOCKALERT_DATABASE"] = os.path.join(_test_dir.name, "test.db")

import pytest
from werkzeug.security import generate_password_hash

import app as stockalert
import database as database_module
from database import get_connection, init_db


@pytest.fixture
def client():
    init_db()
    with get_connection() as connection:
        connection.execute("DELETE FROM attendance")
        connection.execute("DELETE FROM stock_movements")
        connection.execute("DELETE FROM expiry_batches")
        connection.execute("DELETE FROM products")
        connection.execute("DELETE FROM users")
        connection.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("admin_test", generate_password_hash("long-test-password"), "admin"))
        connection.execute("INSERT INTO users(username,password_hash,role) VALUES(?,?,?)", ("staff_test", generate_password_hash("long-test-password"), "staff"))
    stockalert.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return stockalert.app.test_client()


def sign_in(client, username="admin_test", password="long-test-password"):
    return client.post("/login", data={"username": username, "password": password})


def test_risk_boundaries_and_bounds():
    today = date(2026, 10, 27)
    assert stockalert.calculate_expiry_risk(9, "2026-10-26", 1, today) == (-1, 9, "Expired")
    assert stockalert.calculate_expiry_risk(9, "2026-10-27", 1, today) == (0, 9, "Expires Today")
    assert stockalert.calculate_expiry_risk(9, "2026-10-28", 0, today) == (1, 9, "High Risk")
    assert stockalert.calculate_expiry_risk(9, "2026-10-28", 20, today) == (1, 0, "Low Risk")
    assert stockalert.calculate_expiry_risk(9, "bad-date", 0, today) == (None, 9, "Invalid Date")
    assert stockalert.calculate_expiry_risk(9, "2026-10-28", .5, today)[1] == 9
    # ceil is applied to the remaining-risk estimate: 10 - (2.01 * 1) -> 8.
    assert stockalert.calculate_expiry_risk(10, "2026-10-28", 2.01, today)[1] == 8
    for risk in (stockalert.calculate_expiry_risk(10, "2026-10-28", usage, today)[1] for usage in (0, .1, 10, 1000)):
        assert 0 <= risk <= 10


def test_auth_roles_batches_and_stock(client):
    assert client.get("/").status_code == 302
    assert client.get("/staff").status_code == 302
    sign_in(client)
    assert client.get("/staff").status_code == 200
    assert client.post("/products", data={"sku":"T1", "name":"Tea", "min_stock":"2", "selling_price":"1"}).status_code == 302
    with get_connection() as connection:
        product_id = connection.execute("SELECT id FROM products WHERE sku='T1'").fetchone()[0]
    assert client.post(f"/stock/{product_id}", data={"movement_type":"IN", "quantity":"5"}).status_code == 302
    assert client.post(f"/stock/{product_id}", data={"movement_type":"OUT", "quantity":"6"}).status_code == 302
    assert client.post(f"/stock/{product_id}", data={"movement_type":"OUT", "quantity":"4", "reason":"sale"}).status_code == 302
    with get_connection() as connection:
        assert connection.execute("SELECT quantity FROM products WHERE id=?", (product_id,)).fetchone()[0] == 1
    batch = {"product_id":product_id, "batch_number":"B1", "quantity":"3", "expiry_date":date.today().isoformat()}
    assert client.post("/batches", data={**batch, "quantity":"0"}).status_code == 302
    assert client.post("/batches", data=batch).status_code == 302
    client.post("/batches", data=batch)
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM expiry_batches WHERE product_id=?", (product_id,)).fetchone()[0] == 1
    dashboard_response = client.get("/")
    assert b"Tea" in dashboard_response.data
    assert b"Low-Stock Alerts" in dashboard_response.data
    report_response = client.get("/reports")
    assert b"Tea" in report_response.data and b"sale" in report_response.data
    client.post("/logout")
    sign_in(client, "staff_test")
    assert client.get("/staff").status_code == 200
    assert client.post("/staff", data={"username":"another", "password":"long-test-password", "confirm_password":"long-test-password"}).status_code == 403
    assert client.post("/batches", data=batch).status_code == 403
    assert client.get("/reports").status_code == 200
    assert client.post(f"/stock/{product_id}", data={"movement_type":"OUT", "quantity":"1", "reason":"staff sale"}).status_code == 302


def test_reports_and_dashboard_empty_and_populated(client):
    sign_in(client)
    assert client.get("/reports").status_code == 200
    assert client.get("/").status_code == 200


def test_staff_account_validation_and_duplicate(client):
    sign_in(client)
    payload = {"username":"new_staff", "password":"strong-pass-1", "confirm_password":"strong-pass-1", "age":"28", "contact_number":"+919876543210", "work_location":"Central Store"}
    response = client.post("/staff", data=payload, follow_redirects=True)
    assert response.status_code == 200
    assert b"Staff account created successfully" in response.data
    assert b"Contact Number" in response.data and b"Age" in response.data
    assert b"Existing Accounts" in response.data and b"+919876543210" in response.data
    assert b"Central Store" in response.data
    with get_connection() as connection:
        member = connection.execute("SELECT password_hash, role, age, contact_number, work_location FROM users WHERE username='new_staff'").fetchone()
        assert member["role"] == "staff"
        assert member["password_hash"] != payload["password"]
        assert member["age"] == 28
        assert member["contact_number"] == "+919876543210"
        assert member["work_location"] == "Central Store"

    response = client.post("/staff", data=payload, follow_redirects=True)
    assert b"That username already exists" in response.data

    invalid_cases = [
        ({**payload, "username":"  "}, b"Username is required"),
        ({**payload, "username":"young", "age":"17"}, b"valid age from 18 to 100"),
        ({**payload, "username":"old", "age":"101"}, b"valid age from 18 to 100"),
        ({**payload, "username":"decimal_age", "age":"18.5"}, b"valid age from 18 to 100"),
        ({**payload, "username":"bad_phone", "contact_number":"1234567890"}, b"valid 10-digit Indian mobile number"),
        ({**payload, "username":"bad_country_phone", "contact_number":"+911234567890"}, b"valid 10-digit Indian mobile number"),
        ({**payload, "username":"short_pw", "password":"short", "confirm_password":"short"}, b"Password must contain at least 8 characters"),
        ({**payload, "username":"mismatch", "confirm_password":"different-pass"}, b"Passwords do not match"),
    ]
    for invalid_payload, expected_message in invalid_cases:
        response = client.post("/staff", data=invalid_payload, follow_redirects=True)
        assert response.status_code == 200
        assert expected_message in response.data
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM users WHERE username IN ('young','old','decimal_age','bad_phone','bad_country_phone','short_pw','mismatch')").fetchone()[0] == 0
    client.post("/products", data={"sku":"T2", "name":"Coffee", "min_stock":"2", "selling_price":"1"})
    with get_connection() as connection:
        product_id = connection.execute("SELECT id FROM products WHERE sku='T2'").fetchone()[0]
        connection.execute("INSERT INTO expiry_batches(product_id,batch_number,quantity,expiry_date) VALUES(?,?,?,?)", (product_id,"R1",4,(date.today()+timedelta(days=2)).isoformat()))
    response = client.get("/reports")
    assert response.status_code == 200
    assert b"Estimated Units at Risk" in response.data
    assert b"product-level" in response.data
    assert client.get("/").status_code == 200


def test_staff_attendance_location_and_status_permissions(client):
    sign_in(client)
    admin_page = client.get("/staff")
    today = date.today()
    assert admin_page.status_code == 200
    assert b"View Attendance" in admin_page.data
    assert b"attendance_date" not in admin_page.data
    admin_mark_page = client.get("/attendance/mark")
    assert admin_mark_page.status_code == 200
    assert b"Attendance" in admin_mark_page.data
    assert b'href="/attendance/mark"' in admin_page.data
    assert b'href="/attendance/view"' in admin_page.data
    assert b"staff_test" in admin_mark_page.data
    assert client.post("/attendance/mark", follow_redirects=True).status_code == 200
    created_staff = client.post("/staff", data={"username":"attend_staff", "password":"strong-pass-1", "confirm_password":"strong-pass-1", "age":"32", "contact_number":"9876543210", "work_location":"Initial Branch"}, follow_redirects=True)
    assert b"Initial Branch" in created_staff.data
    with get_connection() as connection:
        staff_id = connection.execute("SELECT id FROM users WHERE username='attend_staff'").fetchone()[0]
        admin_id = connection.execute("SELECT id FROM users WHERE username='admin_test'").fetchone()[0]

    profile_url = f"/staff/{staff_id}/update"
    location_url = f"/staff/{staff_id}/location"
    status_url = f"/staff/{staff_id}/status"
    location_saved = client.post(location_url, data={"work_location":" Central Warehouse "}, follow_redirects=True)
    assert location_saved.status_code == 200
    assert b"Work location saved." in location_saved.data
    with get_connection() as connection:
        allocated = connection.execute("SELECT work_location, staff_status FROM users WHERE id=?", (staff_id,)).fetchone()
        assert tuple(allocated) == ("Central Warehouse", "Active")
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    status_saved = client.post(status_url, data={"staff_status":"On Leave"}, follow_redirects=True)
    assert status_saved.status_code == 200
    assert b"Staff status updated." in status_saved.data
    assert b"Attendance marked successfully" not in status_saved.data
    assert b"Profile Updated" not in status_saved.data
    with get_connection() as connection:
        allocation = connection.execute("SELECT work_location, staff_status FROM users WHERE id=?", (staff_id,)).fetchone()
        assert tuple(allocation) == ("Central Warehouse", "On Leave")
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    assert client.post(profile_url, data={"age":"32", "contact_number":"9876543210"}).status_code == 302
    assert client.post(profile_url, data={"age":"17", "contact_number":"9876543210"}).status_code == 302
    assert client.post(profile_url, data={"contact_number":"1234567890"}).status_code == 302
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    client.post("/logout")
    sign_in(client, "attend_staff", "strong-pass-1")
    staff_page = client.get("/staff")
    assert b"Central Warehouse" in staff_page.data and b"On Leave" in staff_page.data
    assert b"Mark Today's Attendance" in staff_page.data
    assert b"attendance_date" not in staff_page.data
    assert client.get("/attendance/view").status_code == 403
    on_leave_attempt = client.post(
        "/attendance/mark",
        data={"staff_id":staff_id, "attendance_status":"Present"},
        follow_redirects=True
    )
    assert b"Only Active staff can mark attendance" in on_leave_attempt.data
    assert client.post("/attendance/mark", data={"user_id":admin_id, "attendance_date":today.isoformat(), "attendance_status":"Present"}).status_code == 302
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    assert client.post(location_url, data={"work_location":"Tampered"}).status_code == 403
    assert client.post(status_url, data={"staff_status":"Active"}).status_code == 403

    client.post("/logout")
    sign_in(client)
    assert client.post(status_url, data={"staff_status":"Active"}).status_code == 302
    with get_connection() as connection:
        profile = connection.execute("SELECT age, contact_number, work_location, staff_status FROM users WHERE id=?", (staff_id,)).fetchone()
        assert tuple(profile) == (32, "9876543210", "Central Warehouse", "Active")
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    refreshed_admin_page = client.get("/staff")
    assert b"Central Warehouse" in refreshed_admin_page.data
    client.post("/logout")
    sign_in(client, "attend_staff", "strong-pass-1")
    staff_page = client.get("/attendance/mark")
    assert staff_page.status_code == 200
    assert b"Your Attendance" in staff_page.data
    assert b'name="attendance_status"' in staff_page.data
    assert b'type="date"' not in staff_page.data
    assert f"{today.isoformat()}".encode() in staff_page.data
    yesterday = (today - timedelta(days=1)).isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    rejected_past = client.post("/attendance/mark", data={"attendance_date":yesterday})
    assert rejected_past.status_code == 302
    assert client.post("/attendance/mark", data={"attendance_date":tomorrow}).status_code == 302
    assert client.post("/attendance/mark", data={"attendance_date":"not-a-date"}).status_code == 302
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    invalid_status = client.post("/attendance/mark", data={"attendance_status":"Absent"}, follow_redirects=True)
    assert b"Attendance can only be marked as Present" in invalid_status.data
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 0
    successful_mark = client.post("/attendance/mark", data={"user_id":admin_id, "attendance_status":"Present"}, follow_redirects=True)
    assert successful_mark.status_code == 200
    assert b"Attendance marked successfully" in successful_mark.data
    client.post("/attendance/mark", data={"attendance_status":"Present"})
    with get_connection() as connection:
        rows = connection.execute("SELECT user_id, attendance_date, attendance_status, check_in_time FROM attendance").fetchall()
        assert len(rows) == 1
        assert rows[0]["user_id"] == staff_id
        assert rows[0]["attendance_date"] == today.isoformat()
        assert rows[0]["attendance_status"] == "Present"
        assert len(rows[0]["check_in_time"]) == 8
    assert b"Attendance has already been marked" in client.get("/attendance/mark", follow_redirects=True).data

    client.post("/logout")
    sign_in(client)
    today_view = client.get(f"/attendance/view?attendance_date={today.isoformat()}")
    assert f'value="{today.isoformat()}"'.encode() in today_view.data
    today_staff_row = next(row for row in today_view.data.split(b"<tr>") if b"attend_staff" in row)
    assert b"Present" in today_staff_row
    assert today.isoformat().encode() in today_staff_row
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 1
    historical_date = "2020-04-05"
    with get_connection() as connection:
        connection.execute("INSERT INTO attendance(user_id, attendance_date, attendance_status, check_in_time) VALUES(?, ?, 'Present', '08:15:00')", (staff_id, historical_date))
    historical_view = client.get(f"/attendance/view?attendance_date={historical_date}")
    assert historical_view.status_code == 200
    assert f'value="{historical_date}"'.encode() in historical_view.data
    historical_staff_row = next(row for row in historical_view.data.split(b"<tr>") if b"attend_staff" in row)
    assert b"Present" in historical_staff_row
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 2
    legacy_date = "2026-10-02"
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO attendance(user_id, attendance_date, attendance_status, check_in_time) VALUES(?, ?, 'Present', '09:30:00')",
            (staff_id, "02-10-2026")
        )
    legacy_date_view = client.get(f"/attendance/view?attendance_date={legacy_date}")
    assert legacy_date_view.status_code == 200
    assert f'value="{legacy_date}"'.encode() in legacy_date_view.data
    staff_row = next(row for row in legacy_date_view.data.split(b"<tr>") if b"attend_staff" in row)
    assert b"Present" in staff_row
    assert b"Not marked" not in staff_row
    assert b"Central Warehouse" in staff_row
    invalid_filter = client.get("/attendance/view?attendance_date=invalid")
    assert invalid_filter.status_code == 200
    assert f'value="{date.today().isoformat()}"'.encode() in invalid_filter.data
    unmarked_date = client.get("/attendance/view?attendance_date=2019-01-01")
    unmarked_row = next(row for row in unmarked_date.data.split(b"<tr>") if b"attend_staff" in row)
    assert b"Not Marked" in unmarked_row
    with get_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 3
        connection.execute(
            "INSERT INTO users(username, password_hash, role, work_location, staff_status) VALUES(?, ?, 'staff', ?, 'Active')",
            ("legacy_attendee", generate_password_hash("strong-pass-1"), "Legacy Branch")
        )
        legacy_staff_id = connection.execute(
            "SELECT id FROM users WHERE username='legacy_attendee'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO attendance(user_id, attendance_date, attendance_status, check_in_time) VALUES(?, ?, 'Present', '07:45:00')",
            (legacy_staff_id, today.strftime("%d-%m-%Y"))
        )
    client.post("/logout")
    sign_in(client, "legacy_attendee", "strong-pass-1")
    duplicate_legacy = client.post("/attendance/mark", data={"attendance_status":"Present"}, follow_redirects=True)
    assert b"Attendance has already been marked for today" in duplicate_legacy.data
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM attendance WHERE user_id = ? AND attendance_date IN (?, ?)",
            (legacy_staff_id, today.isoformat(), today.strftime("%d-%m-%Y"))
        ).fetchone()[0] == 1
    client.post("/logout")
    sign_in(client)
    assert client.post(status_url, data={"staff_status":"Inactive"}).status_code == 302
    client.post("/logout")
    sign_in(client, "attend_staff", "strong-pass-1")
    response = client.post("/attendance/mark", data={"attendance_date":today.isoformat(), "attendance_status":"Present"}, follow_redirects=True)
    assert b"Only Active staff can mark attendance" in response.data
    inactive_mark_page = client.get("/attendance/mark")
    assert inactive_mark_page.status_code == 200
    assert b"Only Active staff can mark attendance" in inactive_mark_page.data


def test_admin_marks_staff_and_view_shows_saved_attendance(client):
    sign_in(client)
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO users(username, password_hash, role, work_location, staff_status) VALUES(?, ?, 'staff', ?, 'Active')",
            ("markable_staff", generate_password_hash("strong-pass-1"), "North Store")
        )
        staff_id = connection.execute(
            "SELECT id FROM users WHERE username='markable_staff'"
        ).fetchone()[0]

    today = date.today()
    mark_page = client.get("/attendance/mark")
    assert b"markable_staff" in mark_page.data
    assert b"Mark Present" in mark_page.data

    marked = client.post(
        "/attendance/mark",
        data={"staff_id":staff_id, "attendance_status":"Present"},
        follow_redirects=True
    )
    assert marked.status_code == 200
    assert b"Attendance marked successfully" in marked.data
    with get_connection() as connection:
        record = connection.execute(
            "SELECT user_id, attendance_date, attendance_status FROM attendance WHERE user_id=?",
            (staff_id,)
        ).fetchone()
    assert tuple(record) == (staff_id, today.isoformat(), "Present")

    viewed = client.get(f"/attendance/view?attendance_date={today.isoformat()}")
    row = next(line for line in viewed.data.split(b"<tr>") if b"markable_staff" in line)
    assert b"Present" in row
    assert today.isoformat().encode() in row
    assert b"North Store" in row

    duplicate = client.post(
        "/attendance/mark",
        data={"staff_id":staff_id, "attendance_status":"Present"},
        follow_redirects=True
    )
    assert b"Attendance has already been marked for today" in duplicate.data
    with get_connection() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM attendance WHERE user_id=? AND attendance_date=?",
            (staff_id, today.isoformat())
        ).fetchone()[0] == 1


def test_existing_user_rows_survive_staff_schema_migration(tmp_path, monkeypatch):
    legacy_db = tmp_path / "legacy.db"
    legacy_connection = sqlite3.connect(legacy_db)
    legacy_connection.execute("""
        CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    legacy_connection.execute(
        "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
        ("existing_staff", "preserved-hash", "staff")
    )
    legacy_connection.execute("""
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            attendance_date TEXT NOT NULL,
            attendance_status TEXT NOT NULL DEFAULT 'Present',
            check_in_time TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, attendance_date),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    legacy_connection.execute(
        "INSERT INTO attendance(user_id, attendance_date, attendance_status, check_in_time) VALUES(1, '2024-01-02', 'Present', '08:00:00')"
    )
    legacy_connection.commit()
    legacy_connection.close()
    monkeypatch.setattr(database_module, "DATABASE_PATH", legacy_db)

    init_db()
    migrated = get_connection()
    try:
        member = migrated.execute("""
            SELECT username, password_hash, role, work_location, staff_status
            FROM users WHERE username = ?
        """, ("existing_staff",)).fetchone()
        assert tuple(member) == ("existing_staff", "preserved-hash", "staff", "", "Active")
        new_columns = migrated.execute("SELECT age, contact_number FROM users WHERE username = ?", ("existing_staff",)).fetchone()
        assert tuple(new_columns) == (None, None)
        attendance = migrated.execute("SELECT user_id, attendance_date, check_in_time FROM attendance").fetchone()
        assert tuple(attendance) == (1, "2024-01-02", "08:00:00")
    finally:
        migrated.close()


def test_production_startup_requires_secret_and_persistent_database():
    environment = os.environ.copy()
    environment["STOCKALERT_ENV"] = "production"
    environment.pop("STOCKALERT_SECRET_KEY", None)
    environment.pop("STOCKALERT_DATABASE", None)
    project_path = os.path.dirname(os.path.abspath(stockalert.__file__))
    import_script = f"import sys; sys.path.insert(0, {project_path!r}); import app"

    missing_secret = subprocess.run(
        [sys.executable, "-c", import_script],
        env=environment, capture_output=True, text=True
    )
    assert missing_secret.returncode != 0
    assert "STOCKALERT_SECRET_KEY must be configured" in missing_secret.stderr

    environment["STOCKALERT_SECRET_KEY"] = "temporary-test-only-secret"
    missing_database = subprocess.run(
        [sys.executable, "-c", import_script],
        env=environment, capture_output=True, text=True
    )
    assert missing_database.returncode != 0
    assert "STOCKALERT_DATABASE must point to persistent storage" in missing_database.stderr
