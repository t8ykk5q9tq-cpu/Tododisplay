import os
import sqlite3
from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "lists.db")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Live-reload: enabled when LIVE_RELOAD=1 in the environment.
LIVE_RELOAD = os.environ.get("LIVE_RELOAD") == "1"


def static_version():
    """Return a fingerprint of the frontend files based on their newest mtime.
    The browser polls this; when it changes, the page reloads itself."""
    latest = 0.0
    for root, _dirs, files in os.walk(STATIC_DIR):
        for name in files:
            try:
                mtime = os.path.getmtime(os.path.join(root, name))
                if mtime > latest:
                    latest = mtime
            except OSError:
                pass
    return str(latest)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_type TEXT NOT NULL CHECK(list_type IN ('todo', 'shopping')),
            text TEXT NOT NULL,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


# --- API Routes ---


@app.route("/api/items/<list_type>", methods=["GET"])
def get_items(list_type):
    if list_type not in ("todo", "shopping"):
        return jsonify({"error": "Invalid list type"}), 400
    conn = get_db()
    rows = conn.execute(
        "SELECT id, text, done FROM items WHERE list_type = ? ORDER BY done ASC, created_at DESC",
        (list_type,),
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.route("/api/items/<list_type>", methods=["POST"])
def add_item(list_type):
    if list_type not in ("todo", "shopping"):
        return jsonify({"error": "Invalid list type"}), 400
    data = request.get_json()
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "Text is required"}), 400
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO items (list_type, text) VALUES (?, ?)", (list_type, text)
    )
    conn.commit()
    item_id = cursor.lastrowid
    conn.close()
    return jsonify({"id": item_id, "text": text, "done": 0}), 201


@app.route("/api/items/<int:item_id>/toggle", methods=["PATCH"])
def toggle_item(item_id):
    conn = get_db()
    conn.execute("UPDATE items SET done = 1 - done WHERE id = ?", (item_id,))
    conn.commit()
    row = conn.execute("SELECT id, text, done FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    if row is None:
        return jsonify({"error": "Item not found"}), 404
    return jsonify(dict(row))


@app.route("/api/items/<int:item_id>", methods=["DELETE"])
def delete_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/items/<list_type>/clear-done", methods=["DELETE"])
def clear_done(list_type):
    if list_type not in ("todo", "shopping"):
        return jsonify({"error": "Invalid list type"}), 400
    conn = get_db()
    conn.execute("DELETE FROM items WHERE list_type = ? AND done = 1", (list_type,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# --- Live Reload ---


@app.route("/api/version")
def version():
    """Frontend polls this to detect when static files change."""
    return jsonify({"version": static_version(), "live_reload": LIVE_RELOAD})


# --- Serve Frontend ---


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=LIVE_RELOAD, use_reloader=LIVE_RELOAD)
