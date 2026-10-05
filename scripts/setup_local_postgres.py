"""Start a project-owned PostgreSQL cluster, or configure an existing local server.

Run with ``python -m scripts.setup_local_postgres``. Credentials and database
files stay in ignored local paths. ``--existing`` prompts for credentials;
``--stop`` stops only the cluster owned by this project.
"""

from __future__ import annotations

import argparse
from collections import Counter
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

import psycopg
from psycopg import sql
from dotenv import dotenv_values, set_key


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data" / "processed" / "local-postgres"
CLUSTER = RUNTIME / "cluster"
METADATA = RUNTIME / "connection.json"
SCHEMA = "app_private"
TABLES = (
    "auth_users", "auth_email_tokens", "auth_sessions", "memory_messages",
    "memory_facts", "model_settings", "user_model_settings", "chat_conversations",
    "chat_messages", "career_preferences", "career_profile", "career_jobs",
    "career_score_batches", "career_score_tasks", "usage_events", "bi_datasets", "analytics_events",
)
IDENTITY_TABLES = ("memory_messages", "chat_messages", "usage_events")


def binary_directory(override: str | None = None) -> Path:
    candidates = [Path(override)] if override else []
    installed = shutil.which("pg_ctl")
    if installed:
        candidates.append(Path(installed).parent)
    if os.name == "nt":
        candidates.extend(sorted(
            (Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PostgreSQL").glob("*/bin"),
            reverse=True,
        ))
    suffix = ".exe" if os.name == "nt" else ""
    for candidate in candidates:
        if all((candidate / f"{name}{suffix}").is_file() for name in ("pg_ctl", "initdb")):
            return candidate.resolve()
    raise RuntimeError("Install PostgreSQL and put its bin directory on PATH, or pass --bin-dir.")


def run(command: list[str], *, check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    # A detached Windows postgres process can inherit stdout. A file avoids
    # waiting for EOF on a pipe that stays open for the lifetime of the server.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            command, cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            timeout=60,
        )
        output.seek(0)
        result.stdout = output.read().decode("utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(result.stdout.strip() or "PostgreSQL command failed.")
    return result


def connection_url(config: dict, *, admin: bool = False) -> str:
    username = "postgres" if admin else config["username"]
    password = config["admin_password"] if admin else config["password"]
    database = "postgres" if admin else config["database"]
    host = config["host"]
    if ":" in host:
        host = f"[{host}]"
    return f"postgresql://{quote(username, safe='')}:{quote(password, safe='')}@{host}:{config['port']}/{quote(database, safe='')}"


def local_cluster(args: argparse.Namespace) -> dict:
    binaries = binary_directory(args.bin_dir)
    suffix = ".exe" if os.name == "nt" else ""
    pg_ctl = str(binaries / f"pg_ctl{suffix}")
    if args.stop:
        if (CLUSTER / "PG_VERSION").is_file():
            run([pg_ctl, "-D", str(CLUSTER), "-w", "-t", "30", "-m", "fast", "stop"])
        print("Project PostgreSQL stopped; data retained.")
        return {}

    if METADATA.is_file():
        config = json.loads(METADATA.read_text(encoding="utf-8"))
        if args.port is not None and args.port != config["port"]:
            raise RuntimeError("The project database already has a port configured. Reuse that port.")
    else:
        if CLUSTER.exists() and any(CLUSTER.iterdir()):
            raise RuntimeError("Existing cluster has no connection metadata; refusing to replace it.")
        config = {
            "host": "127.0.0.1", "port": args.port or 5433,
            "username": "ai_platform", "database": "ai_platform_local",
            "password": secrets.token_urlsafe(32), "admin_password": secrets.token_urlsafe(32),
        }
        RUNTIME.mkdir(parents=True, exist_ok=True)
        METADATA.write_text(json.dumps(config), encoding="utf-8")
        METADATA.chmod(0o600)

    running = run([pg_ctl, "-D", str(CLUSTER), "status"], check=False).returncode == 0
    if not running:
        with socket.socket() as probe:
            try:
                probe.bind((config["host"], config["port"]))
            except OSError as exc:
                raise RuntimeError(f"Port {config['port']} is already in use by another server.") from exc
        if not (CLUSTER / "PG_VERSION").is_file():
            password_file = RUNTIME / "init-password.tmp"
            password_file.write_text(config["admin_password"] + "\n", encoding="utf-8")
            password_file.chmod(0o600)
            try:
                run([
                    str(binaries / f"initdb{suffix}"), "-D", str(CLUSTER),
                    "-U", "postgres", "--pwfile", str(password_file),
                    "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C",
                ])
            finally:
                password_file.unlink(missing_ok=True)
            with (CLUSTER / "postgresql.conf").open("a", encoding="utf-8") as handle:
                handle.write(f"\nlisten_addresses = '127.0.0.1'\nport = {config['port']}\nmax_connections = 30\nshared_buffers = '64MB'\n")
        run([pg_ctl, "-D", str(CLUSTER), "-l", str(RUNTIME / "postgres.log"), "-w", "-t", "30", "start"])

    with psycopg.connect(connection_url(config, admin=True), sslmode="disable", autocommit=True, connect_timeout=5) as connection:
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (config["username"],)).fetchone():
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(
                sql.Identifier(config["username"]), sql.Literal(config["password"]),
            ))
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config["database"],)).fetchone():
            connection.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(config["database"]), sql.Identifier(config["username"]),
            ))
    return config


