# Architecture

## How the project is split

```
backend/
├── main.py        FastAPI app: creates the tables at startup, mounts the router
├── routes.py      All HTTP endpoints (a project rule: every route lives here)
├── database.py    SQLite engine, session factory, foreign-key enforcement, get_db dependency
├── models.py      SQLAlchemy 2.0 models and enums: JobOffer, Ad, AdVariant
├── schemas.py     Pydantic v2 schemas: API input/output and the generated-content shape
├── channels.py    Channel catalog: image sizes per channel, which channels need a town
├── images.py      Image generation: Hugging Face FLUX.1-schnell with Pollinations.ai fallback
├── llm.py         Ollama service: prompts, channel/format specs, validation, retries
├── seed.py        Loads the sample job offer and pre-generated variants
├── data/          Seed fixtures (job offer from the assignment, generated variants)
├── frontend/      index.html: the optional single-page interface, served at /
├── tests/         pytest suite: Playwright (Firefox) interface tests, API tests, LLM check and image service unit tests
├── docs/          readme, architecture, prompts, ai-workflows, tradeoffs
└── gyver.db       Pre-filled SQLite database
```

It is a flat module layout, one file per responsibility. Each layer only depends on the ones below it: `routes → llm, images → channels, schemas → models → database` (`images.py` depends on none of the app modules). The project is small, so packages, repositories and service classes would add indirection without adding clarity.

## Data model

```
JobOffer 1 ───── * Ad 1 ───── * AdVariant
(internal data)    (grouping)    (what gets published)
```

### JobOffer: the dense internal input
- **Mirrors the sample JSON from the assignment**, so `POST /job-offers` accepts it unchanged.
- **The primary key is the external string ID** (`jo_001`), which keeps seeding and imports idempotent.
- **The `location` object is stored as separate columns** (street, postal code, locality, province, region, country) and rebuilt as a nested object on output. Separate columns keep each part queryable.
- **`required_skills` is a JSON list.** Salary and experience are min/max integers with database CHECK constraints that require min ≤ max.
- **The long text fields are kept raw** (`role_description`, `compensation_package`, …). They are the material the LLM rewrites; they are never shown as-is.

### Ad: groups the variants of one job offer
- Linked to `job_offer_id` and indexed on it. Variant generation does get-or-create on it, so today every job offer has exactly one `Ad`.
- It exists as the place for a future campaign or publication batch, e.g. "July hiring push" vs "September re-post". Adding that later means adding columns here, not restructuring the variants (see tradeoffs).

### AdVariant: one publishable ad
| Field | Meaning |
|---|---|
| `channel` | The platform the ad is published on: `indeed`, `instagram`, `tiktok` or `whatsapp` |
| `format` | `text_only`, `image_only` or `text_and_image` |
| `image_size` | `square` (1080×1080), `portrait` (1080×1350), `vertical` (1080×1920) or `a4` (1240×1754). `null` for text-only; a DB CHECK enforces that. |
| `status` | `draft` → `ready` → `published` (no transition rules enforced) |
| `locality`, `province`, `province_code`, `region`, `country_code` | The location this ad communicates (see below) |
| `angle` | Optional creative angle, for deliberate A/B tests of copy and creatives |
| `generated_content` | The LLM's output (JSON). Never modified after creation. |
| `edited_content` | Manual edits (JSON). `null` until someone edits. |
| `image_file`, `image_provider` | The generated image's file name in `IMAGES_DIR` and who made it (`huggingface` or `pollinations`). `null` until an image is generated. |

Index on `(ad_id, channel)` plus the index on `Ad.job_offer_id`. Together they cover the required "query by job offer and by channel". A second index on `(country_code, region, province_code, locality)` serves the location filters.

### Channels are platforms
Instagram and TikTok are separate channels, not one `social` value. They share the same content specs and tone today (the spec objects are reused), but each has its own image sizes, its own row in the filters, and its own place to diverge (e.g. TikTok-specific rules) without touching the other. The PDF groups them as "ads sui social": that is a family of channels, not a channel.

Allowed image sizes per channel live in `channels.py`:

| Channel | Image sizes (first = default) |
|---|---|
| indeed | — (text only) |
| instagram | `square`, `portrait`, `vertical` |
| tiktok | `vertical` |
| whatsapp | `a4` (the only size that shows whole in the chat preview) |

