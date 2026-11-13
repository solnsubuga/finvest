"""File storage settings, read from `FINVEST_OBJECT_STORAGE_*` by the API and the worker.

Terraform writes the bucket, endpoint, region and a key scoped to that one bucket into each
GitHub environment, and the deploy passes them on (infra/README.md). `none` fails closed.
`memory` is for tests and a developer's machine and is refused anywhere deployed, because its
files vanish when the process stops.
"""

import re
from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.storage.files import DisabledStorage, FileStorage, MemoryStorage
from app.infrastructure.storage.sigv4 import Credentials
from app.infrastructure.storage.spaces import SpacesStorage

BUCKET = re.compile(r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")
REGION = re.compile(r"^[a-z0-9-]{2,32}$")


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_OBJECT_STORAGE_", extra="ignore", hide_input_in_errors=True
    )

    backend: Literal["none", "memory", "s3"] = "none"
    bucket: str | None = None
    endpoint: str | None = None
    region: str | None = None
    access_key_id: str | None = None
    secret_access_key: SecretStr | None = None
    # Local S3-compatible servers address the bucket in the path. Spaces does not need it.
    path_style: bool = False
    timeout_seconds: float = Field(default=30, gt=0, le=120)
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = Field(default=None, validation_alias="FINVEST_ENVIRONMENT")

    @field_validator(
        "bucket", "endpoint", "region", "access_key_id", "secret_access_key", mode="before"
    )
    @classmethod
    def blank_is_unset(cls, value: object) -> object:
        # Compose passes unset variables as empty strings.
        return None if value == "" else value

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.backend == "memory" and self.environment not in (None, "", "local"):
            raise ValueError("The memory file storage may only run on a developer's machine")
        if self.backend != "s3":
            return self
        missing = [
            name
            for name in ("bucket", "endpoint", "region", "access_key_id", "secret_access_key")
            if getattr(self, name) is None
        ]
        if missing:
            names = ", ".join(f"FINVEST_OBJECT_STORAGE_{n.upper()}" for n in missing)
            raise ValueError(f"S3 file storage needs {names}")
        if not BUCKET.fullmatch(self.bucket or ""):
            raise ValueError("FINVEST_OBJECT_STORAGE_BUCKET is not a valid bucket name")
        if not REGION.fullmatch(self.region or ""):
            raise ValueError("FINVEST_OBJECT_STORAGE_REGION is not valid")
        return self

    def storage(self) -> FileStorage:
        if self.backend == "memory":
            return MemoryStorage()
        if (
            self.backend == "none"
            or self.bucket is None
            or self.endpoint is None
            or self.region is None
            or self.access_key_id is None
            or self.secret_access_key is None
        ):
            return DisabledStorage()
        return SpacesStorage(
            bucket=self.bucket,
            endpoint=self.endpoint,
            region=self.region,
            credentials=Credentials(self.access_key_id, self.secret_access_key),
            path_style=self.path_style,
            timeout=self.timeout_seconds,
        )
