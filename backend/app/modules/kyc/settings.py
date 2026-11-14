"""Identity check configuration. Read by the API and the worker, the two processes that open
identity details."""

from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.sealing import Sealer, key_from_text
from app.infrastructure.storage import FileStorage
from app.modules.kyc.domain.face_confirmations import FaceMatcher
from app.modules.kyc.domain.liveness import LivenessProvider
from app.modules.kyc.domain.poses import FaceQuality
from app.modules.kyc.domain.provider import IdentityProvider
from app.modules.kyc.iotec import BASE_URL, IotecVerifyProvider
from app.modules.kyc.iotec_face_match import IotecFaceMatcher
from app.modules.kyc.iotec_liveness import IotecLivenessProvider
from app.modules.kyc.providers import (
    DevelopmentStubFaceMatcher,
    DevelopmentStubLiveness,
    DevelopmentStubProvider,
    ManualProvider,
)
from app.modules.kyc.regula import RegulaProvider, private_or_https
from app.modules.kyc.regula_quality import RegulaFaceQuality


class KycSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    # 32 random bytes in base64url, one per environment. It seals document numbers, dates of
    # birth and TINs. Losing it loses them, and changing it makes every saved check unreadable.
    identity_data_key: SecretStr | None = None
    # `manual` sends every client to a person and runs no jobs. `regula` is Regula, run on
    # Finvest's own servers. `iotec` is ioTec Verify. `development-stub` is for a developer's
    # own machine.
    identity_provider: Literal["manual", "regula", "iotec", "development-stub"] = "manual"
    # Where Finvest's own Regula services answer, such as http://regula-docreader:8080. Plain
    # http only on the private network. Needed for `regula`.
    regula_document_reader_url: str | None = None
    regula_face_url: str | None = None
    # From the ioTec Verify portal, Management > Settings > Configuration. Needed for `iotec`.
    iotec_verify_client_id: str | None = None
    iotec_verify_api_key: SecretStr | None = None
    iotec_verify_base_url: str = BASE_URL
    # The owner's decision (docs/decisions.md, 2026-11-14): a client whose automatic checks all
    # passed is approved with no person involved. Unset, it is on with Regula and ioTec Verify
    # and off for the other providers, which run no real checks. `false` sends everyone to a person.
    identity_automatic_approval: bool | None = None
    # `staging` or `production` where deployed (infra/README.md). Unset on a developer's machine.
    environment: str | None = None

    @field_validator("identity_data_key")
    @classmethod
    def require_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            # key_from_text never puts the value in its error.
            key_from_text(value.get_secret_value())
        return value

    @model_validator(mode="after")
    def keep_the_stub_harmless(self) -> Self:
        if self.identity_provider != "manual" and self.identity_data_key is None:
            raise ValueError("An identity provider needs FINVEST_IDENTITY_DATA_KEY")
        if self.identity_provider == "development-stub" and self.environment == "production":
            # Staff would see passes for checks nobody ran, and approve on them.
            raise ValueError("The development stub checks nothing and may not run in production")
        if self.identity_provider == "development-stub" and self.identity_automatic_approval:
            raise ValueError("The development stub checks nothing and may not approve clients")
        if self.identity_provider == "regula":
            if not self.regula_document_reader_url or not self.regula_face_url:
                raise ValueError(
                    "Regula needs FINVEST_REGULA_DOCUMENT_READER_URL and FINVEST_REGULA_FACE_URL"
                )
            for url in (self.regula_document_reader_url, self.regula_face_url):
                if not private_or_https(url):
                    raise ValueError("Regula must be reached over https or the private network")
        if self.identity_provider == "iotec":
            if not self.iotec_verify_client_id or self.iotec_verify_api_key is None:
                raise ValueError(
                    "ioTec Verify needs FINVEST_IOTEC_VERIFY_CLIENT_ID and "
                    "FINVEST_IOTEC_VERIFY_API_KEY"
                )
            if not self.iotec_verify_base_url.startswith("https://"):
                raise ValueError("FINVEST_IOTEC_VERIFY_BASE_URL must use https")
        return self

    @property
    def automatic_approval(self) -> bool:
        """Whether the worker approves a client whose automatic checks all passed."""
        if self.identity_automatic_approval is None:
            return self.identity_provider in ("regula", "iotec")
        return self.identity_automatic_approval

    def sealer(self) -> Sealer:
        if self.identity_data_key is None:
            raise ValueError("FINVEST_IDENTITY_DATA_KEY is not set")
        return Sealer(key_from_text(self.identity_data_key.get_secret_value()), purpose="identity")

    def provider(self, storage: FileStorage) -> IdentityProvider:
        """The provider the worker sends to. It reads the photos from `storage`."""
        if self.identity_provider == "regula":
            assert self.regula_document_reader_url and self.regula_face_url  # checked above
            return RegulaProvider(
                document_reader_url=self.regula_document_reader_url,
                face_url=self.regula_face_url,
                storage=storage,
            )
        if self.identity_provider == "iotec":
            assert self.iotec_verify_client_id and self.iotec_verify_api_key  # checked above
            return IotecVerifyProvider(
                client_id=self.iotec_verify_client_id,
                api_key=self.iotec_verify_api_key,
                storage=storage,
                base_url=self.iotec_verify_base_url,
            )
        if self.identity_provider == "development-stub":
            return DevelopmentStubProvider()
        return ManualProvider()

    def face_quality(self) -> FaceQuality | None:
        """The face quality service for the guided selfie, or None for a plain selfie.

        With Regula the selfie is the guided one: a straight-on selfie and four pose frames,
        each kept only if Regula's face quality check passed it (the owner, 2026-11-14).
        """
        if self.identity_provider == "regula":
            assert self.regula_face_url  # checked above
            return RegulaFaceQuality(face_url=self.regula_face_url)
        return None

    def liveness(self) -> LivenessProvider | None:
        """The liveness provider, or None when clients can only send a camera selfie."""
        if self.identity_provider == "iotec":
            assert self.iotec_verify_client_id and self.iotec_verify_api_key  # checked above
            return IotecLivenessProvider(
                client_id=self.iotec_verify_client_id,
                api_key=self.iotec_verify_api_key,
                base_url=self.iotec_verify_base_url,
            )
        if self.identity_provider == "development-stub":
            return DevelopmentStubLiveness()
        return None

    def face_matcher(self, storage: FileStorage) -> FaceMatcher | None:
        """The face match for face confirmations, or None when liveness is off.

        Always paired with `liveness()`: a live capture with nothing to compare it to, or a
        comparison of a photo the client chose, would not prove who is there.
        """
        if self.identity_provider == "iotec":
            assert self.iotec_verify_client_id and self.iotec_verify_api_key  # checked above
            return IotecFaceMatcher(
                client_id=self.iotec_verify_client_id,
                api_key=self.iotec_verify_api_key,
                storage=storage,
                base_url=self.iotec_verify_base_url,
            )
        if self.identity_provider == "development-stub":
            return DevelopmentStubFaceMatcher()
        return None
