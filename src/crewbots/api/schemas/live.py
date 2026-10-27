"""What /v1/live sends."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from crewbots.api.schemas.chats import RoomMessageOut, RoomOut
from crewbots.api.schemas.crew import CrewState
from crewbots.api.schemas.tasks import EventOut, TaskOut


class LiveCrew(BaseModel):
    type: Literal["crew"]
    crew: CrewState

class LiveRoom(BaseModel):
    type: Literal["room"]
    room: RoomOut

class LiveRoomGone(BaseModel):
    type: Literal["room_gone"]
    room_id: str

class LiveRoomMessage(BaseModel):
    type: Literal["room_message"]
    message: RoomMessageOut

class LivePins(BaseModel):
    type: Literal["pins"]
    pins: list[str]

class LivePage(BaseModel):
    type: Literal["page"]
    bot: str
    page: str  # its name: written by the bot, or by the user from the app

class LiveBots(BaseModel):
    type: Literal["bots"]  # the crew's role cards changed (a bot named in its chat, one added, a card edited): read them again

class LiveTask(BaseModel):
    type: Literal["task"]
    task: TaskOut

class LiveEvent(BaseModel):
    type: Literal["event"]
    event: EventOut

LiveMessage = Annotated[LiveTask | LiveEvent | LiveCrew | LiveBots | LiveRoom | LiveRoomGone | LiveRoomMessage | LivePins
                        | LivePage, Field(discriminator="type")]
