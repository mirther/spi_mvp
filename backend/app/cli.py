from __future__ import annotations

import argparse
import getpass
import re
import sys
from uuid import uuid4

from argon2 import PasswordHasher
from sqlalchemy import select

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models import AuthSession, AuthUser, ClusterConfiguration, DataAsset, PipelineConnection, PipelineNode, Project, ProjectRun, RunExecution

PASSWORD_MIN_LENGTH = 15
USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)


def prompt_password() -> str:
    password = getpass.getpass("New SPI password (minimum 15 characters): ")
    confirmation = getpass.getpass("Confirm SPI password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise SystemExit(f"Password must contain at least {PASSWORD_MIN_LENGTH} characters.")
    return password


def create_admin() -> None:
    Base.metadata.create_all(bind=engine)
    username = input("SPI administrator username: ").strip().casefold()
    if not USERNAME_PATTERN.fullmatch(username):
        raise SystemExit("Username must be 3-64 characters: letters, numbers, dot, underscore or hyphen.")

    with SessionLocal() as db:
        existing = db.scalar(select(AuthUser).limit(1))
        if existing is not None:
            raise SystemExit("An SPI account already exists. Use 'reset-password' to replace its password.")
        password = prompt_password()
        db.add(AuthUser(id=str(uuid4()), username=username, password_hash=password_hasher.hash(password)))
        db.commit()

    print(f"Created the SPI administrator account '{username}'.")


def reset_password() -> None:
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        users = db.scalars(select(AuthUser).order_by(AuthUser.username)).all()
        if not users:
            raise SystemExit("No SPI account exists. Run 'create-admin' first.")
        if len(users) == 1:
            user = users[0]
        else:
            print("Accounts:")
            for item in users:
                print(f" - {item.username}")
            username = input("Username to reset: ").strip().casefold()
            user = db.scalar(select(AuthUser).where(AuthUser.username == username))
            if user is None:
                raise SystemExit("Account not found.")
        password = prompt_password()
        user.password_hash = password_hasher.hash(password)
        db.query(AuthSession).filter(AuthSession.user_id == user.id).delete(synchronize_session=False)
        db.commit()
        print(f"Password reset for '{user.username}'. All existing sessions were revoked.")


def main() -> None:
    parser = argparse.ArgumentParser(description="SPI local account administration")
    parser.add_argument("command", choices=["create-admin", "reset-password"])
    args = parser.parse_args()
    if args.command == "create-admin":
        create_admin()
    else:
        reset_password()


if __name__ == "__main__":
    main()
