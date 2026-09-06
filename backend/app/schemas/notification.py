"""
================================================================================
schemas/notification.py  ▸  Telegram notification wire shapes
================================================================================
Request/response models for the notification endpoints. camelCase aliases match
the frontend contract.
================================================================================
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LinkTelegramRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Optional phone number, stored for future SMS/WhatsApp channels. Telegram
    # delivery itself rides on the chat_id captured via the /start handshake.
    phone: str | None = Field(default=None, max_length=20)


class LinkTelegramResponse(BaseModel):
    """The deep link the user opens to complete the handshake."""

    model_config = ConfigDict(populate_by_name=True)

    linked: bool
    deep_link: str = Field(serialization_alias="deepLink")
    bot_username: str = Field(serialization_alias="botUsername")


class TelegramStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    linked: bool
