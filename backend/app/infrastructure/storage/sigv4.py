"""AWS Signature Version 4 for S3 requests, which DigitalOcean Spaces accepts.

Written against https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-authenticating-requests.html
and checked against AWS's published examples (tests/unit/test_storage.py). Kept here rather than
adding an S3 SDK: four requests and a presigned link are all the backend needs.
"""

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote

from pydantic import SecretStr

ALGORITHM = "AWS4-HMAC-SHA256"
UNSIGNED = "UNSIGNED-PAYLOAD"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Credentials:
    access_key_id: str
    secret_access_key: SecretStr

    def __repr__(self) -> str:
        return "Credentials(<hidden>)"


def uri_encode(value: str, *, keep_slash: bool = False) -> str:
    """Percent-encode everything except unreserved characters, as SigV4 requires."""
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def canonical_query(params: Mapping[str, str]) -> str:
    pairs = sorted((uri_encode(k), uri_encode(v)) for k, v in params.items())
    return "&".join(f"{k}={v}" for k, v in pairs)


def amz_date(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _hmac(key: bytes, text: str) -> bytes:
    return hmac.new(key, text.encode(), hashlib.sha256).digest()


def signing_key(secret: str, day: str, region: str, service: str = "s3") -> bytes:
    key = _hmac(f"AWS4{secret}".encode(), day)
    key = _hmac(key, region)
    key = _hmac(key, service)
    return _hmac(key, "aws4_request")


def signature(
    credentials: Credentials,
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    payload_hash: str,
    moment: datetime,
    region: str,
) -> tuple[str, str]:
    """The signature and the signed header list for one request. `path` is already encoded."""
    lowered = {name.lower(): " ".join(value.strip().split()) for name, value in headers.items()}
    names = sorted(lowered)
    canonical = "\n".join(
        [
            method,
            path,
            canonical_query(query),
            "".join(f"{name}:{lowered[name]}\n" for name in names),
            ";".join(names),
            payload_hash,
        ]
    )
    stamp = amz_date(moment)
    scope = f"{stamp[:8]}/{region}/s3/aws4_request"
    to_sign = "\n".join([ALGORITHM, stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = signing_key(credentials.secret_access_key.get_secret_value(), stamp[:8], region)
    return hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest(), ";".join(names)


def authorization(
    credentials: Credentials,
    *,
    method: str,
    path: str,
    query: Mapping[str, str],
    headers: Mapping[str, str],
    payload_hash: str,
    moment: datetime,
    region: str,
) -> str:
    """The Authorization header. `headers` must hold host, x-amz-date and x-amz-content-sha256."""
    signed, names = signature(
        credentials,
        method=method,
        path=path,
        query=query,
        headers=headers,
        payload_hash=payload_hash,
        moment=moment,
        region=region,
    )
    scope = f"{amz_date(moment)[:8]}/{region}/s3/aws4_request"
    return (
        f"{ALGORITHM} Credential={credentials.access_key_id}/{scope}, "
        f"SignedHeaders={names}, Signature={signed}"
    )


def presigned_query(
    credentials: Credentials,
    *,
    method: str,
    host: str,
    path: str,
    extra: Mapping[str, str],
    expires_seconds: int,
    moment: datetime,
    region: str,
) -> str:
    """The query string of a presigned link: anyone holding it may make that one request."""
    stamp = amz_date(moment)
    params = {
        **extra,
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": f"{credentials.access_key_id}/{stamp[:8]}/{region}/s3/aws4_request",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "host",
    }
    signed, _ = signature(
        credentials,
        method=method,
        path=path,
        query=params,
        headers={"host": host},
        payload_hash=UNSIGNED,
        moment=moment,
        region=region,
    )
    return canonical_query({**params, "X-Amz-Signature": signed})
