#!/usr/bin/env python3
"""
Generate sample passwords with the bot's own generator, for validation.

Writes the requested number of passwords to a CSV file, each one drawn
by the same function the service uses for a provisioning request
(src/modules/intake/passwords.py of this application folder), and
checks every password against the password rules:

  * exactly 8 half-width characters;
  * the first and the last character are letters;
  * only uppercase letters, digits and the configured symbols;
  * at least one digit and, when symbols are configured, at least one
    symbol, between the first and the last character;
  * no look-alike characters (O 0 I 1), unless they are allowed.

The checks are written here, apart from the generator, so the file
shows whether the generator keeps the rules.

Run it from the application folder. The service may stay running:

  # 1,000 passwords with the shipped settings (symbol "@").
  .\\.venv\\Scripts\\python.exe scripts\\generate_passwords.py 1000

  # Another file name, or other symbols (as on Settings > Provisioning
  # password when they were changed there).
  .\\.venv\\Scripts\\python.exe scripts\\generate_passwords.py 500 --out C:\\Temp\\sample.csv
  .\\.venv\\Scripts\\python.exe scripts\\generate_passwords.py 500 --symbols "@#"

  # Look-alike characters allowed (when that setting is switched off).
  .\\.venv\\Scripts\\python.exe scripts\\generate_passwords.py 500 --allow-lookalikes

What it does not do
  - It does not connect to the database, the service or ServiceNow.
  - It changes no setting and no file of the application.
  - The passwords are samples: none of them is issued to a user or
    recorded as used. The service keeps its own record of the passwords
    it issued; this script neither reads nor writes it.

The CSV file holds the passwords in clear text. Keep it only as long as
the validation needs it.

Exit code 0: every password keeps the rules. 1: at least one does not
(the rows are marked FAIL), or the run was stopped. 2: the command line
is not right.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import string
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATOR_FILE = ROOT / "src" / "modules" / "intake" / "passwords.py"
MAX_COUNT = 1_000_000
# Draws allowed per password before the run stops: a generator that
# keeps returning passwords already in the file is not random.
MAX_DRAWS = 50
COLUMNS = ("number", "password", "length", "first_is_letter",
           "last_is_letter", "has_digit", "has_symbol",
           "allowed_characters_only", "no_lookalike_characters", "result")
LETTERS = string.ascii_uppercase
DIGITS = string.digits


class Stop(Exception):
    """Ends the run with a message; no file was written."""


def load_generator(path: Path = GENERATOR_FILE):
    """Load the service's password module from its file.

    Loaded by path, without the application package around it, so
    nothing of the service is started and no setting is read.

    Args:
        path: The password module of the application folder.

    Returns:
        The loaded module.

    Raises:
        Stop: If the file is missing or cannot be loaded.
    """
    if not path.is_file():
        raise Stop(f"The password module was not found: {path}\n"
                   f"Run the script from the application folder.")
    try:
        spec = importlib.util.spec_from_file_location(
            "finesiii_password_generator", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except Exception as e:
        raise Stop(f"The password module could not be loaded: {e}")
    if not callable(getattr(module, "generate", None)):
        raise Stop(f"{path} has no password generator (generate).")
    return module


def checksum(path: Path) -> str:
    """Return the SHA-256 checksum of a file (hex)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(password: str, symbols: str, exclude_lookalikes: bool,
          lookalikes) -> dict:
    """Check one password against the password rules.

    Args:
        password: The password.
        symbols: The symbols a password may hold.
        exclude_lookalikes: Look-alike characters are not allowed.
        lookalikes: The look-alike characters.

    Returns:
        One value per rule (True = kept), "length" and "result"
        ("PASS" when every rule is kept, otherwise "FAIL").
    """
    middle = password[1:-1]
    rules = {
        "first_is_letter": password[:1] in LETTERS and bool(password),
        "last_is_letter": password[-1:] in LETTERS and bool(password),
        "has_digit": any(c in DIGITS for c in middle),
        # Without configured symbols a password has none to hold.
        "has_symbol": (any(c in symbols for c in middle) if symbols
                       else True),
        "allowed_characters_only": bool(password) and all(
            c in LETTERS + DIGITS + symbols for c in password),
        "no_lookalike_characters": not (
            exclude_lookalikes and any(c in lookalikes for c in password)),
    }
    kept = len(password) == 8 and all(rules.values())
    return {"length": len(password), **rules,
            "result": "PASS" if kept else "FAIL"}


