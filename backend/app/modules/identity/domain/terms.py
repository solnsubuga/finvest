"""The versions of the terms and the privacy notice a client agrees to.

A version is the day the wording took effect, as `YYYY-MM-DD`. The website shows the same
version on each page and keeps a PDF of every version at `/legal/<document>-<version>.pdf`
(`apps/site/src/data/legal.ts`), so a client can always read what they agreed to. When the
lawyers change either document, change its version here and on the website in the same pull
request. A test checks the two agree.
"""

from typing import Final, Literal

TermsDocument = Literal["terms", "privacy"]

CURRENT_VERSIONS: Final[dict[TermsDocument, str]] = {
    "terms": "2026-11-14",
    "privacy": "2026-11-14",
}

# How a client came to agree: by signing up, or by claiming an account carried over from
# Finvest 1.0, which is when they first agree to Finvest 2.0's terms.
AcceptedVia = Literal["signup", "claim"]
