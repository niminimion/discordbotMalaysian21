"""
card_renderer.py — Fetch card PNG images and composite into a single hand image.
---------------------------------------------------------------------------------
Uses the free Deck of Cards API (no API key required):
  https://deckofcardsapi.com/static/img/{code}.png

Returns a plain io.BytesIO PNG buffer — no discord imports.
Callers wrap it:  discord.File(buffer, filename="hand.png")

Falls back to None if all fetches fail (network down, API unavailable, etc.).
Callers should send a text-only message in that case.
"""

import asyncio
import io

import aiohttp
from PIL import Image

from blackjack import Hand


BASE_URL  = "https://deckofcardsapi.com/static/img/{code}.png"
CARD_H    = 150    # resize each card to this height (pixels)
CARD_GAP  = 6      # gap between cards (pixels)
BG_COLOUR = (47, 49, 54)   # Discord dark theme background (RGB)


async def _fetch_one(session: aiohttp.ClientSession, code: str) -> Image.Image | None:
    """
    Fetch a single card PNG and return a Pillow Image scaled to CARD_H.
    Returns None on any network or decoding error.
    """
    url = BASE_URL.format(code=code)
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.read()
                img  = Image.open(io.BytesIO(data)).convert("RGBA")
                # Resize maintaining aspect ratio
                ratio     = CARD_H / img.height
                new_width = int(img.width * ratio)
                return img.resize((new_width, CARD_H), Image.LANCZOS)
    except Exception:
        pass
    return None


async def render_hand_image(hand: Hand) -> io.BytesIO | None:
    """
    Fetch all cards in the hand in parallel, stitch them side-by-side,
    and return a BytesIO PNG buffer.

    Returns None if every card fetch failed (caller falls back to text-only).

    Usage in main.py:
        buf = await render_hand_image(hand)
        if buf:
            await interaction.followup.send(
                "Your hand: ...",
                file=discord.File(buf, "hand.png"),
                ephemeral=True,
            )
        else:
            await interaction.followup.send("Your hand: ...", ephemeral=True)
    """
    if not hand.cards:
        return None

    async with aiohttp.ClientSession() as session:
        # Fetch all card images concurrently — same pattern as parallel I2C reads
        results = await asyncio.gather(
            *[_fetch_one(session, card.image_code) for card in hand.cards]
        )

    images = [img for img in results if img is not None]
    if not images:
        return None

    # Stitch images side by side onto a single canvas
    total_w = sum(img.width for img in images) + CARD_GAP * (len(images) - 1)
    canvas  = Image.new("RGB", (total_w, CARD_H), BG_COLOUR)

    x = 0
    for img in images:
        # paste() with mask=img preserves PNG transparency over the background
        canvas.paste(img, (x, 0), img)
        x += img.width + CARD_GAP

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
