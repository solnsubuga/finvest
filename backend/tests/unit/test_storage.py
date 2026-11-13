"""File storage: SigV4 against AWS's published examples, the memory fake, Spaces over a mock."""

import hashlib
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.infrastructure.storage import (
    DisabledStorage,
    FileMismatch,
    FileNotFound,
    InvalidFile,
    MemoryStorage,
    NewFile,
    SpacesStorage,
    StorageRefused,
    StorageSettings,
    StorageUnavailable,
    sha256_hex,
    sigv4,
)

# The example credentials and moment from AWS's SigV4 documentation for S3.
AWS = sigv4.Credentials(
    "AKIAIOSFODNN7EXAMPLE", SecretStr("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
)
AWS_MOMENT = datetime(2013, 5, 24, tzinfo=UTC)
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
KEY = "kyc/check1/photo1"
PNG = b"\x89PNG\r\n\x1a\nnot really an image"


def test_get_object_matches_the_aws_example() -> None:
    header = sigv4.authorization(
        AWS,
        method="GET",
        path="/test.txt",
        query={},
        headers={
            "Host": "examplebucket.s3.amazonaws.com",
            "Range": "bytes=0-9",
            "x-amz-content-sha256": sigv4.EMPTY_SHA256,
            "x-amz-date": "20130524T000000Z",
        },
        payload_hash=sigv4.EMPTY_SHA256,
        moment=AWS_MOMENT,
        region="us-east-1",
    )
    assert header == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


def test_put_object_matches_the_aws_example() -> None:
    body = b"Welcome to Amazon S3."
    digest = hashlib.sha256(body).hexdigest()
    header = sigv4.authorization(
        AWS,
        method="PUT",
        path="/" + sigv4.uri_encode("test$file.text", keep_slash=True),
        query={},
        headers={
            "Host": "examplebucket.s3.amazonaws.com",
            "Date": "Fri, 24 May 2013 00:00:00 GMT",
            "x-amz-date": "20130524T000000Z",
            "x-amz-storage-class": "REDUCED_REDUNDANCY",
            "x-amz-content-sha256": digest,
        },
        payload_hash=digest,
        moment=AWS_MOMENT,
        region="us-east-1",
    )
    assert header.endswith(
        "SignedHeaders=date;host;x-amz-content-sha256;x-amz-date;x-amz-storage-class, "
        "Signature=98ad721746da40c64f1a55b78f14c238d841ea1380cd77a1b5971af0ece108bd"
    )


def test_presigned_link_matches_the_aws_example() -> None:
    query = sigv4.presigned_query(
        AWS,
        method="GET",
        host="examplebucket.s3.amazonaws.com",
        path="/test.txt",
        extra={},
        expires_seconds=86400,
        moment=AWS_MOMENT,
        region="us-east-1",
    )
    assert parse_qs(query)["X-Amz-Signature"] == [
        "aeeed9bbccd4d02ee5c0109b86d86835f995330da4c265957d157751f604d404"
    ]


@pytest.mark.parametrize(
    "key",
    ["", "kyc", "KYC/a/b", "kyc/../secret", "kyc/a b/c", "kyc//c", "/kyc/a", "kyc/a/", "kyc/a?x=1"],
)
def test_a_key_that_could_escape_or_confuse_is_refused(key: str) -> None:
    with pytest.raises(InvalidFile):
        NewFile(key, PNG, "image/png")


def test_a_file_needs_a_type_and_some_bytes() -> None:
    with pytest.raises(InvalidFile):
        NewFile(KEY, PNG, "image/png; charset=x")
    with pytest.raises(InvalidFile):
        NewFile(KEY, b"", "image/png")


async def test_memory_storage_keeps_what_it_was_given() -> None:
    storage = MemoryStorage()
    stored = await storage.put(NewFile(KEY, PNG, "image/png"))
    assert stored.sha256 == sha256_hex(PNG) and stored.byte_size == len(PNG)
    assert await storage.read(KEY, sha256=stored.sha256) == PNG
    assert await storage.exists(KEY)
    # Writing the same bytes again, as a retry does, is harmless.
    await storage.put(NewFile(KEY, PNG, "image/png"))
    with pytest.raises(InvalidFile):
        await storage.put(NewFile(KEY, b"other", "image/png"))
    with pytest.raises(FileMismatch):
        await storage.read(KEY, sha256=sha256_hex(b"other"))
    await storage.delete(KEY)
    await storage.delete(KEY)
    assert not await storage.exists(KEY)
    with pytest.raises(FileNotFound):
        await storage.read(KEY, sha256=stored.sha256)


def test_read_links_last_at_most_fifteen_minutes() -> None:
    storage = MemoryStorage()
    link = storage.read_link(KEY, content_type="image/png", lifetime=timedelta(minutes=5), now=NOW)
    assert link.expires_at == NOW + timedelta(minutes=5)
    with pytest.raises(InvalidFile):
        storage.read_link(KEY, content_type="image/png", lifetime=timedelta(minutes=16), now=NOW)


async def test_disabled_storage_fails_closed() -> None:
    with pytest.raises(StorageUnavailable):
        await DisabledStorage().put(NewFile(KEY, PNG, "image/png"))


def spaces(handler: httpx.MockTransport, **changes: object) -> SpacesStorage:
    values: dict[str, object] = {
        "bucket": "finvest-staging-documents",
        "endpoint": "https://lon1.digitaloceanspaces.com",
        "region": "lon1",
        "credentials": sigv4.Credentials("DO00EXAMPLE", SecretStr("not-a-real-secret")),
        "client": httpx.AsyncClient(transport=handler),
    }
    return SpacesStorage(**{**values, **changes})  # type: ignore[arg-type]


async def test_spaces_upload_is_signed_with_the_body_digest() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    stored = await spaces(httpx.MockTransport(handle)).put(NewFile(KEY, PNG, "image/png"))
    request = seen[0]
    assert request.method == "PUT"
    assert (
        str(request.url) == f"https://finvest-staging-documents.lon1.digitaloceanspaces.com/{KEY}"
    )
    assert request.headers["x-amz-content-sha256"] == stored.sha256 == sha256_hex(PNG)
    assert request.headers["content-type"] == "image/png"
    assert request.content == PNG
    authorization = request.headers["authorization"]
    assert "/lon1/s3/aws4_request" in authorization
    assert "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date" in authorization
    assert "not-a-real-secret" not in authorization


async def test_spaces_path_style_puts_the_bucket_in_the_path() -> None:
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    storage = spaces(httpx.MockTransport(handle), endpoint="http://localhost:9000", path_style=True)
    await storage.put(NewFile(KEY, PNG, "image/png"))
    assert str(seen[0].url) == f"http://localhost:9000/finvest-staging-documents/{KEY}"


async def test_spaces_read_checks_the_digest() -> None:
    storage = spaces(httpx.MockTransport(lambda request: httpx.Response(200, content=PNG)))
    assert await storage.read(KEY, sha256=sha256_hex(PNG)) == PNG
    with pytest.raises(FileMismatch):
        await storage.read(KEY, sha256=sha256_hex(b"what was recorded"))


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (403, StorageRefused),
        (400, StorageRefused),
        (404, FileNotFound),
        (429, StorageUnavailable),
        (503, StorageUnavailable),
    ],
)
async def test_spaces_failures_are_sorted(status: int, error: type[Exception]) -> None:
    storage = spaces(
        httpx.MockTransport(lambda request: httpx.Response(status, text="<Key>kyc/x</Key>"))
    )
    with pytest.raises(error) as raised:
        await storage.read(KEY, sha256=sha256_hex(PNG))
    assert "kyc" not in str(raised.value)