The chosen size is passed to the prompt (e.g. for `vertical`: keep text away from the top and bottom 15% covered by the app's UI). Other sizes return 422.

### Locations: structured, and stored on every variant
A variant's location is the same structured shape as the job offer's administrative area (`locality`, `province`, `province_code`, `region`, `country_code`), never free text.
- **Copied at creation.** With no `target_location` in the request, the job offer's area is copied onto the variant. With one, the request's area is used. Storing it (instead of computing a fallback at read time) means a published ad keeps the place it actually advertised even if the job offer moves, and every variant is filterable by location with a plain `WHERE`.
- **Any granularity.** A town (`Brescia`, `BS`), a province only, or a region only (`Lombardia`) for an area-wide campaign. At least one area field is required (Pydantic validator + DB CHECK). The display label adapts: "Brescia (BS)", "Provincia di Brescia", "Lombardia".
- **Channel rules.** Indeed needs a town, because a job board lists each ad at a specific place; a region-only Indeed variant returns 422. Instagram, TikTok and WhatsApp accept any granularity.
- **Street address stays internal.** Variants never store a street. Indeed's `publishing_fields.address` is the job offer's full address when the variant is at the job offer's town, otherwise the variant's town.
- **Filterable.** `GET /variants?region=&province_code=&locality=` (case-insensitive), combinable with the job offer, channel and status filters.
- **One location per variant.** Advertising the same job in three towns means three variants, so each one can be measured separately.

Values computed when a variant is read (not stored, so they never go stale):
- **`job_offer_id`**: taken from the parent ad.
- **`location`**: the stored area fields as one object, plus a `label` (e.g. "Orzinuovi (BS)") used on screen and in the prompt.
- **`publishing_fields`**: Indeed only. Full address, required skills and experience range, built **deterministically from the job offer** and not by the LLM. They are exactly the structured fields in the PDF's job-board example, and they must never be paraphrased.
- **`effective_content`**: `edited_content` if present, otherwise `generated_content`.
- **`image_url`**: `/variants/{id}/image?v=<updated_at>` when an image exists, otherwise `null`.

### Content shape: one schema for every channel
`VariantContent` holds:
- `headline`
- `highlights`: short chips printed on a creative
- `body`
- `bullet_points`
- `call_to_action`
- `visual_brief`: art direction for an image

A single schema keeps storage, editing and the API uniform. What changes per channel and format is the **meaning and the limits** of each field, defined in `llm.py`:

| Channel × format | headline | highlights | body | bullet_points | visual_brief |
|---|---|---|---|---|---|
| indeed × text_only | job title | — | sectioned description ("Il ruolo:", "Cosa ti offre l'azienda:") | "Il tuo profilo" requirements | — |
| instagram/tiktok × image_only | big text on the creative | 2–3 hook lines | one supporting line | ≤ 2 short perks | required |
| instagram/tiktok × text_and_image | text on the creative | 2–3 hook lines | post caption | perks | required |
| whatsapp × text_only | first line of the message | — | 2–3 sentences | key points | — |
| whatsapp × image_only | title of the A4 flyer | 3–5 chips (RAL, contract…) | opening lines | flyer lines | required |
| whatsapp × text_and_image | title of the A4 flyer | 3–5 chips | chat message accompanying the image | flyer lines | required |

**Channel/format pairs that aren't supported** (e.g. Indeed with an image) are rejected with 422. The allowed matrix is simply the keys of `VARIANT_SPECS`.

### How well this holds up if something changes
- **New channel** (e.g. LinkedIn): add an enum value, a `ChannelRules` entry, the `VARIANT_SPECS` entries for its formats and its image sizes in `channels.py`. Enums are stored as plain strings with no database CHECK constraint, so **no schema migration is needed**. Pydantic does the validation.
- **New format or image size:** a new enum value and spec entry. A new size for an existing channel is one line in `CHANNEL_IMAGE_SIZES`.
- **New location granularity** (e.g. a radius around a town, or several towns per ad): the area columns are grouped and only read through `area()`/`location`, so they can move to their own table without changing the API shape.
- **New content field:** add it to `VariantContent` with a default. Older rows stay valid because the content is JSON.

## Data flow: from job offer to the screen

```
POST /job-offers/{id}/variants  {channel, format, image_size?, target_location?, angle?}
  │
  ├─ routes.py   load the JobOffer (404); resolve the area (request or job offer copy);
  │              reject unsupported channel/format/image size, or Indeed without a town (422)
  │
  ├─ llm.py      job_offer_payload(): pick and rename the useful internal fields
  │              (no IDs, status or dates) — the LLM never sees internal metadata
  │              build prompt = system rules + channel rules + format instructions
  │                             + image size + length limits + location label + angle + job offer data
  │              call Ollama with format = VariantContent JSON schema
  │              ┌─ validate JSON → Pydantic VariantContent
  │              ├─ normalize: clear image fields on text formats, lay out Indeed sections
  │              ├─ check: lengths, sections, style (voice, numbers, emoji, markdown, repetition, leaked field names)
  │              └─ problems? send them back to the model and retry (max 3) → else 502
  │
  ├─ routes.py   get-or-create the Ad, insert the AdVariant(status=draft, image_size, area, generated_content)
  │
  └─ response    AdVariantRead: content + location + publishing_fields + effective_content
```

Editing is `PATCH /variants/{id}`. The fields sent are merged onto the current effective content, validated, and stored in `edited_content`.

On screen, the interface (`frontend/index.html`, served by FastAPI at `/`, so no CORS setup is needed) uses only the public API:
- the list shows `effective_content.headline` plus channel, format and image size, status, `location.label` and `angle`, filterable by region and province;
- the editor shows `generated_content` read-only next to editable fields filled from `effective_content`, plus `publishing_fields` for Indeed, and for image formats a button that calls `POST /variants/{id}/image` and a preview of `image_url`;
- format and image size choices come from `GET /channels`, which reads the same `VARIANT_SPECS` and `CHANNEL_IMAGE_SIZES` the backend validates against, so the page can't offer a combination the API would reject.

All model output is inserted with `textContent`, never as HTML.

## Image generation

`images.py` knows nothing about ads: it turns a prompt and a size into a PNG.
- **`ImageGenerator`** tries a list of providers in order and returns the first image (`GeneratedImage`: PNG bytes, provider name, size). If all fail it raises `ImageGenerationError` with each provider's reason, and the route answers 503.
- **`HuggingFaceProvider`** calls `InferenceClient.text_to_image` with `FLUX.1-schnell` (4 steps), using `HF_TOKEN`. Cold starts (503) are retried a couple of times with a pause. Rate limits (429), exhausted credits (402), auth errors, other HTTP errors, timeouts and connection errors raise `ProviderError` right away, so the next provider is tried. With no token it is skipped without a network call.
- **`PollinationsProvider`** is a plain `GET https://image.pollinations.ai/prompt/{encoded prompt}?width=&height=`. Non-200 replies, non-image content types, unreadable images and timeouts are all `ProviderError`.
- Adding a provider means a class with `name` and `generate(prompt, width, height)`.

The ad-specific part is in the route: `POST /variants/{id}/image` takes the effective `visual_brief`, wraps it with `creative_prompt()` (photo style, **no text**, room for an overlay, because headline and highlights belong in a template, not in diffusion output), and scales the variant's `image_size` to a long side of 1024 px in multiples of 16 (`channels.generation_dimensions`, e.g. vertical → 576×1024). The PNG is saved as `IMAGES_DIR/variant_{id}.png`, and the variant stores `image_file` and `image_provider`. Its `image_url` carries `?v=<updated_at>` so a regenerated image isn't served from the browser cache.

## Simplifications to be aware of
- **One image per variant.** Regenerating overwrites it; there's no history of images to A/B test.
- **The generated image is the background only.** Text is not composed onto it, and the A4 flyer's layout brief is used as a photo prompt.
- **Pollinations' output is watermarked and lower quality.** It's a fallback to keep the flow working, not a substitute for FLUX on Hugging Face.
- **Length limits don't depend on image size.** A 9:16 creative gets the same limits as a square one; only the layout hint in the prompt changes.
- **Locations aren't checked against a gazetteer.** "Brescia" with `province_code` "MI" is accepted as given.
- **Generation is synchronous** inside the HTTP request: about 20–60 s for copy with retries, a few seconds to a minute for an image with cold starts and the fallback.
- **`Ad` is 1:1 with a job offer** for now (see above).
- **Status changes have no rules:** any status can follow any other.
- **No migrations.** Tables come from `create_all`, so schema changes need a database rebuild.
- **Time zones are dropped.** SQLite stores datetimes without them, so `2026-02-08T20:05:13.000Z` comes back as `2026-02-08T20:05:13`.
- **Style checks are regex heuristics** tuned to Italian copy (see prompts.md).
