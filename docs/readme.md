# Gyver Ads — README

The core of the Ads section ("sezione Annunci"): a FastAPI backend with a SQLite database. It turns an internal job offer into channel-specific ad variants for Indeed, Instagram, TikTok and WhatsApp. The copy comes from a local LLM served by Ollama; the creative's image comes from FLUX.1-schnell on Hugging Face, with Pollinations.ai as a fallback.

Other documents in this folder:
[architecture.md](architecture.md) · [prompts.md](prompts.md) · [ai-workflows.md](ai-workflows.md) · [tradeoffs.md](tradeoffs.md)

## Requirements

| Tool | Needed for | Notes |
|---|---|---|
| Python 3.13 | everything | pinned in `backend/.python-version` |
| [uv](https://docs.astral.sh/uv/) | installing and running | `brew install uv` or `pip install uv` |
| [Ollama](https://ollama.com) | **only** for generating new variants | `brew install ollama`, then `ollama serve`. Reading, listing and editing the pre-filled ads work without it. |
| Internet access | **only** for generating images | Hugging Face (with `HF_TOKEN`) or Pollinations.ai (no key) |

## 1. Install

```sh
cd backend
uv sync
```

## 2. Environment variables

No API key is required: the LLM runs locally through Ollama, and images fall back to Pollinations.ai, which needs no key. `HF_TOKEN` is optional and only improves image quality. Every variable is optional.

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_HOST` | `http://localhost:11434` | Address of the Ollama server (local or `https://ollama.com`) |
| `OLLAMA_MODEL` | `gemma4:e4b-mlx` | Model used for generation |
| `OLLAMA_API_KEY` | none | Only for Ollama Cloud; read automatically by the `ollama` client |
| `OLLAMA_TIMEOUT` | `180` | Seconds to wait for each model reply before returning 503 |
| `HF_TOKEN` | none | Hugging Face token for image generation with FLUX.1-schnell. Create a free one at huggingface.co → Settings → Access Tokens (type *Read*, or fine-grained with "Make calls to Inference Providers"). Without it, images come from Pollinations.ai. |
| `HF_IMAGE_MODEL` | `black-forest-labs/FLUX.1-schnell` | Hugging Face text-to-image model |
| `HF_PROVIDER` | `auto` | Hugging Face inference provider (`auto` lets Hugging Face pick, e.g. `fal-ai`, `hf-inference`) |
| `HF_COLD_START_RETRIES` / `HF_COLD_START_WAIT` | `2` / `10` | Retries and seconds between them while the model is loading (HTTP 503) |
| `IMAGE_TIMEOUT` | `90` | Seconds per image request, for each provider |
| `POLLINATIONS_URL` | `https://image.pollinations.ai/prompt/` | Fallback image service, no key needed |
| `IMAGES_DIR` | `backend/generated_images` | Where generated images are saved (git-ignored) |
| `DATABASE_URL` | `backend/gyver.db` (SQLite) | SQLAlchemy URL of the database |

### Choosing the model

Generation goes through Ollama, so switching models only means changing environment variables. No code changes are needed.

**Default (Apple Silicon):**
```sh
ollama pull gemma4:e4b-mlx
```

**Any other machine, with a local model:** pull a model and point `OLLAMA_MODEL` at it.
```sh
ollama pull llama3.2:3b
OLLAMA_MODEL=llama3.2:3b uv run fastapi dev main.py
```

**No local GPU: a hosted model on Ollama Cloud.** Create a key at ollama.com (Settings → Keys).
```sh
OLLAMA_HOST=https://ollama.com OLLAMA_API_KEY=<your key> OLLAMA_MODEL=<cloud model name> uv run fastapi dev main.py
```

Notes:
- `gemma4:e4b-mlx` is an MLX build and runs **only on Apple Silicon**.
- Prompts and checks were tuned on `gemma4:e4b-mlx`. `llama3.2:3b` was also tested: it produces valid ads but weaker copy (more retries, formal "Lei" instead of "tu").
- Ollama Cloud uses the same client and API but **was not tested**.
- Whatever the model, every reply goes through the same checks. A weaker model means more retries, and possibly 502, but never unchecked content.

## 3. Database

`backend/gyver.db` is **included and pre-filled**: the sample job offer `jo_001` plus 7 ad variants covering every channel, every format and several image sizes and locations. One variant has a manual edit (status `ready`) and one is `published`.

Tables are created automatically when the app starts. To rebuild the database from scratch:

```sh
cd backend
rm gyver.db
uv run python seed.py
```

`seed.py` loads `data/sample_job_offer.json` (the job offer from the assignment, exactly as given) and `data/sample_variants.json` (variants generated earlier by the model). It is idempotent: it skips job offers that already exist, and job offers that already have ads.

## 4. Run the backend

```sh
cd backend
uv run fastapi dev main.py     # or: uv run python main.py
```

- **Interface (optional module): http://127.0.0.1:8000/**
- API documentation (Swagger UI): http://127.0.0.1:8000/docs

The frontend is a single file, `backend/frontend/index.html`, plain HTML and JavaScript, served by the backend itself. It needs no install, build or separate server.

## Optional module: the interface

Open http://127.0.0.1:8000/ in a browser. On one page you can:

1. **See the list of ads** for the selected job offer, with channel, format and image size, status, location, angle and headline. Filter by channel, status, region and province.
2. **Create a new ad from the job offer:** pick a channel (the format and image size lists show only what that channel allows), optionally a location (town, province and/or region; empty means the job offer's site) and a creative angle, then click *Genera con LLM*. It takes 20–60 s, then the new variant opens in the editor.
3. **Edit the content:** click a row to see the generated content (read-only) next to the editable version. *Salva modifiche* stores your edits and the status; *Ripristina contenuto generato* discards the edits. Indeed variants also show the job-board fields (address, skills, experience).
4. **Generate the image:** on an image variant, *Genera immagine dal visual brief* creates the creative's photo and shows it with the provider that made it.

Every action uses the API below, so the page reflects the backend exactly.

## 5. Full walkthrough: from job offer to generated ad

The quickest path is the interface at http://127.0.0.1:8000/: select `jo_001`, generate an ad, then edit it. The same flow with the API is below. Each step can also be done from `/docs`.

**1. Check the job offer.** It is already seeded; you can also `POST /job-offers` with any JSON shaped like the assignment's sample.
```sh
curl http://127.0.0.1:8000/job-offers/jo_001
```

**2. List the existing ads**, filtering by job offer, channel, status and/or location.
```sh
curl "http://127.0.0.1:8000/variants?job_offer_id=jo_001&channel=instagram"
curl "http://127.0.0.1:8000/variants?region=Lombardia&province_code=BS"
```

**3. Generate a new variant with the LLM.** This takes about 20–60 s on a laptop.
```sh
curl -X POST http://127.0.0.1:8000/job-offers/jo_001/variants \
  -H 'Content-Type: application/json' \
  -d '{"channel": "tiktok", "format": "image_only", "image_size": "vertical",
       "target_location": {"locality": "Brescia", "province": "Brescia", "province_code": "BS", "region": "Lombardia"},
       "angle": "stabilità del contratto"}'
```
The response contains:
- `generated_content`: headline, highlights, body, bullet points, call to action, visual brief
- `location`: the structured location the ad communicates, with a display `label` (e.g. "Brescia (BS)")
- `image_size`: the creative's size (`null` for text-only); omit it in the request to get the channel's default
- `publishing_fields`: the extra fields a job board needs (Indeed only)

**4. Override the content by hand.** Only the fields you send change. `generated_content` is never modified.
```sh
curl -X PATCH http://127.0.0.1:8000/variants/<id> \
  -H 'Content-Type: application/json' \
  -d '{"edited_content": {"headline": "Tecnico fotovoltaico: assunzione diretta"}, "status": "ready"}'
```
`effective_content` in the response is what should be published: the edited version if there is one, otherwise the generated one. Send `{"edited_content": null}` to discard the manual edits.

**5. Generate the creative's image** (image formats only). The image is made from the variant's `visual_brief` at its `image_size`.
```sh
curl -X POST http://127.0.0.1:8000/variants/<id>/image
```
The response has `image_url` (open it in a browser) and `image_provider` (`huggingface` or `pollinations`). In the interface: open an image variant and click *Genera immagine dal visual brief*.

## Tests

The suite is in `backend/tests/` and uses pytest. Interface tests drive **Firefox through Playwright**.

```sh
cd backend
uv sync                                  # includes the dev dependencies (pytest, pytest-playwright)
uv run playwright install firefox        # one time: Playwright's own Firefox build
uv run pytest                            # 82 tests, about 35 s, no LLM and no network needed
uv run pytest -m llm                     # 4 extra tests that call the real model through Ollama (about 2 min)
```

| File | What it covers |
|---|---|
| `test_frontend.py` | The interface in Firefox: listing, channel/status/location filters, channel → format and image size options, Indeed job-board fields, editing and restoring, image section, error messages |
| `test_api.py` | Every endpoint: job offer import from the assignment JSON, filters (including location), image size and location validation, merging of partial edits, error codes (404, 409, 422, 503) |
| `test_llm_checks.py` | The output checks and fixes, using bad outputs recorded during prompt work (see prompts.md), and image size resolution |
| `test_images.py` | The image service: Hugging Face cold-start retries, 429/402/401/timeout handling, Pollinations responses, fallback order, and the full API path against a local fake Pollinations server |

Each test gets its own server on a free port and a fresh database built by `seed.py`, so tests never touch `gyver.db` and can run in any order. The tests that need an LLM are marked `llm` and skipped by default. When the model is unreachable, a test points the server at a closed port to check that a 503 is returned. Test servers run with no `HF_TOKEN` and Pollinations pointed at a closed port, so no test ever calls a real image service.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/job-offers` | Store a raw job offer (assignment JSON format). 201, or 409 if the ID exists. |
| `GET` | `/job-offers` | List job offers |
| `GET` | `/job-offers/{job_offer_id}` | Read a job offer (404 if missing) |
| `GET` | `/channels` | Allowed formats and image sizes for each channel, and whether it needs a town |
| `GET` | `/` | The interface (HTML page) |
| `POST` | `/job-offers/{job_offer_id}/variants` | Generate and save a new variant with the LLM (201) |
| `GET` | `/variants?job_offer_id=&channel=&status=&region=&province_code=&locality=` | List variants, filters optional and combinable |
| `GET` | `/variants/{variant_id}` | Read one variant |
| `PATCH` | `/variants/{variant_id}` | Partially edit the content and/or change the status |
| `POST` | `/variants/{variant_id}/image` | Generate the creative's image from its visual brief (Hugging Face, Pollinations fallback) |
| `GET` | `/variants/{variant_id}/image` | The generated PNG (404 if none yet) |
| `GET` | `/health` | Liveness check |

Error codes:
- **404**: unknown job offer or variant
- **409**: duplicate job offer
- **422**: invalid input; a format or image size the channel doesn't support (e.g. `indeed` + `image_only`, `tiktok` + `square`); an Indeed ad without a town
- **502**: the model's output still failed validation after 3 attempts
- **503**: Ollama is unreachable, the model isn't installed, or a reply timed out; or both image providers failed
