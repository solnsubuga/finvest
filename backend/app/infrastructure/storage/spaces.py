"""DigitalOcean Spaces (S3 compatible) storage, signed with SigV4 over httpx."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from app.infrastructure.storage import sigv4
from app.infrastructure.storage.files import (
    CONTENT_TYPE,
    FileNotFound,
    NewFile,
    ReadLink,
    StorageRefused,
    StorageUnavailable,
    StoredFile,
    check_digest,
    check_key,
    check_lifetime,
)


class SpacesStorage:
    """One private bucket. Virtual-hosted addresses (`<bucket>.lon1.digitaloceanspaces.com`)
    unless `path_style` is set, which local S3-compatible servers need."""

    name = "s3"

    def __init__(
        self,
        *,
        bucket: str,
        endpoint: str,
        region: str,
        credentials: sigv4.Credentials,
        path_style: bool = False,
        timeout: float = 30,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parts = urlsplit(endpoint)
        if (
            parts.scheme not in ("https", "http")
            or not parts.hostname
            or parts.path not in ("", "/")
        ):
            raise ValueError("The storage endpoint must be a scheme and host only")
        if parts.scheme == "http" and parts.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("The storage endpoint must use https")
        self.bucket = bucket
        self.region = region
        self.credentials = credentials
        self.timeout = timeout
        self._client = client
        authority = parts.netloc
        if path_style:
            self.origin = f"{parts.scheme}://{authority}"
            self.prefix = f"/{bucket}"
        else:
            self.origin = f"{parts.scheme}://{bucket}.{authority}"
            self.prefix = ""
        self.host = urlsplit(self.origin).netloc

    def _path(self, key: str) -> str:
        return f"{self.prefix}/{sigv4.uri_encode(check_key(key), keep_slash=True)}"

    async def _send(
        self,
        method: str,
        key: str,
        *,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        moment = datetime.now(UTC)
        path = self._path(key)
        payload_hash = sigv4.sha256_hex(body) if body else sigv4.EMPTY_SHA256
        signed = {
            "host": self.host,
            "x-amz-date": sigv4.amz_date(moment),
            "x-amz-content-sha256": payload_hash,
            **(headers or {}),
        }
        signed["authorization"] = sigv4.authorization(
            self.credentials,
            method=method,
            path=path,
            query={},
            headers=signed,
            payload_hash=payload_hash,
            moment=moment,
            region=self.region,
        )
        request_headers = {k: v for k, v in signed.items() if k != "host"}
        try:
            if self._client is not None:
                return await self._client.request(
                    method,
                    self.origin + path,
                    content=body or None,
                    headers=request_headers,
                    timeout=self.timeout,
                )
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                return await client.request(
                    method, self.origin + path, content=body or None, headers=request_headers
                )
        except httpx.HTTPError as error:
            raise StorageUnavailable(
                f"File storage is unreachable ({type(error).__name__})"
            ) from None

    @staticmethod
    def _sort(response: httpx.Response, action: str) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 404:
            raise FileNotFound("No file is stored under this key")
        if status in (401, 403):
            raise StorageRefused(
                f"File storage refused to {action} ({status})", code="storage_auth_failed"
            )
        if status in (408, 429) or status >= 500:
            raise StorageUnavailable(f"File storage could not {action} ({status})")
        raise StorageRefused(f"File storage refused to {action} ({status})")

    async def put(self, file: NewFile) -> StoredFile:
        # The store checks the body against x-amz-content-sha256, so a damaged upload is refused.
        response = await self._send(
            "PUT",
            file.key,
            body=file.data,
            headers={"content-type": file.content_type},
        )
        self._sort(response, "store a file")
        return StoredFile(file.key, file.content_type, file.byte_size, file.sha256)

    async def read(self, key: str, *, sha256: str) -> bytes:
        response = await self._send("GET", key)
        self._sort(response, "read a file")
        return check_digest(response.content, sha256)

    async def exists(self, key: str) -> bool:
        response = await self._send("HEAD", key)
        if response.status_code == 404:
            return False
        self._sort(response, "look up a file")
        return True

    async def delete(self, key: str) -> None:
        response = await self._send("DELETE", key)
        if response.status_code == 404:
            return
        self._sort(response, "delete a file")

    def read_link(
        self, key: str, *, content_type: str, lifetime: timedelta, now: datetime
    ) -> ReadLink:
        if not CONTENT_TYPE.fullmatch(content_type):
            raise ValueError("A content type is not valid")
        seconds = check_lifetime(lifetime)
        path = self._path(key)
        # Served inline with the recorded type, so an image opens in the console and nothing
        # the store guesses decides how a browser treats the file.
        query = sigv4.presigned_query(
            self.credentials,
            method="GET",
            host=self.host,
            path=path,
            extra={
                "response-content-type": content_type,
                "response-content-disposition": "inline",
                "response-cache-control": "private, no-store",
            },
            expires_seconds=seconds,
            moment=now,
            region=self.region,
        )
        return ReadLink(
            f"{self.origin}{path}?{query}", now.astimezone(UTC) + timedelta(seconds=seconds)
        )
