"""Messenger attachment download (port of attachment.js fetchAttachmentImage).

The scontent URL Meta hands us is pre-signed, so no access token is needed. Returns
None when it isn't an image we can read (a video, a PDF, an oversized file); raises
when the download itself fails. Callers treat both as "we don't know" → fail open.
"""

from __future__ import annotations

import re

import httpx

ATTACHMENT_MAX_BYTES = 8 * 1024 * 1024
ATTACHMENT_FETCH_S = 15
IMAGE_TYPES = re.compile(r"^image/(jpeg|jpg|png|webp|heic|heif|gif)$")


async def fetch_attachment_image(url: str) -> tuple[str, bytes] | None:
    async with httpx.AsyncClient(timeout=ATTACHMENT_FETCH_S, follow_redirects=True) as client:
        res = await client.get(url)
    if res.status_code >= 400:
        raise RuntimeError(f"attachment fetch responded {res.status_code}")
    mime = (res.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not IMAGE_TYPES.match(mime):
        return None
    data = res.content
    if len(data) > ATTACHMENT_MAX_BYTES:
        return None
    return ("image/jpeg" if mime == "image/jpg" else mime, data)


async def classify_attachment(url: str):
    """Fetch and classify. None = unreadable attachment; raises on fetch/model failure."""
    from app.agents.specialists import classify_image

    image = await fetch_attachment_image(url)
    if image is None:
        return None
    mime, data = image
    return await classify_image(mime, data)
