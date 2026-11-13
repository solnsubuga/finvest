import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[2]


def scripts() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))


def test_the_history_has_one_head() -> None:
    # Two pull requests that each add a revision produce two heads. The second to merge must
    # point its down_revision at the first.
    assert len(scripts().get_heads()) == 1


def test_revision_ids_match_their_file_names() -> None:
    for revision in scripts().walk_revisions():
        name = Path(revision.path).stem
        assert revision.revision == name
        assert re.fullmatch(r"\d{4}_[a-z0-9_]+", name)
