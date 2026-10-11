#!/usr/bin/env python3
"""
Clear the ticket data for a new round of testing.

Removes the requests of the two ticket tables (request_queue and
transaction_history) so that testing can start again from an empty
list. Completed requests are kept exactly as they are, together with
their history. Nothing else is touched: console accounts, roles,
settings, credentials, the audit trail, evidence folders and logs stay.

The script finds the database the application is connected to from the
application's own settings (the same files and credential store the
service reads), so it works with SQLite and with PostgreSQL without
being told which one is in use.

Run it from the application folder, as the account that runs the
service, with the service stopped:

  # 1. Show what would be removed. Nothing is changed.
  .\\.venv\\Scripts\\python.exe scripts\\clear_ticket_data.py

  # 2. Remove it (asks for a confirmation first).
  .\\.venv\\Scripts\\python.exe scripts\\clear_ticket_data.py --apply

  # Also keep requests that need verification and those already removed:
  .\\.venv\\Scripts\\python.exe scripts\\clear_ticket_data.py --apply --keep completed,needs_verification,already_removed

  # PostgreSQL where the application login may not delete (the database
  # was set up with an administrator address): sign in as the
  # administrator for this one run. The password is asked for.
  .\\.venv\\Scripts\\python.exe scripts\\clear_ticket_data.py --apply --admin-url 'postgresql://postgres@localhost:5432/postgres'

  # Report again the tickets the bot refused earlier (ServiceNow):
  .\\.venv\\Scripts\\python.exe scripts\\clear_ticket_data.py --apply --reset-ticket-memory

Statuses for --keep: completed, needs_verification, already_removed,
failed, cancelled, queued, in_progress. The default is completed.

Safety
  - Without --apply nothing is changed.
  - The script stops when the service is running or a request is in
    progress.
  - The rows that are removed are first written to a backup file
    (passwords left out) in the backups folder of the data folder.
  - Both tables are cleared in one transaction: everything or nothing.
  - Each run is noted in ticket_data_reset.log in the log folder.

Exit code 0: done (or nothing to do). 1: a problem. 2: stopped for
safety, nothing was changed.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]

QUEUE_TABLE = "request_queue"
HISTORY_TABLE = "transaction_history"
POLLER_STATE_FILE = "servicenow-poller.json"
PENDING_FILE = "pending_completions.jsonl"
RUN_LOG = "ticket_data_reset.log"
CONFIRM_WORD = "DELETE"

# The statuses as the console shows them, each with the stored values
# it stands for. "completed" and "needs_verification" are the two halves
# of the stored status "done".
_UNCONFIRMED = "UPPER(COALESCE(result_code, '')) = 'UNCONFIRMED'"
STATUS_SQL = {
    "completed": f"(status = 'done' AND NOT {_UNCONFIRMED})",
    "needs_verification": f"(status = 'done' AND {_UNCONFIRMED})",
    "already_removed": "(status = 'already_deleted')",
    "failed": "(status = 'failed')",
    "cancelled": "(status = 'cancelled')",
    "queued": "(status = 'queued')",
    "in_progress": "(status = 'running')",
}
STATUS_LABELS = {
    "completed": "Completed",
    "needs_verification": "Needs verification",
    "already_removed": "Already removed",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "queued": "Queued",
    "in_progress": "In progress",
    "other": "Other",
}
# Other spellings accepted for --keep.
STATUS_ALIASES = {"unconfirmed": "needs_verification",
                  "already_deleted": "already_removed",
                  "running": "in_progress", "done": "completed"}
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


class Stop(Exception):
    """Ends the run with a message and an exit code; nothing was changed."""

    def __init__(self, message: str, code: int = 1):
        """Keep the message and the exit code."""
        super().__init__(message)
        self.code = code


def say(status: str, name: str, detail: str = "") -> None:
    """Print one result line."""
    print(f"  [{status:<7}] {name}" + (f": {detail}" if detail else ""),
          flush=True)


def step(title: str) -> None:
    """Print a step heading."""
    print(f"\n=== {title}", flush=True)


# ---------------------------------------------------------------------
#  The database the application is connected to
# ---------------------------------------------------------------------
class Connection:
    """One open connection to the application database.

    The same small interface for both engines. Everything runs in one
    transaction that is committed or rolled back by the caller.
    """

    def __init__(self, engine: str, raw, shown: str):
        """Wrap an open driver connection.

        Args:
            engine: "sqlite" or "postgres".
            raw: The driver's connection.
            shown: The database in plain words, without a password.
        """
        self.engine = engine
        self.raw = raw
        self.shown = shown

    def query(self, sql: str) -> list[dict]:
        """Run a query and return its rows as dicts."""
        cur = self.raw.execute(sql)
        return [dict(row) for row in cur.fetchall()]

    def execute(self, sql: str) -> int:
        """Run a statement and return the number of rows it changed."""
        return self.raw.execute(sql).rowcount

    def table_exists(self, name: str) -> bool:
        """Return True if the table exists in the application database."""
        if self.engine == "postgres":
            rows = self.query(
                f"SELECT to_regclass('public.{name}') AS t")
            return bool(rows and rows[0]["t"])
        rows = self.query("SELECT name FROM sqlite_master WHERE "
                          f"type = 'table' AND name = '{name}'")
        return bool(rows)

    def commit(self) -> None:
        """Make the changes permanent."""
        self.raw.commit()

    def rollback(self) -> None:
        """Take the changes back."""
        try:
            self.raw.rollback()
        except Exception:
            pass

    def close(self) -> None:
        """Close the connection."""
        try:
            self.raw.close()
        except Exception:
            pass


def load_configuration():
    """Return the application configuration, read as the service reads it.

    Stored credentials are loaded first, so a database password kept in
    the credential store of this account is found.

    Raises:
        Stop: No settings name a database, or the settings are not valid.
    """
    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from desktop.vault import load_secrets_into_env
        load_secrets_into_env()
    except Exception as e:
        say("WARN", "Credential store", f"not consulted ({e})")
    try:
        from config import get_config
        from config.base import deployment_env_files
    except ImportError as e:
        raise Stop(f"The application code was not found ({e}). Keep this "
                   f"script in the scripts folder of the application and "
                   f"run it with the application's Python: "
                   f".\\.venv\\Scripts\\python.exe "
                   f"scripts\\clear_ticket_data.py") from None
    files = [p for p in deployment_env_files() if os.path.isfile(p)]
    named = [k for k in ("DATABASE_ENGINE", "DATABASE_URL", "SQLITE_PATH")
             if os.environ.get(k)]
    if not files and not named:
        # Without settings the built-in development address would be
        # used, which is not the database of this installation.
        raise Stop("No settings file (.env or finesiii.env) was found for "
                   "this application folder, so the connected database is "
                   "not known. Run the script from the installed "
                   "application folder.", 2)
    try:
        cfg = get_config()
    except Exception as e:
        raise Stop(f"The application settings could not be read: {e}") \
            from None
    for path in files:
        say("OK", "Settings file", str(path))
    return cfg


def _address(url: str) -> dict:
    """Return the parts of a PostgreSQL address.

    Raises:
        Stop: The address is not a complete postgresql:// URL.
    """
    parts = urlsplit((url or "").strip().strip('"').strip("'"))
    if parts.scheme not in ("postgresql", "postgres") or not parts.hostname:
        raise Stop("A PostgreSQL address must look like "
                   "postgresql://<login>@<host>:5432/<database>")
    try:
        port = parts.port or 5432
    except ValueError:
        raise Stop("The port in the PostgreSQL address is not a number.") \
            from None
    return {"user": unquote(parts.username or ""),
            "password": (unquote(parts.password)
                         if parts.password is not None else ""),
            "host": parts.hostname, "port": port,
            "database": unquote(parts.path.lstrip("/"))}


def _same_server(a: dict, b: dict) -> bool:
    """True when two addresses name the same server and port."""
    def host(value: str) -> str:
        """Return the host name with every local spelling made equal."""
        value = value.lower()
        return "localhost" if value in _LOCAL_HOSTS else value
    return host(a["host"]) == host(b["host"]) and a["port"] == b["port"]


def _explain(error: Exception, login: str) -> str:
    """Return a database error with a hint in plain words, if known."""
    text = " ".join(str(error).split()) or type(error).__name__
    low = text.lower()
    if "password authentication failed" in low:
        hint = f"The password of '{login}' is not correct."
    elif "no password supplied" in low:
        hint = (f"No password was found for '{login}'. Run the script as "
                f"the account that runs the service (its credential store "
                f"holds the password), or set DATABASE_PASSWORD in this "
                f"window first.")
    elif "does not exist" in low:
        hint = "Check the database and login names in the settings."
    elif any(w in low for w in ("refused", "timeout", "timed out",
                                "could not", "unreachable")):
        hint = ("The database server cannot be reached. Check that "
                "PostgreSQL is running.")
    else:
        hint = ""
    return text[:220] + (f"  ->  {hint}" if hint else "")


def connect(cfg, admin_url: str, timeout: int) -> Connection:
    """Open the database the application is connected to.

    Args:
        cfg: The application configuration.
        admin_url: Optional administrator address (PostgreSQL only). It
            must name the same server; the application's database is
            always the one opened.
        timeout: Connection time limit in seconds.

    Raises:
        Stop: The database cannot be opened.
    """
    if cfg.database_engine == "sqlite":
        if admin_url:
            raise Stop("--admin-url is for PostgreSQL; the application is "
                       "connected to a SQLite file.", 2)
        path = Path(cfg.sqlite_path)
        if not path.is_file():
            raise Stop(f"The SQLite database file {path} does not exist.")
        import sqlite3
        raw = sqlite3.connect(str(path), timeout=timeout)
        raw.row_factory = sqlite3.Row
        # The write lock is taken at once and held to the end, so a
        # service that is still writing is noticed here.
        raw.isolation_level = None
        try:
            raw.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            raw.close()
            raise Stop(f"The SQLite database is in use ({e}). Stop the "
                       f"service first.", 2) from None
        return Connection("sqlite", raw, f"SQLite file {path}")

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        raise Stop("The PostgreSQL driver is not installed in this Python. "
                   "Run the script with the application's Python: "
                   ".\\.venv\\Scripts\\python.exe "
                   "scripts\\clear_ticket_data.py") from None
    app = _address(cfg.database_url)
    who = app
    if admin_url:
        who = _address(admin_url)
        if not _same_server(who, app):
            raise Stop(f"--admin-url names {who['host']}:{who['port']}, but "
                       f"the application is connected to "
                       f"{app['host']}:{app['port']}. Use the administrator "
                       f"address of the same server.", 2)
        if not who["password"]:
            who["password"] = os.environ.get("DATABASE_ADMIN_PASSWORD", "")
        if not who["password"] and sys.stdin.isatty():
            who["password"] = getpass.getpass(
                f"  Password of '{who['user']}' at {who['host']} "
                f"(not shown): ")
    keywords = {"host": who["host"], "port": who["port"],
                "user": who["user"], "dbname": app["database"],
                "connect_timeout": timeout, "row_factory": dict_row,
                "application_name": "finesiii-clear-ticket-data"}
    if who["password"]:
        keywords["password"] = who["password"]
    try:
        raw = psycopg.connect(**keywords)
    except Exception as e:
        raise Stop("The database could not be opened: "
                   + _explain(e, who["user"])) from None
    return Connection(
        "postgres", raw,
        f"PostgreSQL database '{app['database']}' on "
        f"{app['host']}:{app['port']}, signed in as '{who['user']}'")


def check_delete_rights(conn: Connection, tables: list[str]) -> None:
    """Stop when the login may not delete from the ticket tables.

    A database set up with an administrator address keeps the history
    append-only for the application login, so that login cannot clear it.

    Raises:
        Stop: A right is missing.
    """
    if conn.engine != "postgres":
        return
    missing = []
    for table in tables:
        row = conn.query(
            "SELECT has_table_privilege(current_user, "
            f"'public.{table}', 'DELETE') AS allowed")[0]
        if not row["allowed"]:
            missing.append(table)
    if missing:
        raise Stop(
            "This login is not allowed to delete from "
            + " and ".join(missing) + ": the database protects the "
            "history from the application login. Run again with the "
            "administrator address of the same server, for example  "
            "--admin-url 'postgresql://postgres@localhost:5432/postgres'",
            2)


# ---------------------------------------------------------------------
#  What is kept and what is removed
# ---------------------------------------------------------------------
def parse_keep(text: str) -> list[str]:
    """Return the statuses to keep from the --keep value.

    Raises:
        Stop: A status is not known.
    """
    keep = []
    for part in (text or "").replace(";", ",").split(","):
        name = part.strip().lower().replace(" ", "_").replace("-", "_")
        if not name:
            continue
        name = STATUS_ALIASES.get(name, name)
        if name not in STATUS_SQL:
            raise Stop(f"Unknown status '{part.strip()}' in --keep. Use: "
                       + ", ".join(STATUS_SQL), 2)
        if name not in keep:
            keep.append(name)
    return keep


def keep_clause(keep: list[str]) -> str:
    """Return the SQL condition that matches the rows to keep."""
    if not keep:
        return "(1 = 0)"
    return "(" + " OR ".join(STATUS_SQL[name] for name in keep) + ")"


def remove_clause(table: str, keep: list[str]) -> str:
    """Return the SQL condition that matches the rows to remove.

    A history row is kept when its own status is kept, and also when it
    belongs to a kept request, so a completed request keeps its whole
    history (an earlier failed attempt included).
    """
    kept = keep_clause(keep)
    if table == HISTORY_TABLE:
        return (f"NOT {kept} AND request_id NOT IN (SELECT request_id "
                f"FROM {QUEUE_TABLE} WHERE {kept})")
    return f"NOT {kept}"


def status_of(row: dict) -> str:
    """Return the shown status of a stored status and result code."""
    status = str(row.get("status") or "").strip().lower()
    unconfirmed = str(row.get("result_code") or "").strip().upper() \
        == "UNCONFIRMED"
    return {"done": "needs_verification" if unconfirmed else "completed",
            "already_deleted": "already_removed",
            "running": "in_progress"}.get(
                status, status if status in STATUS_SQL else "other")


def count_by_status(conn: Connection, table: str) -> dict:
    """Return {shown status: number of rows} of a ticket table."""
    counts: dict = {}
    for row in conn.query(
            f"SELECT status, result_code, COUNT(*) AS n FROM {table} "
            f"GROUP BY status, result_code"):
        key = status_of(row)
        counts[key] = counts.get(key, 0) + int(row["n"])
    return counts


def show_counts(conn: Connection, table: str, keep: list[str],
                with_queue: bool) -> int:
    """Print the rows of a table by status and return how many go.

    Args:
        conn: The open database.
        table: The ticket table.
        keep: The statuses to keep.
        with_queue: Whether the request table exists (the history
            condition reads it).
    """
    counts = count_by_status(conn, table)
    total = sum(counts.values())
    clause = remove_clause(table, keep) if with_queue or \
        table == QUEUE_TABLE else f"NOT {keep_clause(keep)}"
    going = int(conn.query(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}")[0]["n"])
    print(f"\n  {table}: {total} row(s), {total - going} kept, "
          f"{going} to remove")
    for key in list(STATUS_SQL) + ["other"]:
        if counts.get(key):
            action = "kept" if key in keep else "removed"
            print(f"      {STATUS_LABELS[key]:<20}{counts[key]:>7}   "
                  f"{action}")
    extra = (total - going) - sum(counts.get(k, 0) for k in keep)
    if extra > 0:
        print(f"      ({extra} more row(s) are kept: the history of kept "
              f"requests)")
    return going


def backup_rows(conn: Connection, tables: dict, folder: Path,
                stamp: str) -> Path:
    """Write the rows that will be removed to a backup file.

    One JSON object per line: {"table": ..., "row": {...}}. Passwords are
    left out.

    Args:
        conn: The open database.
        tables: {table: SQL condition of the rows to remove}.
        folder: The backup folder.
        stamp: Time stamp for the file name.

    Returns:
        The backup file.

    Raises:
        Stop: The file could not be written.
    """
    path = folder / f"ticket_data_{stamp}.jsonl"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for table, clause in tables.items():
                for row in conn.query(
                        f"SELECT * FROM {table} WHERE {clause}"):
                    if "password" in row:
                        row["password"] = ""
                    f.write(json.dumps({"table": table, "row": row},
                                       ensure_ascii=False, default=str)
                            + "\n")
        if os.name != "nt":
            os.chmod(path, 0o600)
    except OSError as e:
        raise Stop(f"The backup file {path} could not be written ({e}). "
                   f"Nothing was removed.") from None
    return path


# ---------------------------------------------------------------------
#  Files beside the database
# ---------------------------------------------------------------------
def service_is_running() -> bool:
    """True when the background service holds its single-instance lock."""
    try:
        from desktop import runtime
        from desktop.single_instance import SingleInstance
        return SingleInstance.is_held(runtime.data_path(runtime.LOCK_FILE))
    except Exception:
        return False


def remembered_tickets(path: Path) -> dict | None:
    """Return the saved ServiceNow poll state, or None if there is none."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def forget_tickets(path: Path, data: dict) -> None:
    """Empty the list of tickets already reported; keep everything else.

    The file is replaced in one step, so a reader never sees half of it.
    """
    data = dict(data)
    data["notified"] = {}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def note_run(folder: str, line: str) -> None:
    """Add one line about this run to the run log; never fails the run."""
    try:
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        with open(path / RUN_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        say("WARN", "Run log", f"not written ({e})")


# ---------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------
def parse_arguments(argv):
    """Parse the command line; the module docstring is the help text."""
    p = argparse.ArgumentParser(
        prog="clear_ticket_data.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="remove the rows (without it nothing is changed)")
    p.add_argument("--keep", default="completed", metavar="STATUSES",
                   help="statuses to keep, comma-separated (default: "
                        "completed)")
    p.add_argument("--admin-url", default="", metavar="URL",
                   help="PostgreSQL only: administrator address of the "
                        "same server, used when the application login may "
                        "not delete")
    p.add_argument("--reset-ticket-memory", action="store_true",
                   help="also forget which ServiceNow tickets were already "
                        "reported as refused, so they are reported again")
    p.add_argument("--backup-dir", default="", metavar="PATH",
                   help="folder for the backup file (default: backups in "
                        "the data folder)")
    p.add_argument("--no-backup", action="store_true",
                   help="do not write a backup file")
    p.add_argument("--yes", action="store_true",
                   help="do not ask for the confirmation")
    p.add_argument("--force", action="store_true",
                   help="continue although the service looks running or a "
                        "request is marked in progress (only when the "
                        "service is known to be stopped)")
    p.add_argument("--timeout", type=int, default=10, metavar="SECONDS",
                   help="connection time limit (default 10)")
    return p.parse_args(argv)


def run(args) -> int:
    """Run the steps in order and return the exit code.

    Raises:
        Stop: The run ends early; nothing was changed.
    """
    keep = parse_keep(args.keep)
    print("=" * 69)
    print(" FINESIII RPA - clear ticket data")
    print("=" * 69)
    print(" Mode : " + ("REMOVE the rows listed below" if args.apply
                        else "show only (nothing is changed)"))
    print(" Kept : " + (", ".join(STATUS_LABELS[k] for k in keep)
                        or "nothing"))

    step("1/4 Connected database")
    cfg = load_configuration()
    from src.core.app_paths import data_path
    from src.modules.intake.models import now_jst

    if service_is_running():
        if not args.force:
            raise Stop("The service is running. Stop it first:  "
                       ".\\.venv\\Scripts\\python.exe desktop\\app.py "
                       "--stop", 2)
        say("WARN", "Service", "looks running; continuing (--force)")
    else:
        say("OK", "Service", "not running")
    conn = connect(cfg, args.admin_url, args.timeout)
    try:
        say("OK", "Database", conn.shown)
        tables = [t for t in (QUEUE_TABLE, HISTORY_TABLE)
                  if conn.table_exists(t)]
        if not tables:
            raise Stop("Neither request_queue nor transaction_history "
                       "exists in this database. Nothing to clear.")
        for name in (QUEUE_TABLE, HISTORY_TABLE):
            if name not in tables:
                say("INFO", "Table", f"{name} does not exist; skipped")

        step("2/4 Ticket data")
        with_queue = QUEUE_TABLE in tables
        going = {t: show_counts(conn, t, keep, with_queue) for t in tables}
        clauses = {
            t: (remove_clause(t, keep) if with_queue or t == QUEUE_TABLE
                else f"NOT {keep_clause(keep)}") for t in tables}
        in_progress = count_by_status(conn, QUEUE_TABLE).get(
            "in_progress", 0) if with_queue else 0
        if in_progress and "in_progress" not in keep:
            if not args.force:
                raise Stop(f"{in_progress} request(s) are in progress. "
                           f"Stop the service and wait until it has "
                           f"ended. If the service is stopped and the "
                           f"rows were left by a crash, add --force.", 2)
            say("WARN", "In progress", f"{in_progress} request(s) are "
                f"removed although marked in progress (--force)")

        step("3/4 Other files")
        state_path = data_path(POLLER_STATE_FILE)
        state = remembered_tickets(state_path)
        known = len((state or {}).get("notified") or {})
        if args.reset_ticket_memory:
            say("INFO", "Refused tickets remembered",
                f"{known}; they will be reported again")
        elif known:
            say("INFO", "Refused tickets remembered",
                f"{known} ticket(s) the bot already reported as refused "
                f"stay remembered and are not reported again while "
                f"unchanged. Add --reset-ticket-memory to report them "
                f"again.")
        else:
            say("OK", "Refused tickets remembered", "none")
        pending = data_path(PENDING_FILE)
        if pending.is_file() and pending.stat().st_size:
            say("WARN", "Results waiting to be stored",
                f"{pending.name} holds results the service could not "
                f"store earlier; they are stored at its next start. Start "
                f"and stop the service once before clearing if they "
                f"should be cleared as well.")
        say("OK", "Not touched", "console accounts, roles, settings, "
            "credentials, audit trail, evidence folders, logs")

        step("4/4 Result")
        if not any(going.values()) and not (args.reset_ticket_memory
                                            and known):
            say("OK", "Nothing to remove", "the ticket tables hold only "
                "rows that are kept")
            conn.rollback()
            return 0
        if not args.apply:
            say("INFO", "Nothing was changed",
                "run again with --apply to remove the rows")
            conn.rollback()
            return 0
        check_delete_rights(conn, [t for t in tables if going[t]])
        if not args.yes:
            if not sys.stdin.isatty():
                raise Stop("A confirmation is needed. Run the script in a "
                           "normal window, or add --yes.", 2)
            print(f"\n  {sum(going.values())} row(s) will be removed from "
                  f"{conn.shown}.")
            if input(f"  Type {CONFIRM_WORD} to continue: ").strip() \
                    != CONFIRM_WORD:
                raise Stop("Not confirmed. Nothing was changed.", 2)

        stamp = now_jst().strftime("%Y%m%d_%H%M%S")
        backup = None
        if any(going.values()) and not args.no_backup:
            folder = Path(args.backup_dir) if args.backup_dir \
                else data_path("backups")
            backup = backup_rows(conn, clauses, folder, stamp)
            say("OK", "Backup", str(backup))

        removed = {}
        # History first: its condition reads the request table.
        for table in (HISTORY_TABLE, QUEUE_TABLE):
            if table in tables:
                removed[table] = conn.execute(
                    f"DELETE FROM {table} WHERE {clauses[table]}")
        if any(removed[t] != going[t] for t in removed):
            raise Stop("The number of rows changed while the script ran "
                       "(is the service running?). Nothing was removed.")
        conn.commit()
        for table in tables:
            left = sum(count_by_status(conn, table).values())
            say("OK", table, f"{removed[table]} row(s) removed, "
                             f"{left} kept")
        if args.reset_ticket_memory and state is not None and known:
            try:
                forget_tickets(state_path, state)
                say("OK", "Refused tickets remembered",
                    f"{known} forgotten")
            except OSError as e:
                say("WARN", "Refused tickets remembered",
                    f"not cleared ({e})")
        note_run(cfg.log_dir, " | ".join([
            now_jst().isoformat(timespec="seconds"),
            f"account={getpass.getuser()}", conn.shown,
            "kept=" + ",".join(keep),
            *(f"{t}={removed[t]} removed" for t in tables),
            f"backup={backup.name if backup else 'none'}",
            f"ticket_memory={'cleared' if args.reset_ticket_memory else 'kept'}",
        ]))
        print("\n  Done. Start the service again:  "
              ".\\.venv\\Scripts\\python.exe desktop\\app.py --agent")
        return 0
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None) -> int:
    """Run the script and return its exit code."""
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        return run(args)
    except Stop as e:
        print(f"\n  [STOP   ] {e}", flush=True)
        return e.code
    except KeyboardInterrupt:
        print("\n  [STOP   ] Interrupted. Nothing was changed.", flush=True)
        return 2
    except Exception as e:
        print(f"\n  [FAIL   ] {type(e).__name__}: "
              f"{' '.join(str(e).split())[:300]}. Nothing was changed.",
              flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
