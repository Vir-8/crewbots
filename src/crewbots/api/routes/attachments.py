"""Files sent with messages, kept in the crew's attachments/ folder."""

import asyncio
import base64
import binascii
from pathlib import Path

from fastapi import APIRouter, HTTPException

from crewbots import attachments as attachments_module
from crewbots.api.context import Context
from crewbots.api.schemas import AttachmentCopy, AttachmentData, AttachmentOut, AttachmentUpload


def routes(c: Context) -> APIRouter:
    runner = c.runner
    v1 = APIRouter()

    @v1.post("/attachments", response_model=AttachmentOut, status_code=201)
    async def upload_attachment(body: AttachmentUpload):
        """Keeps a file sent from the app (a pasted image, a dropped file), for a message to carry."""
        try:
            return attachments_module.save(runner.crew.home, body.name, base64.b64decode(body.data))
        except (attachments_module.AttachmentError, binascii.Error) as e:
            raise HTTPException(422, str(e)) from None

    @v1.post("/attachments/copy", response_model=AttachmentOut, status_code=201)
    async def copy_attachment(body: AttachmentCopy):
        """Keeps a copy of a file you picked."""
        try:
            return await asyncio.to_thread(attachments_module.copy_in, runner.crew.home, Path(body.path))
        except attachments_module.AttachmentError as e:
            raise HTTPException(422, str(e)) from None

    @v1.get("/attachments/{attachment_id}", response_model=AttachmentData)
    async def attachment(attachment_id: str):
        """An attachment's bytes, for the app to show an image."""
        try:
            found = attachments_module.find(runner.crew.home, attachment_id)
        except attachments_module.AttachmentError as e:
            raise HTTPException(404, str(e)) from None
        data = await asyncio.to_thread(Path(found["path"]).read_bytes)
        return {"name": found["name"], "mime": found["mime"], "data": base64.b64encode(data).decode()}

    return v1
