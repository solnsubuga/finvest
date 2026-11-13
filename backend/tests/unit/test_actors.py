import pytest

from app.shared.actors import SYSTEM, Actor
from app.shared.push_ids import push_id


def test_people_need_a_valid_id() -> None:
    assert Actor("client", push_id()).kind == "client"
    with pytest.raises(ValueError, match="needs an ID"):
        Actor("staff")
    with pytest.raises(ValueError, match="Push IDs"):
        Actor("client", "not-an-id")


def test_the_system_has_no_id() -> None:
    assert SYSTEM.id is None
    with pytest.raises(ValueError, match="no ID"):
        Actor("system", push_id())
