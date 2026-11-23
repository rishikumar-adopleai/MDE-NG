import sys
from getpass import getpass
from sqlite3 import IntegrityError

from app.core.config import settings
from app.services.accounts import create_account, initialize_accounts


def main() -> None:
    if settings.app_env != "dev" or not sys.stdin.isatty():
        raise SystemExit("Run this command from an interactive terminal in development mode.")
    email = getpass("Account email (hidden): ")
    password = getpass("Account password (hidden): ")
    if password != getpass("Confirm password (hidden): "):
        raise SystemExit("Passwords do not match.")
    try:
        initialize_accounts()
        create_account(email, password, role="admin")
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    except IntegrityError:
        raise SystemExit("An account already exists for that email.") from None
    print("Development administrator created.")


if __name__ == "__main__":
    main()