async def test_spaces_unreachable_is_unavailable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(StorageUnavailable):
        await spaces(httpx.MockTransport(handle)).put(NewFile(KEY, PNG, "image/png"))


async def test_spaces_exists_and_delete_treat_missing_as_absent() -> None:
    storage = spaces(httpx.MockTransport(lambda request: httpx.Response(404)))
    assert not await storage.exists(KEY)
    await storage.delete(KEY)


def test_spaces_read_link_is_short_lived_and_served_inline() -> None:
    storage = spaces(httpx.MockTransport(lambda request: httpx.Response(200)))
    link = storage.read_link(KEY, content_type="image/jpeg", lifetime=timedelta(minutes=5), now=NOW)
    url = urlsplit(link.url)
    query = parse_qs(url.query)
    assert url.netloc == "finvest-staging-documents.lon1.digitaloceanspaces.com"
    assert url.path == f"/{KEY}"
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-Date"] == ["20261113T120000Z"]
    assert query["response-content-type"] == ["image/jpeg"]
    assert query["response-content-disposition"] == ["inline"]
    assert "not-a-real-secret" not in link.url
    assert link.expires_at == NOW + timedelta(minutes=5)


def test_spaces_refuses_plain_http_outside_this_machine() -> None:
    with pytest.raises(ValueError):
        spaces(
            httpx.MockTransport(lambda request: httpx.Response(200)),
            endpoint="http://spaces.example",
        )


def test_settings_build_spaces_from_what_terraform_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in {
        "FINVEST_OBJECT_STORAGE_BACKEND": "s3",
        "FINVEST_OBJECT_STORAGE_BUCKET": "finvest-production-documents",
        "FINVEST_OBJECT_STORAGE_ENDPOINT": "https://lon1.digitaloceanspaces.com",
        "FINVEST_OBJECT_STORAGE_REGION": "lon1",
        "FINVEST_OBJECT_STORAGE_ACCESS_KEY_ID": "DO00EXAMPLE",
        "FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY": "not-a-real-secret",
        "FINVEST_ENVIRONMENT": "production",
    }.items():
        monkeypatch.setenv(name, value)
    storage = StorageSettings().storage()
    assert isinstance(storage, SpacesStorage)
    assert storage.host == "finvest-production-documents.lon1.digitaloceanspaces.com"
    assert "not-a-real-secret" not in repr(StorageSettings())


def test_settings_need_every_s3_value() -> None:
    with pytest.raises(ValidationError, match="FINVEST_OBJECT_STORAGE_SECRET_ACCESS_KEY"):
        StorageSettings(
            backend="s3",
            bucket="finvest-staging-documents",
            endpoint="https://lon1.digitaloceanspaces.com",
            region="lon1",
            access_key_id="DO00EXAMPLE",
        )


def test_memory_storage_is_refused_when_deployed() -> None:
    with pytest.raises(ValidationError):
        StorageSettings(backend="memory", FINVEST_ENVIRONMENT="staging")
    assert isinstance(StorageSettings(backend="memory").storage(), MemoryStorage)


def test_unset_storage_is_disabled() -> None:
    assert isinstance(StorageSettings().storage(), DisabledStorage)
