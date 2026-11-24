import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import closing
from hmac import compare_digest
from time import time
from uuid import uuid4

from app.core.config import settings

SESSION_COOKIE = "mde_session"


def _connect():
    connection = sqlite3.connect(settings.auth_database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return closing(connection)


def initialize_accounts() -> None:
    os.close(os.open(settings.auth_database_path, os.O_CREAT | os.O_WRONLY, 0o600))
    with _connect() as connection, connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
                expires_at INTEGER NOT NULL
            );
        """)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+", email):
        raise ValueError("Enter a valid email address.")
    return email


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 1024**2)


def create_account(email: str, password: str, *, role: str = "intake") -> str:
    email = normalize_email(email)
    if not 12 <= len(password) <= 128:
        raise ValueError("Use a password between 12 and 128 characters.")
    account_id = str(uuid4())
    salt = secrets.token_bytes(16)
    verifier = _hash_password(password, salt)
    with _connect() as connection, connection:
        connection.execute(
            "INSERT INTO accounts (id, email, password_salt, password_hash, role) VALUES (?, ?, ?, ?, ?)",
            (account_id, email, salt, verifier, role),
        )
    return account_id


def authenticate(email: str, password: str) -> str | None:
    with _connect() as connection:
        account = connection.execute(
            "SELECT id, password_salt, password_hash FROM accounts WHERE email = ?",
            (email.strip().lower(),),
        ).fetchone()
    salt = account["password_salt"] if account else bytes(16)
    expected = account["password_hash"] if account else bytes(64)
    valid = compare_digest(_hash_password(password, salt), expected)
    return account["id"] if valid and account else None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(account_id: str, previous_token: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = int(time())
    with _connect() as connection, connection:
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        if previous_token:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(previous_token),))
        connection.execute(
            "INSERT INTO sessions (token_hash, account_id, expires_at) VALUES (?, ?, ?)",
            (_token_hash(token), account_id, now + settings.session_minutes * 60),
        )
    return token


def session_account(token: str) -> sqlite3.Row | None:
    with _connect() as connection:
        return connection.execute(
            """SELECT accounts.id, accounts.role
               FROM sessions JOIN accounts ON accounts.id = sessions.account_id
               WHERE sessions.token_hash = ? AND sessions.expires_at > ?""",
            (_token_hash(token), int(time())),
        ).fetchone()


def revoke_session(token: str) -> None:
    with _connect() as connection, connection:
        connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
