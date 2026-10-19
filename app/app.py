import os

import psycopg2
from psycopg2 import errors
from flask import Flask, jsonify, request

app = Flask(__name__)
_schema_ready = False


def get_conn():
    global _schema_ready
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        connect_timeout=3,
    )
    if not _schema_ready:
        try:
            with conn, conn.cursor() as cur:
                # Lock so two replicas starting together don't race to create the table
                cur.execute("SELECT pg_advisory_xact_lock(42)")
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS users ("
                    "id SERIAL PRIMARY KEY, "
                    "name TEXT NOT NULL, "
                    "email TEXT NOT NULL UNIQUE)"
                )
        except Exception:
            conn.close()
            raise
        _schema_ready = True
    return conn


def run(sql, params=(), fetch=None):
    conn = get_conn()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return cur.rowcount
    finally:
        conn.close()


def to_user(row):
    return {"id": row[0], "name": row[1], "email": row[2]}


def read_body():
    data = request.get_json(silent=True) or {}
    return (data.get("name") or "").strip(), (data.get("email") or "").strip()


@app.get("/healthz")
def healthz():
    # Liveness: no database check, only "is the process running?"
    return jsonify(status="alive"), 200


@app.get("/ready")
def ready():
    # Readiness: 200 only if the database answers
    try:
        run("SELECT 1", fetch="one")
        return jsonify(status="ready"), 200
    except Exception as exc:
        return jsonify(status="database unavailable", error=str(exc)), 503


@app.get("/users")
def list_users():
    rows = run("SELECT id, name, email FROM users ORDER BY id", fetch="all")
    return jsonify([to_user(r) for r in rows])


@app.post("/users")
def create_user():
    name, email = read_body()
    if not name or not email:
        return jsonify(error="name and email are required"), 400
    try:
        row = run(
            "INSERT INTO users (name, email) VALUES (%s, %s) RETURNING id, name, email",
            (name, email),
            fetch="one",
        )
    except errors.UniqueViolation:
        return jsonify(error="email already exists"), 409
    return jsonify(to_user(row)), 201


@app.get("/users/<int:user_id>")
def get_user(user_id):
    row = run("SELECT id, name, email FROM users WHERE id = %s", (user_id,), fetch="one")
    if row is None:
        return jsonify(error="user not found"), 404
    return jsonify(to_user(row))


@app.put("/users/<int:user_id>")
def update_user(user_id):
    name, email = read_body()
    if not name or not email:
        return jsonify(error="name and email are required"), 400
    try:
        row = run(
            "UPDATE users SET name = %s, email = %s WHERE id = %s RETURNING id, name, email",
            (name, email, user_id),
            fetch="one",
        )
    except errors.UniqueViolation:
        return jsonify(error="email already exists"), 409
    if row is None:
        return jsonify(error="user not found"), 404
    return jsonify(to_user(row))


@app.delete("/users/<int:user_id>")
def delete_user(user_id):
    if run("DELETE FROM users WHERE id = %s", (user_id,)) == 0:
        return jsonify(error="user not found"), 404
    return "", 204