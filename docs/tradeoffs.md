# Trade-offs

## Language and structure

- **Python + FastAPI + Pydantic v2.** Pydantic models are the API contract, the validator for the LLM's output, *and* the JSON schema handed to the model (`format=VariantContent.model_json_schema()`). One definition drives all three, which is the core of a robust LLM pipeline. FastAPI also provides an interactive UI (`/docs`) for free.
- **SQLAlchemy 2.0 + SQLite.** A real relational model with foreign keys, indexes and CHECK constraints, but stored in one file that can ship pre-filled with the repo, as the assignment suggests. The models are not SQLite-specific, but `database.py` currently applies SQLite-only connection settings, so moving to Postgres needs a small change there in addition to `DATABASE_URL`.
- **Ollama with a local model.** No API keys to manage, nothing to pay per call, and the internal job offer data never leaves the machine. The cost is lower copy quality and slower generation than a frontier hosted model (see the limits below).
- **FLUX.1-schnell on Hugging Face, Pollinations.ai as fallback, for images.** Schnell is fast (4 steps) and available on Hugging Face's free tier through the official `huggingface_hub` client. Pollinations needs no key, so the image flow works on any machine, and it also falls back for rate limits and cold starts. Unlike the copy, image prompts leave the machine: they contain only the `visual_brief`, never the internal job offer.
- **Flat module layout** (one file per responsibility, see architecture.md). For a codebase this size, packages and service layers would only add indirection.

## What I sacrificed to stay in scope

| Left out | Why it's acceptable for now |
|---|---|
| Composing the final creative | The background photo is generated (Hugging Face FLUX.1-schnell, Pollinations fallback), but the headline and highlights aren't laid over it. Diffusion models write text badly, so that belongs in a template. |
| A real frontend stack | The optional interface is one plain HTML/JS file served by FastAPI: no framework, no build, no styling effort, as the assignment allows. It covers listing, creating and editing, and nothing else. |
| A full test suite | There is a small suite (interface tests in Firefox via Playwright, API tests, unit tests for the checks and the image service). It doesn't cover copy or image quality: the real-model tests only assert that the output is valid. |
| Migrations | `create_all` plus a re-seed is enough while the schema is still changing. |
| Asynchronous generation | The LLM and image calls happen inside the request (about 20–60 s for copy). Acceptable for a single internal user. |
| Authentication, pagination, deletion, status-transition rules | Not needed to demonstrate the core flow. |

## How I read the ambiguous parts of the assignment

- **"Annuncio" vs "variante".** An `AdVariant` is the unit that gets published: one channel, one format, one piece of content. `Ad` groups the variants of a job offer. I kept `Ad` because a campaign or publication batch is a likely next need. Today it is 1:1 with the job offer (get-or-create).
- **"The job offer is the only input".** `POST /job-offers` takes the JSON from the PDF unchanged, and the database model follows it (string ID, nested location, `ral_min`/`ral_max`, skills). The model's first version used my own field names. I changed it once I compared it with the real sample.
- **Which formats each channel allows.**
  - Indeed: `text_only` only, because a job board publishes a text job description.
  - Instagram and TikTok: `image_only` and `text_and_image`, because a sponsored post is always a creative.
  - WhatsApp: all three, because the PDF shows an A4 image and messages are text.

  Other pairs return 422 rather than producing something meaningless.
- **"Social (TikTok, Instagram)".** Two channels, not one: they are different platforms with different image sizes, and filtering or measuring "social" as one bucket would hide which one works. They share specs in code, so splitting cost nothing.
- **"Different locations per ad, and between ads".** Each variant stores a structured area (town, province, region, country), copied from the job offer unless the request gives another one. It can be as coarse as a region, for area-wide social campaigns, except on Indeed, which needs a town. The prompt only ever receives the area's label, never the street address.
- **The job-board fields** (address, skills, experience). The PDF says these are needed to publish on a job board, separately from the job description. They are built **in code from the job offer** and not by the LLM, because they must be exact.
- **"Copy and creatives can differ, to test which works best".** Two ways to get there:
  - unlimited variants per job offer and channel, with `temperature=0.7` so repeated generations differ;
  - an optional **`angle`** per variant (e.g. "stabilità del contratto" vs "impianti oltre 100 kW"), stored with it, so an A/B test is deliberate and traceable.
- **Image sizes.** The PDF says 1080×1080 "is not the only format". Each image variant stores an `image_size`, limited per channel (Instagram square/4:5/9:16, TikTok 9:16, WhatsApp A4), and the prompt adapts the layout hint to it. The content limits stay the same across sizes.
- **Manual edits.** Stored next to the generated content, never instead of it, so the original stays available for comparison and the edits can be undone.

## Technical limits

- **Copy quality depends on a small local model** (4B-class). Occasional typos ("Possidi" instead of "Possiedi"), slightly generic phrasing, and some runs need 2–3 attempts. Checks and retries keep the structure reliable; they can't make the prose excellent.
- **Portability.** The default model `gemma4:e4b-mlx` runs only on Apple Silicon. Switching takes only environment variables: `OLLAMA_MODEL` for another local model, or `OLLAMA_HOST`/`OLLAMA_API_KEY` for Ollama Cloud. But the prompts are tuned for the default. With `llama3.2:3b` the output stays valid but the copy is weaker, and Ollama Cloud is untested.
- **Latency.** 20–60 s per generation including retries, and it blocks the request.
- **The style checks are Italian-specific regexes** (company first person, numbers written as words, emoji ranges). They can produce false positives or miss new kinds of bad output.
- **Length leeway.** The 15% tolerance means outputs can slightly exceed the stated limits.
- **Every channel uses one content schema.** That keeps the system simple, but a channel with a very different structure (e.g. carousels) would need the schema to grow.
- **SQLite** drops time zones and isn't meant for concurrent writers.
- **Images.** The Hugging Face path is tested only with fakes, not with a real token. Pollinations' output is watermarked and lower quality. Each variant keeps one image, so creatives can't be A/B tested yet, and editing the `visual_brief` doesn't mark the existing image as outdated.

## With one more day, in priority order

1. **A better interface:** previews of each ad as it would appear on its channel (WhatsApp bubble, Indeed listing, Instagram/TikTok creative), warnings when a manual edit breaks the channel's limits, and generating several A/B variants in one click.
2. **More tests:** run generation with a stubbed LLM, so the retry and 502 paths are covered without Ollama, and run the suite in CI.
3. **Composing the creatives:** HTML/SVG templates that place the generated photo plus the copy, one for each `image_size` (social square, 4:5, 9:16 and the A4 WhatsApp flyer), filled from `headline`, `highlights` and `bullet_points`, exported as PNG.
4. **Generation in a background job:** return 202 with a pending variant and poll for the result. This removes the long request and lets several variants generate at once.
5. **Test and tune for a hosted model** (Ollama Cloud already works through environment variables), and record which model generated each variant, so copy quality can be compared across models.
6. **Evaluation set:** a few job offers × every channel/format, scored by the checks plus an LLM judge on tone and faithfulness to the data. It would turn prompt changes into measurable improvements.
7. **Status rules and Alembic migrations**, and giving `Ad` a real campaign meaning (name, period, channels).
