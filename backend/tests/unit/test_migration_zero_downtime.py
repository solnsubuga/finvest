"""Every new migration either keeps the running release working or says that it does not.

A deployment runs migrations while the previous release still serves requests, then switches to
the new one (infra/deployment/deploy.sh). An upgrade that drops or renames a table, column or
function, or makes a column required, can break the release still running. Such a migration
declares `zero_downtime = False`, and the deployment stops the previous release before running
it (app/release_preflight.py), or `zero_downtime = True` after checking the running code does not
use what it removes. Spread a removal over two releases to keep deploys online: stop using it in
one, drop it in the next.
"""

import contextlib
import importlib.util
import re
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from app.release_preflight import scripts

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
BREAKING_SQL = re.compile(
    r"\bDROP\s+(TABLE|COLUMN|VIEW|FUNCTION|TYPE)\b|\bRENAME\s+(TO|COLUMN)\b|\bSET\s+NOT\s+NULL\b",
    re.IGNORECASE,
)
BREAKING_CALLS = ("drop_table", "drop_column", "rename_table")
# A new required column with no default makes the old release's inserts fail, as SET NOT NULL does.
# Each ADD COLUMN clause runs to the next clause of the same ALTER TABLE, or the statement's end.
ADD_COLUMN = re.compile(
    r"\bADD\s+COLUMN\b(?P<clause>.*?)(?=,\s*(?:ADD|DROP|ALTER|RENAME)\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
NOT_NULL = re.compile(r"\bNOT\s+NULL\b", re.IGNORECASE)
FILLED = re.compile(r"\bDEFAULT\b|\bGENERATED\b", re.IGNORECASE)


def _required_without_default(sql: str) -> bool:
    return any(
        NOT_NULL.search(match["clause"]) and not FILLED.search(match["clause"])
        for match in ADD_COLUMN.finditer(sql)
    )


def _load(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _breaking(path: Path) -> list[str]:
    module = _load(path)
    op = MagicMock()
    module.__dict__["op"] = op
    with contextlib.suppress(Exception):
        module.upgrade()
    found = [
        sql
        for call in op.execute.call_args_list
        if isinstance(sql := call.args[0] if call.args else None, str)
        and (BREAKING_SQL.search(sql) or _required_without_default(sql))
    ]
    found += [f"op.{name}" for name in BREAKING_CALLS if getattr(op, name).called]
    for call in op.add_column.call_args_list:
        column = call.args[1] if len(call.args) > 1 else call.kwargs.get("column")
        if (
            getattr(column, "nullable", True) is False
            and getattr(column, "server_default", None) is None
        ):
            found.append("op.add_column")
    for call in op.alter_column.call_args_list:
        if "new_column_name" in call.kwargs or call.kwargs.get("nullable") is False:
            found.append("op.alter_column")
    return found


MIGRATIONS = sorted(Path(revision.path) for revision in scripts().walk_revisions())


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda path: path.stem)
def test_breaking_migration_declares_whether_it_needs_the_previous_release_stopped(
    path: Path,
) -> None:
    module = _load(path)
    if hasattr(module, "zero_downtime"):
        assert isinstance(module.zero_downtime, bool), f"{path.stem}: zero_downtime is a bool"
        return
    breaking = _breaking(path)
    assert not breaking, (
        f"{path.stem} may break the release still running during a deployment ({breaking[0]!r}). "
        "Set zero_downtime = False to stop it first, or zero_downtime = True once the running "
        "code no longer uses what this removes."
    )


def test_the_checker_catches_a_dropped_column(tmp_path: Path) -> None:
    migration = tmp_path / "9999_drop.py"
    migration.write_text(
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.execute('ALTER TABLE clients DROP COLUMN name')\n"
        "    op.alter_column('clients', 'name', new_column_name='label')\n"
    )
    assert _breaking(migration) == ["ALTER TABLE clients DROP COLUMN name", "op.alter_column"]


def test_the_checker_catches_a_required_column_with_no_default(tmp_path: Path) -> None:
    migration = tmp_path / "9999_required.py"
    migration.write_text(
        "import sqlalchemy as sa\n"
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.execute('ALTER TABLE clients ADD COLUMN t text NOT NULL, ADD COLUMN m text')\n"
        "    op.add_column('clients', sa.Column('band', sa.Text(), nullable=False))\n"
    )
    assert _breaking(migration) == [
        "ALTER TABLE clients ADD COLUMN t text NOT NULL, ADD COLUMN m text",
        "op.add_column",
    ]


def test_the_checker_passes_a_required_column_with_a_default(tmp_path: Path) -> None:
    migration = tmp_path / "9999_defaulted.py"
    migration.write_text(
        "import sqlalchemy as sa\n"
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.execute(\"ALTER TABLE clients ADD COLUMN t text NOT NULL DEFAULT 'b'\")\n"
        "    op.execute('ALTER TABLE clients ADD COLUMN n numeric(10, 2) NOT NULL DEFAULT 0')\n"
        "    op.add_column('c', sa.Column('b', sa.Text(), nullable=False, server_default=''))\n"
    )
    assert _breaking(migration) == []


def test_the_checker_passes_an_additive_migration(tmp_path: Path) -> None:
    migration = tmp_path / "9999_add.py"
    migration.write_text(
        "from alembic import op\n"
        "def upgrade():\n"
        "    op.execute('ALTER TABLE clients ADD COLUMN nickname text')\n"
    )
    assert _breaking(migration) == []
