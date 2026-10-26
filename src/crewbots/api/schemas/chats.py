"""Chats and group chats, their messages and attachments."""

from typing import Literal

from pydantic import BaseModel


class RoomOut(BaseModel):
    id: str
    title: str
    lead: str
    members: list[str]
    created_at: str
    updated_at: str

class AttachmentOut(BaseModel):
    id: str
    name: str
    path: str  # in the crew's folder, where bots read it
    mime: str
    size: int
    kind: Literal["image", "file"]

class RoomMessageOut(BaseModel):
    id: int
    room_id: str
    author: str  # user, or a bot's name
    text: str
    depth: int
    at: str
    attachments: list[AttachmentOut] = []

class LastMessage(BaseModel):
    author: str
    text: str
    at: str

class ChatOut(BaseModel):
    key: str  # bot:<name> or room:<id>
    kind: Literal["direct", "group"]
    title: str
    bot: str | None  # a direct chat's bot
    task_id: str | None  # a direct chat's task, whose events are the conversation
    room: RoomOut | None
    last: LastMessage | None
    unread: int
    working: bool
    updated_at: str

class AttachmentUpload(BaseModel):
    name: str
    data: str  # base64

class AttachmentCopy(BaseModel):
    path: str  # a file you picked

class AttachmentData(BaseModel):
    name: str
    mime: str
    data: str  # base64

class NewRoom(BaseModel):
    title: str = ""
    members: list[str]
    lead: str | None = None
