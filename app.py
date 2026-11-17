
import os
import sqlite3
import secrets
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, abort
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-this-development-secret")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATABASE = os.path.join(BASE_DIR, "lost_found.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

CATEGORIES = [
    "Electronics", "Books", "Bags", "ID Cards",
    "Keys", "Clothing", "Accessories", "Other"
]


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT NOT NULL,
            item_type TEXT NOT NULL,
            location TEXT NOT NULL,
            image TEXT,
            status TEXT DEFAULT 'Open',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'Pending',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(item_id, user_id),
            FOREIGN KEY(item_id) REFERENCES items(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
    """)
    conn.commit()
    conn.close()


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            flash("Please login first.", "warning")
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


@app.route("/")
def index():
    search = request.args.get("search", "").strip()
    category = request.args.get("category", "").strip()
    item_type = request.args.get("type", "").strip()

    conn = get_db()
    sql = """
        SELECT items.*, users.name AS owner
        FROM items JOIN users ON items.user_id = users.id
        WHERE items.status = 'Open'
    """
    params = []

    if search:
        sql += " AND (items.title LIKE ? OR items.description LIKE ? OR items.location LIKE ?)"
        word = f"%{search}%"
        params.extend([word, word, word])

    if category in CATEGORIES:
        sql += " AND items.category = ?"
        params.append(category)

    if item_type in ("Lost", "Found"):
        sql += " AND items.item_type = ?"
        params.append(item_type)

    sql += " ORDER BY items.created_at DESC"
    items = conn.execute(sql, params).fetchall()

    total_lost = conn.execute(
        "SELECT COUNT(*) FROM items WHERE item_type='Lost'"
    ).fetchone()[0]

    total_found = conn.execute(
        "SELECT COUNT(*) FROM items WHERE item_type='Found'"
    ).fetchone()[0]

    # Smart matching: compare category and meaningful words
    matches = []
    lost_items = conn.execute(
        "SELECT * FROM items WHERE item_type='Lost' AND status='Open'"
    ).fetchall()
    found_items = conn.execute(
        "SELECT * FROM items WHERE item_type='Found' AND status='Open'"
    ).fetchall()

    for lost in lost_items:
        for found in found_items:
            if lost["user_id"] == found["user_id"]:
                continue

            score = 0
            if lost["category"] == found["category"]:
                score += 40

            if lost["location"].lower() == found["location"].lower():
                score += 20

            lost_words = set(lost["title"].lower().split())
            found_words = set(found["title"].lower().split())
            common = lost_words & found_words
            score += min(len(common) * 15, 30)

            if score >= 40:
                matches.append({
                    "lost": lost,
                    "found": found,
                    "score": min(score, 100)
                })

    matches.sort(key=lambda x: x["score"], reverse=True)
    conn.close()

    return render_template(
        "index.html",
        items=items,
        categories=CATEGORIES,
        search=search,
        category=category,
        item_type=item_type,
        total_lost=total_lost,
        total_found=total_found,
        matches=matches[:4]
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not name or not email or len(password) < 6:
            flash("Enter all details. Password must be at least 6 characters.", "danger")
            return redirect(url_for("register"))

        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (name, email, password) VALUES (?, ?, ?)",
                (name, email, generate_password_hash(password))
            )
            conn.commit()
            flash("Registration successful! Please login.", "success")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("This email is already registered.", "danger")
        finally:
            conn.close()

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["name"] = user["name"]
            flash("Welcome back, " + user["name"] + "!", "success")
            return redirect(url_for("index"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "info")
    return redirect(url_for("index"))


@app.route("/post", methods=["GET", "POST"])
@login_required
def post_item():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        description = request.form.get("description", "").strip()
        category = request.form.get("category", "")
        item_type = request.form.get("item_type", "")
        location = request.form.get("location", "").strip()

        if not all([title, description, category, item_type, location]):
            flash("Please fill in all required fields.", "danger")
            return redirect(url_for("post_item"))

        if category not in CATEGORIES or item_type not in ("Lost", "Found"):
            abort(400)

        image_name = None
        file = request.files.get("image")

        if file and file.filename:
            extension = os.path.splitext(file.filename)[1].lower()
            if extension not in (".png", ".jpg", ".jpeg", ".webp"):
                flash("Only PNG, JPG and WEBP images are allowed.", "danger")
                return redirect(url_for("post_item"))

            image_name = secrets.token_hex(16) + extension
            file.save(os.path.join(UPLOAD_FOLDER, image_name))

        conn = get_db()
        conn.execute("""
            INSERT INTO items
            (user_id, title, description, category, item_type, location, image)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            session["user_id"], title, description,
            category, item_type, location, image_name
        ))
        conn.commit()
        conn.close()

        flash("Your item has been reported successfully!", "success")
        return redirect(url_for("index"))

    return render_template("post_item.html", categories=CATEGORIES)


