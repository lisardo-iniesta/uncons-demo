"""AnkiConnect adapter for flashcard operations."""

import asyncio
import base64
import html
import logging
import os
import re
from typing import Any

import httpx

from src.domain.entities.card import Card
from src.domain.value_objects.deck_stats import DeckStats
from src.domain.value_objects.rating import Rating

logger = logging.getLogger(__name__)


class AnkiConnectError(Exception):
    """AnkiConnect API error."""

    pass


class AnkiConnectAdapter:
    """AnkiConnect API adapter implementing FlashcardService protocol.

    Communicates with Anki desktop via AnkiConnect addon API.
    Uses lazy client initialization for connection reuse.
    """

    def __init__(
        self,
        url: str = "http://anki:8765",
        timeout: float = 5.0,
    ):
        """Initialize adapter.

        Args:
            url: AnkiConnect API URL (default: Docker service name)
            timeout: Request timeout in seconds
        """
        self._url = url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy client initialization for connection reuse."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _invoke(self, action: str, **params: Any) -> Any:
        """Call AnkiConnect action.

        Args:
            action: AnkiConnect action name
            **params: Action parameters

        Returns:
            Action result

        Raises:
            AnkiConnectError: If API returns an error
        """
        client = await self._get_client()
        payload = {"action": action, "version": 6, "params": params}
        response = await client.post(self._url, json=payload)
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            raise AnkiConnectError(result["error"])
        return result.get("result")

    async def wait_for_connection(
        self,
        max_retries: int = 10,
        retry_delay: float = 1.0,
    ) -> bool:
        """Wait for Anki to become available with retries.

        Args:
            max_retries: Maximum number of connection attempts
            retry_delay: Seconds to wait between retries

        Returns:
            True if connection successful, False if all retries exhausted
        """
        print(f"[AnkiConnect] Waiting for connection to {self._url}...")

        for attempt in range(max_retries):
            try:
                # Simple connectivity test - request version
                client = await self._get_client()
                response = await client.post(
                    self._url,
                    json={"action": "version", "version": 6},
                )
                if response.status_code == 200:
                    print(f"[AnkiConnect] Connected (attempt {attempt + 1}/{max_retries})")
                    logger.info(f"AnkiConnect available (attempt {attempt + 1})")
                    return True
                else:
                    print(
                        f"[AnkiConnect] Bad status {response.status_code} (attempt {attempt + 1}/{max_retries})"
                    )
            except Exception as e:
                print(f"[AnkiConnect] Not ready (attempt {attempt + 1}/{max_retries}): {e}")
                logger.warning(f"AnkiConnect not ready (attempt {attempt + 1}/{max_retries}): {e}")

            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)

        print(f"[AnkiConnect] Failed to connect after {max_retries} attempts")
        logger.error(f"AnkiConnect unavailable after {max_retries} attempts")
        return False

    async def get_decks(self) -> list[str]:
        """Get flat list of deck names."""
        return await self._invoke("deckNames")

    async def get_due_cards(self, deck_name: str) -> list[Card]:
        """Get due cards for deck (review cards only).

        Note: For session start, use get_reviewable_cards() instead
        to include new and learning cards.
        """
        # Query for due cards
        card_ids = await self._invoke("findCards", query=f'"deck:{deck_name}" is:due')
        if not card_ids:
            return []

        # Get card info
        cards_info = await self._invoke("cardsInfo", cards=card_ids)
        return [self._parse_card(info) for info in cards_info]

    async def get_reviewable_cards(self, deck_name: str) -> list[Card]:
        """Get all reviewable cards (new, learning, due) for deck.

        Fetches cards in Anki's study order:
        1. Learning cards (in step phase, need immediate attention)
        2. Review cards (due today)
        3. New cards (to introduce)

        Uses parallel queries for performance.
        """
        # Query all three categories in parallel
        learn_task = self._invoke("findCards", query=f'"deck:{deck_name}" is:learn')
        due_task = self._invoke("findCards", query=f'"deck:{deck_name}" is:due')
        new_task = self._invoke("findCards", query=f'"deck:{deck_name}" is:new')

        learn_ids, due_ids, new_ids = await asyncio.gather(learn_task, due_task, new_task)

        # Combine in priority order: learn → due → new
        # Use dict.fromkeys() to deduplicate while preserving order
        all_ids = list(dict.fromkeys((learn_ids or []) + (due_ids or []) + (new_ids or [])))

        if not all_ids:
            return []

        # Fetch card details
        cards_info = await self._invoke("cardsInfo", cards=all_ids)
        return [self._parse_card(info) for info in cards_info]

    async def get_next_card(self, deck_name: str) -> Card | None:
        """Get next reviewable card from deck."""
        cards = await self.get_reviewable_cards(deck_name)
        return cards[0] if cards else None

    async def submit_review(self, card_id: int, rating: Rating) -> None:
        """Submit review rating."""
        await self._invoke("answerCards", answers=[{"cardId": card_id, "ease": int(rating)}])

    async def get_card_image(self, filename: str) -> bytes | None:
        """Get image file decoded from Base64."""
        try:
            result = await self._invoke("retrieveMediaFile", filename=filename)
            if result:
                return base64.b64decode(result)
        except AnkiConnectError:
            pass
        return None

    async def sync(self) -> bool:
        """Trigger AnkiWeb sync."""
        try:
            await self._invoke("sync")
            return True
        except AnkiConnectError:
            return False

    async def get_decks_with_due_count(self) -> list[tuple[str, int]]:
        """Get decks sorted by due count (descending).

        Uses parallel execution with semaphore to limit concurrent requests.
        """
        decks = await self.get_decks()
        if not decks:
            return []

        # Limit concurrent requests to avoid overwhelming AnkiConnect
        semaphore = asyncio.Semaphore(10)

        async def get_count(deck: str) -> tuple[str, int]:
            async with semaphore:
                cards = await self._invoke("findCards", query=f'"deck:{deck}" is:due')
                return (deck, len(cards) if cards else 0)

        counts = await asyncio.gather(*[get_count(deck) for deck in decks])
        return sorted(counts, key=lambda x: x[1], reverse=True)

    async def get_decks_with_card_counts(self) -> list[DeckStats]:
        """Get decks with new, learn, due counts.

        Uses parallel execution with semaphore to limit concurrent requests.
        Queries three card categories per deck: is:new, is:learn, is:due.
        """
        decks = await self.get_decks()
        if not decks:
            return []

        # Limit concurrent requests to avoid overwhelming AnkiConnect
        semaphore = asyncio.Semaphore(10)

        async def get_counts(deck: str) -> DeckStats:
            async with semaphore:
                # Run three queries in parallel for each deck
                new_task = self._invoke("findCards", query=f'"deck:{deck}" is:new')
                learn_task = self._invoke("findCards", query=f'"deck:{deck}" is:learn')
                due_task = self._invoke("findCards", query=f'"deck:{deck}" is:due')

                new_cards, learn_cards, due_cards = await asyncio.gather(
                    new_task, learn_task, due_task
                )

                return DeckStats(
                    name=deck,
                    new_count=len(new_cards) if new_cards else 0,
                    learn_count=len(learn_cards) if learn_cards else 0,
                    due_count=len(due_cards) if due_cards else 0,
                )

        counts = await asyncio.gather(*[get_counts(deck) for deck in decks])
        return sorted(counts, key=lambda x: x.total_count, reverse=True)

    def _parse_card(self, info: dict) -> Card:
        """Parse AnkiConnect card info to Card entity."""
        fields = info.get("fields", {})

        # Get front/back fields - try common field names
        front_field = fields.get("Front", fields.get("front", {}))
        back_field = fields.get("Back", fields.get("back", {}))

        front = self._strip_html(front_field.get("value", ""))
        # Preserve formatting (bold, bullets) for the answer display
        back = self._strip_html(back_field.get("value", ""), preserve_formatting=True)

        # Extract image filename if present (search all fields)
        image_filename = None
        for field_data in fields.values():
            field_value = field_data.get("value", "")
            image_match = re.search(r'<img[^>]+src="([^"]+)"', field_value)
            if image_match:
                filename = image_match.group(1)
                # Validate: no path traversal (defense in depth)
                if os.path.basename(filename) == filename and ".." not in filename:
                    image_filename = filename
                break

        # Generate generic prompt for image-only cards
        if image_filename and not front.strip():
            front = "What do you see in this image?"

        return Card(
            id=info["cardId"],
            deck_name=info["deckName"],
            front=front,
            back=back,
            image_filename=image_filename,
            queue=info.get("queue", 0),
            due=info.get("due", 0),
        )

    def _strip_html(self, text: str, preserve_formatting: bool = False) -> str:
        """Remove HTML tags from text.

        Args:
            text: HTML text to clean
            preserve_formatting: If True, keep <b>, <strong>, <em>, <i> tags
        """
        clean = text

        if preserve_formatting:
            # Convert <br> tags to newlines
            clean = re.sub(r"<br\s*/?>", "\n", clean, flags=re.IGNORECASE)
            # Convert <li> to bullet points
            clean = re.sub(r"<li[^>]*>", "• ", clean, flags=re.IGNORECASE)
            # Remove </li> tags
            clean = re.sub(r"</li>", "\n", clean, flags=re.IGNORECASE)
            # Remove <ul>, </ul>, <ol>, </ol> tags
            clean = re.sub(r"</?[uo]l[^>]*>", "", clean, flags=re.IGNORECASE)
            # Remove <p> tags but keep newlines
            clean = re.sub(r"<p[^>]*>", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"</p>", "\n", clean, flags=re.IGNORECASE)
            # Remove <div> tags but keep newlines
            clean = re.sub(r"<div[^>]*>", "", clean, flags=re.IGNORECASE)
            clean = re.sub(r"</div>", "\n", clean, flags=re.IGNORECASE)
            # Remove all other HTML tags except b, strong, em, i
            clean = re.sub(r"<(?!/?(?:b|strong|em|i)(?:\s|>))[^>]+>", "", clean)
        else:
            # Remove all HTML tags
            clean = re.sub(r"<[^>]+>", "", clean)

        # Decode all HTML entities (handles &nbsp;, &lt;, &#39;, &#x27;, etc.)
        clean = html.unescape(clean)
        # Normalize special characters for TTS
        clean = clean.replace("\xa0", " ")  # non-breaking space → space
        clean = clean.replace("\u2019", "'")  # right single quote → apostrophe
        clean = clean.replace("\u2018", "'")  # left single quote → apostrophe
        clean = clean.replace("\u201c", '"')  # left double quote → quote
        clean = clean.replace("\u201d", '"')  # right double quote → quote
        clean = clean.replace("\u2014", "-")  # em dash → hyphen
        # Collapse multiple newlines
        clean = re.sub(r"\n{3,}", "\n\n", clean)
        return clean.strip()

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
