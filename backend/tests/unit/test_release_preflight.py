"""What the release preflight makes of each database revision. The database read is covered in
tests/integration/test_release_preflight.py."""

import pytest

from app import release_preflight
from app.release_preflight import STOP_PREVIOUS, schema_relation, scripts


def _chain() -> list[str]:
    # Head first, base last.
    return [revision.revision for revision in scripts().walk_revisions()]


def test_an_empty_database_is_created_by_the_migrations() -> None:
    assert "no schema" in schema_relation(set())


def test_a_database_at_the_head_needs_nothing() -> None:
    assert "schema head" in schema_relation({_chain()[0]})


def test_an_older_known_revision_needs_forward_migrations() -> None:
    message = schema_relation({_chain()[1]})
    assert "forward migrations" in message
    assert STOP_PREVIOUS not in message


def test_an_unknown_revision_is_refused() -> None:
    with pytest.raises(ValueError, match="matching image"):
        schema_relation({"9999_from_a_newer_release"})
    with pytest.raises(ValueError):
        schema_relation({_chain()[0], "9999_from_a_newer_release"})


def test_only_a_pending_migration_marked_unsafe_asks_to_stop_the_previous_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = scripts()
    chain = _chain()
    unsafe, before = chain[1], chain[2]
    for revision in script.walk_revisions():
        monkeypatch.setattr(
            revision.module, "zero_downtime", revision.revision != unsafe, raising=False
        )
    monkeypatch.setattr(release_preflight, "scripts", lambda: script)
    message = schema_relation({before})
    assert message.endswith(STOP_PREVIOUS)
    assert f"{unsafe} cannot run beside" in message
    # Already applied, so it no longer matters.
    assert STOP_PREVIOUS not in schema_relation({unsafe})
    assert STOP_PREVIOUS not in schema_relation(set())
