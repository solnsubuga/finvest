"""Private file storage behind one small interface, shared by every module.

Files (bank transfer receipts, identity document photos, proofs of account ownership) are
uploaded by the API, never by the browser, and kept in a private bucket. A module stores the
key, the size and the SHA-256 digest in its own table. Staff see a file through a read link that
works for a few minutes only.

Keys look like `<module>/<record id>/<file id>`. A file is written once and never replaced: a
new version gets a new key, so a record's digest always describes what is stored under its key.

Failures are sorted so the caller knows whether trying again is safe:

- `StorageUnavailable`: the store could not be reached or answered with a server error. Writing
  the same key with the same bytes again is safe.
- `StorageRefused`: the store refused the credentials or the request. Retrying cannot help.
- `FileNotFound`: no file is stored under the key.

Error messages are safe to store and log: they never contain keys, contents or credentials.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

KEY = re.compile(r"^[a-z][a-z0-9-]{0,30}(/[A-Za-z0-9_-]{1,64}){1,6}$")
# The only kinds of file modules keep. Nothing a browser would run as a page (HTML, SVG) can be
# stored, so a read link can never open one from the bucket's address inside the console.
CONTENT_TYPES = frozenset({"application/pdf", "image/jpeg", "image/png", "image/webp", "text/csv"})
SHA256 = re.compile(r"^[a-f0-9]{64}$")
# Long enough to open a file in the console, short enough that a copied link soon stops working.
MAX_LINK_LIFETIME = timedelta(minutes=15)
MAX_FILE_BYTES = 25 * 1024 * 1024


class StorageError(Exception):
    code = "storage_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.safe_message = message
        if code is not None:
            self.code = code


class InvalidFile(StorageError, ValueError):
    """The key, type, size or digest is not acceptable. Retrying the same call cannot succeed."""

    code = "invalid_file"


class StorageUnavailable(StorageError):
    code = "storage_unavailable"


class StorageRefused(StorageError):
    code = "storage_refused"


class FileNotFound(StorageError):
    code = "file_not_found"


class FileMismatch(StorageError):
    """The bytes read back do not match the digest the caller recorded."""

    code = "file_mismatch"


def check_content_type(content_type: str) -> str:
    if content_type not in CONTENT_TYPES:
        raise InvalidFile("Only PDF, JPEG, PNG, WebP and CSV files can be stored")
    return content_type


def check_key(key: str) -> str:
    if not KEY.fullmatch(key):
        raise InvalidFile("A storage key is not valid")
    return key


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class NewFile:
    """A file to store. The digest is worked out here, so it always matches the bytes."""

    key: str
    data: bytes = field(repr=False)
    content_type: str

    def __post_init__(self) -> None:
        check_key(self.key)
        check_content_type(self.content_type)
        if not 0 < len(self.data) <= MAX_FILE_BYTES:
            raise InvalidFile(f"A file must hold 1 byte to {MAX_FILE_BYTES} bytes")

    @property
    def sha256(self) -> str:
        return sha256_hex(self.data)

    @property
    def byte_size(self) -> int:
        return len(self.data)


@dataclass(frozen=True)
class StoredFile:
    key: str
    content_type: str
    byte_size: int
    sha256: str


@dataclass(frozen=True)
class ReadLink:
    url: str = field(repr=False)
    expires_at: datetime


def check_lifetime(lifetime: timedelta) -> int:
    seconds = int(lifetime.total_seconds())
    if not 1 <= seconds <= MAX_LINK_LIFETIME.total_seconds():
        raise InvalidFile("A read link may last at most 15 minutes")
    return seconds


class FileStorage(Protocol):
    name: str

    async def put(self, file: NewFile) -> StoredFile:
        """Store the file. Storing the same key with the same bytes again is harmless."""
        ...

    async def read(self, key: str, *, sha256: str) -> bytes:
        """The file's bytes, checked against the digest the caller recorded."""
        ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> None:
        """Remove the file. Removing a key that holds nothing is harmless."""
        ...

    def read_link(
        self, key: str, *, content_type: str, lifetime: timedelta, now: datetime
    ) -> ReadLink:
        """A link that opens the file in a browser until it expires. No network call."""
        ...


class DisabledStorage:
    """No store is configured: every call fails closed."""

    name = "none"

    async def put(self, file: NewFile) -> StoredFile:
        raise StorageUnavailable("File storage is not configured", code="storage_not_configured")

    async def read(self, key: str, *, sha256: str) -> bytes:
        raise StorageUnavailable("File storage is not configured", code="storage_not_configured")

    async def exists(self, key: str) -> bool:
        raise StorageUnavailable("File storage is not configured", code="storage_not_configured")

    async def delete(self, key: str) -> None:
        raise StorageUnavailable("File storage is not configured", code="storage_not_configured")

    def read_link(
        self, key: str, *, content_type: str, lifetime: timedelta, now: datetime
    ) -> ReadLink:
        raise StorageUnavailable("File storage is not configured", code="storage_not_configured")


class MemoryStorage:
    """Tests and local development: files live in this process only and vanish with it."""

    name = "memory"

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    async def put(self, file: NewFile) -> StoredFile:
        held = self.files.get(file.key)
        if held is not None and held[0] != file.data:
            # A key is written once. Real stores would keep both versions; refusing here makes a
            # test that reuses a key fail loudly.
            raise InvalidFile("A different file is already stored under this key")
        self.files[file.key] = (file.data, file.content_type)
        return StoredFile(file.key, file.content_type, file.byte_size, file.sha256)

    async def read(self, key: str, *, sha256: str) -> bytes:
        check_key(key)
        held = self.files.get(key)
        if held is None:
            raise FileNotFound("No file is stored under this key")
        return check_digest(held[0], sha256)

    async def exists(self, key: str) -> bool:
        return check_key(key) in self.files

    async def delete(self, key: str) -> None:
        self.files.pop(check_key(key), None)

    def read_link(
        self, key: str, *, content_type: str, lifetime: timedelta, now: datetime
    ) -> ReadLink:
        check_key(key)
        check_content_type(content_type)
        seconds = check_lifetime(lifetime)
        expires_at = now.astimezone(UTC) + timedelta(seconds=seconds)
        return ReadLink(f"memory://{key}?expires={int(expires_at.timestamp())}", expires_at)


def check_digest(data: bytes, sha256: str) -> bytes:
    if not SHA256.fullmatch(sha256):
        raise InvalidFile("A SHA-256 digest is not valid")
    if sha256_hex(data) != sha256:
        raise FileMismatch("The stored file does not match its recorded digest")
    return data