@app.route("/item/<int:item_id>")
def item_detail(item_id):
    conn = get_db()
    item = conn.execute("""
        SELECT items.*, users.name AS owner
        FROM items JOIN users ON items.user_id = users.id
        WHERE items.id = ?
    """, (item_id,)).fetchone()
    conn.close()

    if not item:
        abort(404)

    return render_template("item_detail.html", item=item)

@app.route("/delete_item/<int:item_id>", methods=["POST"])
@login_required
def delete_item(item_id):
    conn = get_db()

    item = conn.execute(
        "SELECT * FROM items WHERE id = ?",
        (item_id,)
    ).fetchone()

    if not item:
        conn.close()
        abort(404)

    if item["user_id"] != session["user_id"]:
        conn.close()
        abort(403)

    conn.execute(
        "DELETE FROM items WHERE id = ?",
        (item_id,)
    )

    conn.commit()
    conn.close()

    flash("Item deleted successfully.", "success")
    return redirect(url_for("dashboard"))
@app.route("/claim/<int:item_id>", methods=["POST"])
@login_required
def claim_item(item_id):
    message = request.form.get("message", "").strip()

    if not message:
        flash("Please enter a message.", "danger")
        return redirect(url_for("item_detail", item_id=item_id))

    conn = get_db()
    item = conn.execute(
        "SELECT * FROM items WHERE id=?", (item_id,)
    ).fetchone()

    if not item or item["status"] != "Open":
        conn.close()
        flash("This item is no longer available.", "warning")
        return redirect(url_for("index"))

    if item["user_id"] == session["user_id"]:
        conn.close()
        flash("You cannot claim your own report.", "warning")
        return redirect(url_for("item_detail", item_id=item_id))

    try:
        conn.execute("""
            INSERT INTO claims (item_id, user_id, message)
            VALUES (?, ?, ?)
        """, (item_id, session["user_id"], message))
        conn.commit()
        flash("Your claim request has been sent!", "success")
    except sqlite3.IntegrityError:
        flash("You have already submitted a claim for this item.", "warning")
    finally:
        conn.close()

    return redirect(url_for("item_detail", item_id=item_id))


@app.route("/dashboard")
@login_required
def dashboard():
    conn = get_db()

    my_items = conn.execute("""
        SELECT * FROM items
        WHERE user_id = ? ORDER BY created_at DESC
    """, (session["user_id"],)).fetchall()

    my_claims = conn.execute("""
        SELECT claims.*, items.title AS item_title, items.item_type
        FROM claims
        JOIN items ON claims.item_id = items.id
        WHERE claims.user_id = ?
        ORDER BY claims.created_at DESC
    """, (session["user_id"],)).fetchall()

    received_claims = conn.execute("""
        SELECT claims.*, items.title AS item_title,
               users.name AS student_name
        FROM claims
        JOIN items ON claims.item_id = items.id
        JOIN users ON claims.user_id = users.id
        WHERE items.user_id = ?
        ORDER BY claims.created_at DESC
    """, (session["user_id"],)).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        my_items=my_items,
        my_claims=my_claims,
        received_claims=received_claims
    )

@app.route("/claim/<int:claim_id>/update", methods=["POST"])
@login_required
def update_claim(claim_id):
    status = request.form.get("status")

    if status not in ("Approved", "Rejected"):
        abort(400)

    conn = get_db()
    claim = conn.execute("""
        SELECT claims.item_id, items.user_id, items.status AS item_status
        FROM claims JOIN items ON claims.item_id = items.id
        WHERE claims.id = ?
    """, (claim_id,)).fetchone()

    if not claim or claim["user_id"] != session["user_id"]:
        conn.close()
        abort(403)

    if claim["item_status"] != "Open":
        conn.close()
        flash("This item has already been closed.", "warning")
        return redirect(url_for("dashboard"))

    conn.execute(
        "UPDATE claims SET status=? WHERE id=?",
        (status, claim_id)
    )

    if status == "Approved":
        conn.execute(
            "UPDATE items SET status='Closed' WHERE id=?",
            (claim["item_id"],)
        )
        conn.execute("""
            UPDATE claims SET status='Rejected'
            WHERE item_id=? AND id != ? AND status='Pending'
        """, (claim["item_id"], claim_id))

    conn.commit()
    conn.close()

    flash("Claim updated successfully.", "success")
    return redirect(url_for("dashboard"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