def draw(generator, count: int, symbols: str,
         exclude_lookalikes: bool) -> tuple:
    """Draw `count` different passwords from the generator.

    A password that is already in the list is drawn again, as the
    service draws again a password it has issued before.

    Returns:
        (passwords, number of passwords drawn again).

    Raises:
        Stop: If the generator fails or keeps repeating itself.
    """
    passwords, seen, again = [], set(), 0
    for _ in range(count):
        for _attempt in range(MAX_DRAWS):
            try:
                password = str(generator.generate(symbols,
                                                  exclude_lookalikes))
            except Exception as e:
                raise Stop(f"The generator failed: {e}")
            if password not in seen:
                break
            again += 1
        else:
            raise Stop(f"The generator returned a password already in "
                       f"the list {MAX_DRAWS} times in a row.")
        seen.add(password)
        passwords.append(password)
    return passwords, again


def write_csv(path: Path, rows: list) -> None:
    """Write the rows to a CSV file that a spreadsheet opens as text.

    Raises:
        Stop: If the file cannot be written.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # UTF-8 with a byte order mark, so a spreadsheet reads it right.
        with path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
    except OSError as e:
        raise Stop(f"The file could not be written: {e}")


def _yes_no(value) -> str:
    """Return a rule's outcome as it is written to the file."""
    return "yes" if value else "no"


def _count(text: str) -> int:
    """Return the number of passwords asked for.

    Raises:
        argparse.ArgumentTypeError: If it is not 1 to MAX_COUNT.
    """
    try:
        number = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError("must be a whole number")
    if not 1 <= number <= MAX_COUNT:
        raise argparse.ArgumentTypeError(
            f"must be between 1 and {MAX_COUNT:,}")
    return number


def parse_arguments(argv) -> argparse.Namespace:
    """Read the command line."""
    parser = argparse.ArgumentParser(
        description="Generate sample passwords with the bot's generator "
                    "and check them against the password rules.")
    parser.add_argument("count", type=_count,
                        help="how many passwords to generate")
    parser.add_argument("--out", default="",
                        help="the CSV file to write (default: "
                             "generated_passwords_<count>_<time>.csv in "
                             "the current folder)")
    parser.add_argument("--symbols", default="@",
                        help="symbols used in generated passwords, as on "
                             "the console (default: @)")
    parser.add_argument("--allow-lookalikes", action="store_true",
                        help="allow the look-alike characters O 0 I 1")
    parser.add_argument("--force", action="store_true",
                        help="replace the CSV file when it exists")
    return parser.parse_args(argv)


def run(argv, out=sys.stdout) -> int:
    """Generate, check and export the passwords.

    Args:
        argv: The command line arguments, without the program name.
        out: Where the summary is printed.

    Returns:
        The exit code: 0 when every password keeps the rules, else 1.
    """
    args = parse_arguments(argv)
    try:
        generator = load_generator()
        target = Path(args.out) if args.out else Path.cwd() / (
            f"generated_passwords_{args.count}_"
            f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv")
        if target.exists() and not args.force:
            raise Stop(f"{target} exists already. Give another name with "
                       f"--out, or add --force to replace it.")
        exclude = not args.allow_lookalikes
        # The symbols the generator really uses: it leaves out those
        # the terminal cannot type.
        allowed = getattr(generator, "allowed_symbols", lambda s: s)
        symbols = allowed(args.symbols)
        lookalikes = set(getattr(generator, "LOOKALIKES", "O0I1"))
        passwords, again = draw(generator, args.count, args.symbols,
                                exclude)
        rows, failed = [], 0
        for number, password in enumerate(passwords, start=1):
            outcome = check(password, symbols, exclude, lookalikes)
            failed += outcome["result"] != "PASS"
            rows.append({
                "number": number, "password": password,
                "length": outcome["length"], "result": outcome["result"],
                **{name: _yes_no(outcome[name]) for name in COLUMNS[3:9]}})
        write_csv(target, rows)
    except Stop as stopped:
        print(f"Stopped: {stopped}", file=out)
        return 1

    ignored = "".join(c for c in args.symbols if c not in symbols)
    print(f"Generator      : {GENERATOR_FILE}", file=out)
    print(f"  SHA-256      : {checksum(GENERATOR_FILE)}", file=out)
    print(f"Symbols        : {symbols or '(none)'}"
          + (f"   (not usable, left out: {ignored})" if ignored else ""),
          file=out)
    print(f"Look-alikes    : {'allowed' if not exclude else 'left out'} "
          f"(O 0 I 1)", file=out)
    print(f"Generated      : {len(rows):,}, all different"
          + (f" ({again} drawn again)" if again else ""), file=out)
    print(f"Rules kept     : {len(rows) - failed:,}", file=out)
    print(f"Rules not kept : {failed:,}", file=out)
    print(f"File           : {target.resolve()}", file=out)
    print("The file holds passwords in clear text. None of them was "
          "issued or recorded;\ndelete the file when the validation is "
          "done.", file=out)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
