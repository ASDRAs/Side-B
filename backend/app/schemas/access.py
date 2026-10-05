from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

AccessAction = Literal["approve", "reject", "block", "unblock", "reopen"]
ListableStatus = Literal["pending", "approved", "rejected", "blocked"]


class AccessRequestBody(BaseModel):
    """Self-service requests carry no input.

    The UID, email and display name come only from the verified token. Any
    field such as ``uid``, ``email``, ``status`` or ``role`` is rejected (422).
    """

    model_config = ConfigDict(extra="forbid")


class AccessDecisionBody(BaseModel):
    """An allowlisted action bound to a revision and an idempotency key.

    Requester identity, role, timestamps and the deciding administrator are
    derived on the server. ``extra="forbid"`` rejects ``role``, ``email``,
    ``decided_by``, ``status`` and similar injected fields with 422.
    """

    model_config = ConfigDict(extra="forbid")

    action: AccessAction
    expected_revision: int = Field(ge=0, le=2**53 - 1, strict=True)
    operation_id: UUID
