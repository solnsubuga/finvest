"""The versions the API records for the terms and privacy notice match the website's."""

import re
from pathlib import Path

from app.modules.identity.domain.terms import CURRENT_VERSIONS

LEGAL = Path(__file__).resolve().parents[3] / "apps" / "site" / "src" / "data" / "legal.ts"


def test_the_recorded_versions_are_the_ones_the_website_shows() -> None:
    source = LEGAL.read_text(encoding="utf-8")
    shown = dict(re.findall(r'slug: "(\w+)",\s*title: "[^"]*",\s*version: "([0-9-]+)"', source))
    assert shown == CURRENT_VERSIONS
