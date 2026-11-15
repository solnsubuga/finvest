"""Whether a password is in a known breach, from Have I Been Pwned's Pwned Passwords range API.

The password never leaves this process. Only the first five characters of its SHA-1 digest
are sent, and the service answers with every breached digest sharing them, padded with
decoys (`Add-Padding`) so that the size of the answer says nothing either. The match is made
here (k-anonymity, https://haveibeenpwned.com/API/v3#PwnedPasswords).

The check only ever adds a refusal. When the service is slow or unreachable, `seen` answers
None and the caller keeps the built-in list of common passwords alone, so signing up never
depends on it. Nothing about the password, the digest or the answer is logged.
"""

import hashlib

import httpx
import structlog

DEFAULT_BASE_URL = "https://api.pwnedpasswords.com"
# A person is waiting on this answer, so it is short. A slow answer counts as no answer.
DEFAULT_TIMEOUT = 2.0
# The service asks every caller to name itself.
USER_AGENT = "finvest-backend"

logger = structlog.get_logger()


def _digest(password: str) -> str:
    # SHA-1 is what the service indexes by. It is a lookup key here, never a stored hash.
    return hashlib.sha1(password.encode(), usedforsecurity=False).hexdigest().upper()


class PwnedPasswords:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    async def seen(self, password: str) -> bool | None:
        """True when the password is in a breach, False when not, None when we cannot tell."""
        digest = _digest(password)
        prefix, suffix = digest[:5], digest[5:]
        try:
            body = await self._range(prefix)
        except httpx.HTTPError:
            logger.warning("breach_check_unavailable", error_code="breach_check_unavailable")
            return None
        for line in body.splitlines():
            found, _, count = line.strip().partition(":")
            # Padding lines carry a count of 0 and are never a match.
            if found == suffix and count.strip().isdigit() and int(count) > 0:
                return True
        return False

    async def _range(self, prefix: str) -> str:
        headers = {"Add-Padding": "true", "User-Agent": USER_AGENT}
        url = f"{self.base_url}/range/{prefix}"
        if self._client is not None:
            response = await self._client.get(url, headers=headers, timeout=self.timeout)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.text
