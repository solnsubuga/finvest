"""Build a disposable database the way a deployment does: roles script, then migrations.

Integration tests need a PostgreSQL administrator URL in `FINVEST_TEST_ADMIN_URL`, for example
`postgresql://postgres:postgres@localhost:5432/postgres`. The harness creates a fresh database,
runs `db/init-roles.sql` with psql, and runs the real migration entry point as finvest_migrator.
Tests then connect as finvest_app and finvest_worker, never as the administrator.
"""

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import URL, make_url

from app.infrastructure.settings import Settings

BACKEND = Path(__file__).resolve().parents[2]
ROLES_SCRIPT = BACKEND / "db" / "init-roles.sql"
ADMIN_URL_VARIABLE = "FINVEST_TEST_ADMIN_URL"
# Local, disposable test databases only. Deployed passwords are generated and held outside git.
PASSWORDS = {
    "finvest_migrator": "test-migrator",
    "finvest_app": "test-app",
    "finvest_worker": "test-worker",
}


@dataclass(frozen=True, slots=True)
class TestDatabase:
    __test__ = False

    admin: URL
    name: str

    def url(self, role: str) -> str:
        return self.admin.set(
            drivername="postgresql+asyncpg",
            username=role,
            password=PASSWORDS[role],
            database=self.name,
        ).render_as_string(hide_password=False)

    def settings(self, role: str, *, pool_size: int = 5) -> Settings:
        # The disposable test database has no certificate.
        return Settings(
            database_url=self.url(role), database_pool_size=pool_size, database_ssl_mode="disable"
        )

    def environment(self, role: str) -> dict[str, str]:
        return {
            **os.environ,
            "FINVEST_DATABASE_URL": self.url(role),
            "FINVEST_DATABASE_SSL_MODE": "disable",
        }


def admin_url() -> URL:
    value = os.environ.get(ADMIN_URL_VARIABLE)
    if not value:
        raise RuntimeError(
            f"Integration tests need {ADMIN_URL_VARIABLE}: a PostgreSQL administrator URL. "
            "Run `make test-integration`, which starts PostgreSQL and sets it."
        )
    return make_url(value).set(drivername="postgresql")


def psql(url: URL, *arguments: str) -> None:
    environment = {
        **os.environ,
        "PGPASSWORD": url.password or "",
        "FINVEST_MIGRATOR_PASSWORD": PASSWORDS["finvest_migrator"],
        "FINVEST_APP_PASSWORD": PASSWORDS["finvest_app"],
        "FINVEST_WORKER_PASSWORD": PASSWORDS["finvest_worker"],
    }
    target = url.set(password=None).render_as_string(hide_password=False)
    subprocess.run(
        ["psql", "--no-psqlrc", "--quiet", "-v", "ON_ERROR_STOP=1", target, *arguments],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )


def create(name: str) -> TestDatabase:
    """A fresh database with the three roles and schema public handed to the migrator."""
    admin = admin_url()
    psql(admin, "-c", f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    psql(admin, "-c", f'CREATE DATABASE "{name}"')
    psql(admin.set(database=name), "-f", str(ROLES_SCRIPT))
    return TestDatabase(admin=admin, name=name)


def drop(database: TestDatabase) -> None:
    psql(database.admin, "-c", f'DROP DATABASE IF EXISTS "{database.name}" WITH (FORCE)')


def migrate(
    database: TestDatabase, role: str = "finvest_migrator"
) -> subprocess.CompletedProcess[str]:
    """Run the real migration entry point, as a deployment does."""
    return subprocess.run(
        [sys.executable, "-m", "app.migrate_main"],
        cwd=BACKEND,
        env=database.environment(role),
        capture_output=True,
        text=True,
        check=False,
    )


def alembic(database: TestDatabase, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=BACKEND,
        env=database.environment("finvest_migrator"),
        capture_output=True,
        text=True,
        check=False,
    )
