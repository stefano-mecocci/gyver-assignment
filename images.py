import logging
import os
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import httpx
from huggingface_hub import InferenceClient
from huggingface_hub.errors import HfHubHTTPError
from huggingface_hub.errors import HTTPError as HfHTTPError
from PIL import Image, UnidentifiedImageError

logger = logging.getLogger(__name__)

HF_TOKEN = os.getenv("HF_TOKEN")
HF_IMAGE_MODEL = os.getenv("HF_IMAGE_MODEL", "black-forest-labs/FLUX.1-schnell")
HF_PROVIDER = os.getenv("HF_PROVIDER", "auto")
HF_COLD_START_RETRIES = int(os.getenv("HF_COLD_START_RETRIES", "2"))
HF_COLD_START_WAIT_SECONDS = float(os.getenv("HF_COLD_START_WAIT", "10"))
POLLINATIONS_URL = os.getenv("POLLINATIONS_URL", "https://image.pollinations.ai/prompt/")
IMAGE_TIMEOUT_SECONDS = float(os.getenv("IMAGE_TIMEOUT", "90"))
IMAGES_DIR = Path(os.getenv("IMAGES_DIR", Path(__file__).resolve().parent / "generated_images"))

COLD_START_STATUS = 503
HF_STATUS_REASONS = {
    401: "invalid or missing HF_TOKEN",
    402: "monthly inference credits exhausted",
    403: "the token has no access to this model",
    429: "rate limit or quota reached",
    503: "model still loading (cold start)",
}


class ImageGenerationError(Exception):
    pass


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class GeneratedImage:
    data: bytes
    provider: str
    width: int
    height: int

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.data)
        return path


class ImageProvider(Protocol):
    name: str

    def generate(self, prompt: str, width: int, height: int) -> Image.Image: ...


class HuggingFaceProvider:
    name = "huggingface"

    def __init__(
        self,
        token: str | None,
        model: str = HF_IMAGE_MODEL,
        provider: str = HF_PROVIDER,
        timeout: float = IMAGE_TIMEOUT_SECONDS,
        cold_start_retries: int = HF_COLD_START_RETRIES,
        cold_start_wait: float = HF_COLD_START_WAIT_SECONDS,
        client: InferenceClient | None = None,
    ) -> None:
        self.token = token
        self.model = model
        self.cold_start_retries = cold_start_retries
        self.cold_start_wait = cold_start_wait
        self.client = client or InferenceClient(model=model, provider=provider, token=token, timeout=timeout)

    def generate(self, prompt: str, width: int, height: int) -> Image.Image:
        if not self.token:
            raise ProviderError("HF_TOKEN is not set")
        for attempt in range(self.cold_start_retries + 1):
            try:
                return self.client.text_to_image(
                    prompt, model=self.model, width=width, height=height, num_inference_steps=4
                )
            except HfHubHTTPError as exc:
                status = exc.response.status_code
                if status == COLD_START_STATUS and attempt < self.cold_start_retries:
                    logger.info("Hugging Face cold start, retrying in %gs", self.cold_start_wait)
                    time.sleep(self.cold_start_wait)
                    continue
                reason = HF_STATUS_REASONS.get(status, "request failed")
                raise ProviderError(f"Hugging Face returned {status}: {reason}") from exc
            except HfHTTPError as exc:
                raise ProviderError(f"Hugging Face unreachable or timed out: {exc}") from exc
            except UnidentifiedImageError as exc:
                raise ProviderError("Hugging Face returned data that is not an image") from exc
        raise ProviderError("Hugging Face model did not finish loading")


class PollinationsProvider:
    name = "pollinations"

    def __init__(
        self,
        base_url: str = POLLINATIONS_URL,
        timeout: float = IMAGE_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def generate(self, prompt: str, width: int, height: int) -> Image.Image:
        url = self.base_url + quote(prompt, safe="")
        params = {"width": width, "height": height, "model": "flux", "nologo": "true"}
        try:
            response = self.client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError("Pollinations timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Pollinations unreachable: {exc}") from exc
        if response.status_code != 200:
            raise ProviderError(f"Pollinations returned {response.status_code}")
        if not response.headers.get("content-type", "").startswith("image/"):
            raise ProviderError("Pollinations returned a response that is not an image")
        try:
            image = Image.open(BytesIO(response.content))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise ProviderError("Pollinations returned an unreadable image") from exc
        return image


class ImageGenerator:
    def __init__(self, providers: list[ImageProvider]) -> None:
        self.providers = providers

    def generate(self, prompt: str, width: int, height: int) -> GeneratedImage:
        failures = []
        for provider in self.providers:
            try:
                image = provider.generate(prompt, width, height)
            except ProviderError as exc:
                logger.warning("Image provider %s failed: %s", provider.name, exc)
                failures.append(f"{provider.name}: {exc}")
                continue
            return GeneratedImage(data=to_png(image), provider=provider.name, width=image.width, height=image.height)
        raise ImageGenerationError("All image providers failed (" + "; ".join(failures) + ")")


def to_png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return buffer.getvalue()


def creative_prompt(visual_brief: str) -> str:
    return (
        "Professional recruiting advertisement photo, photorealistic, natural light, high quality. "
        f"{visual_brief.strip()} "
        "No text, no letters, no logos, no watermark. Leave clean empty space for a text overlay."
    )


def build_default_generator() -> ImageGenerator:
    return ImageGenerator([HuggingFaceProvider(token=HF_TOKEN), PollinationsProvider()])


generator = build_default_generator()
