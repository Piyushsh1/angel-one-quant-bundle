"""
================================================================================
schemas/broker.py  ▸  Broker connection contracts
================================================================================
Matches the frontend's connect contract in
`frontend/src/features/onboarding/api/broker.api.ts`:

    POST /api/v1/broker/connect
      body:  { brokerId, credentials: { <field>: <value> } }
      200:   { connected, brokerId, maskedClientId?, accountName?, availableMargin? }

Only safe-to-display values ever come back. Credentials are write-only: they go
in once and are never returned.
================================================================================
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ConnectBrokerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    broker_id: str = Field(alias="brokerId")
    #: field-name → value, shaped by the broker's credential list.
    credentials: dict[str, str]


class ConnectBrokerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connected: bool
    broker_id: str = Field(serialization_alias="brokerId")
    masked_client_id: str | None = Field(
        default=None, serialization_alias="maskedClientId"
    )
    account_name: str | None = Field(default=None, serialization_alias="accountName")
    available_margin: float | None = Field(
        default=None, serialization_alias="availableMargin"
    )


class BrokerStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connected: bool
    broker_id: str | None = Field(default=None, serialization_alias="brokerId")
    masked_client_id: str | None = Field(
        default=None, serialization_alias="maskedClientId"
    )
    account_name: str | None = Field(default=None, serialization_alias="accountName")
    last_verified_at: str | None = Field(
        default=None, serialization_alias="lastVerifiedAt"
    )


class BrokerAccountResponse(BaseModel):
    """A connected broker account as shown on the Broker Integrations page.

    Sourced from the real BrokerConnection row — only safe-to-display fields.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    broker_id: str = Field(serialization_alias="brokerId")
    masked_client_id: str | None = Field(
        default=None, serialization_alias="maskedClientId"
    )
    account_name: str | None = Field(default=None, serialization_alias="accountName")
    status: str  # "connected" | "error"
    connected_at: str = Field(serialization_alias="connectedAt")
