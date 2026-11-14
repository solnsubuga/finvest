"""ioTec Verify phone number verification: the name a mobile network holds for a number.

`POST /api/verify-phone-number` with the number as ioTec writes it (0772123456) and our lookup's
ID as `referenceId`, sent in the background (`isBlocking: false`). `GET
/api/verify-phone-number/{id}` then reads it:

| `status` | `verificationStatus` | Read as |
| --- | --- | --- |
| Pending, Processing | any | Not answered yet |
| Completed | Success, with a `registeredName` | The network's name |
| Completed | NotFound, or Barred | No name: the number is refused |
| Completed | Failed, Pending, missing or unknown | No answer: the client may ask again |
| Failed, Expired, Cancelled, unknown | any | No answer |

Anything not understood is never read as a name. Nothing ioTec sends is logged or put in an
error, because it names the client.
"""

from app.infrastructure.iotec_verify import (
    IotecVerifyClient,
    Sent,
    VerifyUnavailable,
    is_request_id,
)
from app.modules.payouts.domain.name_lookups import (
    Accepted,
    Found,
    LookupDeferred,
    NoAnswer,
    NoName,
    NotReceived,
    Pending,
    Unanswerable,
)

PATH = "/api/verify-phone-number"
_WAITING = frozenset({"Pending", "Processing"})
_NO_NAME = frozenset({"NotFound", "Barred"})


class IotecRegisteredNames:
    name = "iotec-verify"

    def __init__(self, client: IotecVerifyClient) -> None:
        self._client = client

    async def send(self, msisdn: str, reference: str) -> Accepted | NotReceived | Unanswerable:
        result = await self._client.send(
            PATH,
            {
                "phoneNumber": "0" + msisdn[3:],
                "referenceId": reference,
                "isBlocking": False,
            },
        )
        if result.sent is Sent.ACCEPTED and result.request_id is not None:
            return Accepted(result.request_id)
        if result.sent is Sent.NOT_RECEIVED:
            return NotReceived()
        if result.sent is Sent.REFUSED:
            return Unanswerable(f"iotec_refused_{result.status}")
        # It may have arrived, and ioTec cannot look a request up by our reference.
        return Unanswerable("iotec_uncertain")

    async def read(self, request_id: str) -> Pending | Found | NoName | NoAnswer:
        if not is_request_id(request_id):
            raise ValueError("Not an ioTec Verify request ID")
        try:
            answer = await self._client.read(f"{PATH}/{request_id}")
        except VerifyUnavailable as error:
            raise LookupDeferred(str(error)) from None
        status = answer.get("status")
        if status in _WAITING:
            return Pending()
        if status != "Completed":
            return NoAnswer("iotec_request_ended")
        found = answer.get("verificationStatus")
        if found in _NO_NAME:
            return NoName()
        name = answer.get("registeredName")
        if found == "Success" and isinstance(name, str) and name.strip():
            return Found(name.strip())
        return NoAnswer("iotec_no_result")
