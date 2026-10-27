"""Chats and group chats, their messages and attachments."""

from typing import Literal

from pydantic import BaseModel, Field


class RoomStop(BaseModel):
    after: int  # bot replies in a row, the room's limit
    waiting: list[str]  # who the last reply mentioned: "Continue" wakes them
    owed: list[str] = []  # of those, the ones mentioned by name, who owe a reply


class RoomOut(BaseModel):
    id: str
    title: str
    lead: str
    members: list[str]
    created_at: str
    updated_at: str
    max_depth: int | None = None  # bot replies in a row before they stop; None for the crew's default (4)
    stopped: RoomStop | None = None  # set once the bots stopped, until the user carries on or says something
    muted: bool = False  # no notifications from it
    archived_at: str | None = None  # while it's archived
    crew: bool = False  # the crew's own group chat, with every bot: it can't be archived or deleted

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
    pinned: int | None = None  # its place among the chats pinned to the top of the sidebar


class Pins(BaseModel):
    pins: list[str]  # chats' keys, in the order they're pinned


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


class RoomChange(BaseModel):
    """What to change about a group chat; anything left out stays as it is."""

    title: str | None = None
    lead: str | None = None
    members: list[str] | None = None
    max_depth: int | None = Field(None, ge=1, le=20)  # null for the crew's default
    muted: bool | None = None
    archived: bool | None = None
