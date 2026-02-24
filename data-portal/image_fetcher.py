"""
Image Fetcher — On-demand image fetching and optimization.

Replaces the scattered image-download logic from 7 ingestion scripts
with a single reusable module. Fetches images based on manifest doc
metadata (image_source_type + image_source_url).

Usage:
    fetcher = ImageFetcher()
    image_bytes = await fetcher.fetch_image(manifest_doc)
    optimized = fetcher.optimize_image(image_bytes)
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

import aiohttp
from PIL import Image

logger = logging.getLogger("data-portal.image-fetcher")

# Max retries for transient failures
MAX_RETRIES = 3
RETRY_BACKOFF = [1.0, 3.0, 10.0]
REQUEST_TIMEOUT = 30


class ImageFetcher:
    """Fetch and optimize museum images on demand."""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self._external_session = session
        self._own_session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._external_session:
            return self._external_session
        if self._own_session is None or self._own_session.closed:
            self._own_session = aiohttp.ClientSession(
                headers={"User-Agent": "AlexandriaDataPortal/2.3 (research)"},
            )
        return self._own_session

    async def close(self):
        if self._own_session and not self._own_session.closed:
            await self._own_session.close()

    async def fetch_image(self, manifest_doc: dict) -> bytes:
        """Fetch an image from a museum source based on manifest metadata.

        Args:
            manifest_doc: Dict with at least `image_source_url` and `image_source_type`.

        Returns:
            Raw image bytes.

        Raises:
            ImageFetchError: If all retry attempts fail.
        """
        url = manifest_doc.get("image_source_url", "")
        source_type = manifest_doc.get("image_source_type", "direct_url")

        if not url:
            raise ImageFetchError(f"No image_source_url in manifest doc: {manifest_doc.get('museum', '?')}_{manifest_doc.get('object_id', '?')}")

        # Resolve indirect URL types
        if source_type == "met_api":
            url = await self._resolve_met_api(url)
        elif source_type == "iiif":
            url = self._normalize_iiif_url(url)
        elif source_type == "ids_service":
            url = self._normalize_ids_url(url)

        session = await self._get_session()

        for attempt in range(MAX_RETRIES):
            try:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if len(data) < 1000:
                            raise ImageFetchError(f"Suspiciously small image ({len(data)} bytes): {url}")
                        return data
                    elif resp.status in (404, 410):
                        raise ImageFetchError(f"Image not found (HTTP {resp.status}): {url}")
                    else:
                        logger.warning("Image fetch attempt %d/%d: HTTP %d for %s", attempt + 1, MAX_RETRIES, resp.status, url)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("Image fetch attempt %d/%d failed: %s for %s", attempt + 1, MAX_RETRIES, e, url)

            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_BACKOFF[attempt])

        raise ImageFetchError(f"All {MAX_RETRIES} attempts failed for: {url}")

    @staticmethod
    def optimize_image(
        image_bytes: bytes,
        max_dim: int = 2048,
        quality: int = 90,
        output_format: str = "JPEG",
    ) -> bytes:
        """Resize and compress an image.

        Args:
            image_bytes: Raw image data.
            max_dim: Maximum dimension (width or height).
            quality: JPEG quality (1-100).
            output_format: Output format ("JPEG" or "PNG").

        Returns:
            Optimized image bytes.
        """
        img = Image.open(io.BytesIO(image_bytes))

        # Convert to RGB if needed (e.g., RGBA PNGs, palette images)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")

        # Resize if larger than max_dim
        w, h = img.size
        if max(w, h) > max_dim:
            ratio = max_dim / max(w, h)
            new_w = int(w * ratio)
            new_h = int(h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)

        buf = io.BytesIO()
        if output_format == "PNG":
            img.save(buf, format="PNG", optimize=True)
        else:
            img.save(buf, format="JPEG", quality=quality, optimize=True)

        return buf.getvalue()

    async def _resolve_met_api(self, api_url: str) -> str:
        """Resolve a Met Museum API URL to the actual primaryImage URL."""
        session = await self._get_session()
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                img = data.get("primaryImage", "")
                if img:
                    return img
        raise ImageFetchError(f"Could not resolve Met API image URL: {api_url}")

    @staticmethod
    def _normalize_iiif_url(url: str) -> str:
        """Ensure IIIF URL requests a reasonable size (2048px max)."""
        # If URL already has /full/ parameters, replace with our preferred size
        if "/full/full/" in url:
            return url.replace("/full/full/", "/full/!2048,2048/")
        if "/full/max/" in url:
            return url.replace("/full/max/", "/full/!2048,2048/")
        return url

    @staticmethod
    def _normalize_ids_url(url: str) -> str:
        """Normalize Smithsonian IDS URLs for high-res download."""
        if "?max=" not in url and "max_w=" not in url:
            separator = "&" if "?" in url else "?"
            return f"{url}{separator}max=2048"
        return url


class ImageFetchError(Exception):
    """Raised when image fetching fails after all retries."""
    pass
