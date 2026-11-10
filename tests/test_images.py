import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest
from huggingface_hub.errors import HfHubHTTPError, InferenceTimeoutError
from PIL import Image

from channels import generation_dimensions
from images import (
    HuggingFaceProvider,
    ImageGenerationError,
    ImageGenerator,
    PollinationsProvider,
    ProviderError,
    creative_prompt,
)
from models import ImageSize


def png_bytes(width: int = 8, height: int = 8) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "orange").save(buffer, format="PNG")
    return buffer.getvalue()


def hf_error(status: int) -> HfHubHTTPError:
    response = SimpleNamespace(status_code=status, headers={}, request=None)
    return HfHubHTTPError(f"{status} error", response=response)


class FakeInferenceClient:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def text_to_image(self, prompt: str, **kwargs) -> Image.Image:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeProvider:
    def __init__(self, name: str, result: Image.Image | None = None) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    def generate(self, prompt: str, width: int, height: int) -> Image.Image:
        self.calls += 1
        if self.result is None:
            raise ProviderError(f"{self.name} is down")
        return self.result


def hugging_face(outcomes: list, token: str | None = "hf_test") -> tuple[HuggingFaceProvider, FakeInferenceClient]:
    client = FakeInferenceClient(outcomes)
    provider = HuggingFaceProvider(token=token, cold_start_retries=2, cold_start_wait=0, client=client)
    return provider, client


def test_hugging_face_returns_the_image() -> None:
    provider, client = hugging_face([Image.new("RGB", (4, 4))])
    assert provider.generate("a technician", 4, 4).size == (4, 4)
    assert client.calls == 1


def test_hugging_face_retries_cold_starts() -> None:
    provider, client = hugging_face([hf_error(503), hf_error(503), Image.new("RGB", (4, 4))])
    assert provider.generate("a technician", 4, 4).size == (4, 4)
    assert client.calls == 3


def test_hugging_face_gives_up_after_cold_start_retries() -> None:
    provider, client = hugging_face([hf_error(503)] * 3)
    with pytest.raises(ProviderError, match="cold start"):
        provider.generate("a technician", 4, 4)
    assert client.calls == 3


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (hf_error(429), "rate limit"),
        (hf_error(402), "credits exhausted"),
        (hf_error(401), "HF_TOKEN"),
        (InferenceTimeoutError("too slow"), "timed out"),
    ],
)
def test_hugging_face_failures_do_not_retry(error: Exception, message: str) -> None:
    provider, client = hugging_face([error])
    with pytest.raises(ProviderError, match=message):
        provider.generate("a technician", 4, 4)
    assert client.calls == 1


def test_hugging_face_without_token_is_skipped() -> None:
    provider, client = hugging_face([], token=None)
    with pytest.raises(ProviderError, match="HF_TOKEN"):
        provider.generate("a technician", 4, 4)
    assert client.calls == 0


def pollinations(handler) -> PollinationsProvider:
    return PollinationsProvider(base_url="https://pollinations.test/prompt/", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_pollinations_encodes_the_prompt_and_size() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "image/png"}, content=png_bytes(6, 4))

    image = pollinations(handler).generate("tecnico & cantiere/FV", 576, 1024)
    assert image.size == (6, 4)
    assert requests[0].url.raw_path.decode().startswith("/prompt/tecnico%20%26%20cantiere%2FFV?")
    assert requests[0].url.params["width"] == "576"
    assert requests[0].url.params["height"] == "1024"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(429),
        httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>"),
        httpx.Response(200, headers={"content-type": "image/png"}, content=b"not a png"),
    ],
)
def test_pollinations_bad_responses_raise(response: httpx.Response) -> None:
    with pytest.raises(ProviderError):
        pollinations(lambda request: response).generate("prompt", 8, 8)


def test_pollinations_timeout_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderError, match="timed out"):
        pollinations(handler).generate("prompt", 8, 8)


def test_generator_falls_back_to_the_next_provider() -> None:
    primary = FakeProvider("huggingface")
    fallback = FakeProvider("pollinations", Image.new("RGBA", (5, 3)))
    image = ImageGenerator([primary, fallback]).generate("prompt", 5, 3)
    assert (image.provider, image.width, image.height) == ("pollinations", 5, 3)
    assert image.data.startswith(b"\x89PNG")
    assert primary.calls == fallback.calls == 1


def test_generator_stops_at_the_first_success() -> None:
    primary = FakeProvider("huggingface", Image.new("RGB", (2, 2)))
    fallback = FakeProvider("pollinations", Image.new("RGB", (2, 2)))
    assert ImageGenerator([primary, fallback]).generate("prompt", 2, 2).provider == "huggingface"
    assert fallback.calls == 0


def test_generator_reports_every_failure() -> None:
    with pytest.raises(ImageGenerationError, match="huggingface: huggingface is down; pollinations: pollinations is down"):
        ImageGenerator([FakeProvider("huggingface"), FakeProvider("pollinations")]).generate("prompt", 2, 2)


def test_generation_dimensions_keep_aspect_ratio_within_1024() -> None:
    assert generation_dimensions(ImageSize.SQUARE) == (1024, 1024)
    assert generation_dimensions(ImageSize.VERTICAL) == (576, 1024)
    assert generation_dimensions(ImageSize.A4) == (720, 1024)


def test_creative_prompt_wraps_the_visual_brief() -> None:
    prompt = creative_prompt("  Tecnico con casco su un impianto fotovoltaico. ")
    assert "Tecnico con casco su un impianto fotovoltaico." in prompt
    assert "No text" in prompt


class FakePollinationsHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = png_bytes(16, 16)
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def fake_pollinations() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakePollinationsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/prompt/"
    server.shutdown()


@pytest.fixture
def server_env(fake_pollinations: str) -> dict[str, str]:
    return {"POLLINATIONS_URL": fake_pollinations}


def test_api_falls_back_to_pollinations_and_serves_the_image(api: httpx.Client) -> None:
    response = api.post("/variants/3/image")
    assert response.status_code == 200
    variant = response.json()
    assert variant["image_provider"] == "pollinations"
    assert variant["image_url"].startswith("/variants/3/image?v=")
    image = api.get(variant["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert Image.open(BytesIO(image.content)).size == (16, 16)
    assert api.get("/variants/3").json()["image_url"] == variant["image_url"]