def existing_connection(args: argparse.Namespace) -> dict:
    host = input("PostgreSQL host [127.0.0.1]: ").strip() or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("This setup command only configures PostgreSQL on this computer.")
    return {
        "host": host,
        "port": args.port or int(input("PostgreSQL port [5432]: ").strip() or "5432"),
        "database": input("Existing development database [ai_platform_local]: ").strip() or "ai_platform_local",
        "username": input("PostgreSQL username [ai_platform]: ").strip() or "ai_platform",
        "password": getpass.getpass("PostgreSQL password: "),
    }


def initialize_schema(url: str) -> None:
    environment = dict(os.environ, DATABASE_URL=url, DATABASE_SCHEMA=SCHEMA,
                       DATABASE_SSLMODE="disable", DATABASE_AUTO_MIGRATE="true",
                       APP_ENV="development", AI_RUNTIME="local")
    run([sys.executable, "-c", "from services.storage.sqlite_service import db; assert db.backend == 'postgresql'; db.close()"], env=environment)


def copy_sqlite(url: str, source: Path, *, archive_stale_tasks: bool = False) -> dict[str, int]:
    if not source.is_file():
        return {}
    with psycopg.connect(url, sslmode="disable", connect_timeout=5) as target:
        target.execute(sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(SCHEMA)))
        target.execute("SELECT pg_advisory_xact_lock(4139188002)")
        if any(target.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))).fetchone()[0] for table in TABLES):
            print("PostgreSQL already contains application records; SQLite copy skipped.")
            return {}
        RUNTIME.mkdir(parents=True, exist_ok=True)
        backup = RUNTIME / f"sqlite-before-postgres-{time.time_ns()}.db"
        with sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True) as original:
            with sqlite3.connect(backup) as snapshot:
                original.backup(snapshot)
        counts = {}
        with sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True) as snapshot:
            available = {row[0] for row in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in TABLES:
                if table not in available:
                    continue
                cursor = snapshot.execute(f'SELECT * FROM "{table}"')
                columns = [item[0] for item in cursor.description]
                rows = cursor.fetchall()
                if table == "career_score_tasks" and archive_stale_tasks:
                    jobs = {row[0] for row in target.execute("SELECT id FROM career_jobs")}
                    batches = {row[0] for row in target.execute("SELECT id FROM career_score_batches")}
                    job_index, batch_index = columns.index("job_id"), columns.index("batch_id")
                    stale = [row for row in rows if row[job_index] not in jobs or row[batch_index] not in batches]
                    if stale:
                        archive = RUNTIME / f"stale-score-tasks-{time.time_ns()}.json"
                        archive.write_text(json.dumps([dict(zip(columns, row)) for row in stale], indent=2), encoding="utf-8")
                        rows = [row for row in rows if row[job_index] in jobs and row[batch_index] in batches]
                        print(f"Archived {len(stale)} legacy scoring tasks with missing references; SQLite originals retained.")
                if rows:
                    statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)),
                        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
                    )
                    with target.cursor() as writer:
                        writer.executemany(statement, rows)
                restored = target.execute(sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(", ").join(map(sql.Identifier, columns)), sql.Identifier(table),
                )).fetchall()
                if Counter(rows) != Counter(restored):
                    raise RuntimeError(f"Verification failed for {table}; PostgreSQL copy rolled back.")
                counts[table] = len(rows)
        for table in IDENTITY_TABLES:
            maximum = target.execute(sql.SQL("SELECT MAX(id) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            target.execute("SELECT setval(pg_get_serial_sequence(%s, 'id'), %s, %s)",
                           (f"{SCHEMA}.{table}", max(maximum or 0, 1), maximum is not None))
        return counts


def write_environment(url: str) -> None:
    path = ROOT / ".env"
    path.touch(exist_ok=True)
    for key, value in {
        "DATABASE_URL": url, "DATABASE_SCHEMA": SCHEMA,
        "DATABASE_SSLMODE": "disable", "DATABASE_AUTO_MIGRATE": "false",
    }.items():
        set_key(str(path), key, value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--existing", action="store_true", help="Prompt for an existing local development database")
    modes.add_argument("--stop", action="store_true", help="Stop only this project's PostgreSQL cluster")
    parser.add_argument("--bin-dir", help="Directory containing PostgreSQL initdb and pg_ctl")
    parser.add_argument("--port", type=int)
    parser.add_argument("--archive-stale-tasks", action="store_true", help="Archive legacy scoring tasks referencing deleted jobs or batches")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    config = None
    try:
        config = existing_connection(args) if args.existing else local_cluster(args)
        if args.stop:
            return 0
        url = connection_url(config)
        with psycopg.connect(url if args.existing else connection_url(config, admin=True),
                             sslmode="disable", connect_timeout=5) as connection:
            if args.existing and connection.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
                raise RuntimeError("Use a dedicated application role without superuser privileges for the existing database.")
        initialize_schema(url)
        previous = dotenv_values(ROOT / ".env")
        source = Path(previous.get("SQLITE_PATH") or "data/processed/app.db")
        counts = copy_sqlite(url, source if source.is_absolute() else ROOT / source,
                             archive_stale_tasks=args.archive_stale_tasks)
        with psycopg.connect(url, sslmode="disable", connect_timeout=5) as connection:
            assert connection.execute("SELECT 1").fetchone() == (1,)
        write_environment(url)
        print(f"PostgreSQL ready at {config['host']}:{config['port']}/{config['database']}.")
        if counts:
            print(f"Copied and verified {sum(counts.values())} SQLite records across {len(counts)} tables.")
            print(f"Preserved {counts.get('chat_conversations', 0)} conversations, {counts.get('chat_messages', 0)} chat messages, and {counts.get('bi_datasets', 0)} datasets.")
        print("Updated ignored .env with PostgreSQL settings. Credentials were not printed.")
        return 0
    except Exception as exc:
        message = str(exc)
        if config is None and METADATA.is_file():
            config = json.loads(METADATA.read_text(encoding="utf-8"))
        if config:
            for key in ("password", "admin_password"):
                if config.get(key):
                    message = message.replace(config[key], "[redacted]")
        print(f"PostgreSQL setup failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
