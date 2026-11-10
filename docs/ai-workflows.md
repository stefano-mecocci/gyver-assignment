# AI workflows

The backend was built with **Claude Code**, Anthropic's coding agent, running inside VS Code. I drove it with one focused prompt per step, and reviewed and tested each step before starting the next.

## The flow

The work was split into small steps, each with one clear goal and its own constraints:

| Step | My prompt asked for | What the agent did besides writing code |
|---|---|---|
| 1. Scaffold | FastAPI with the app in `main.py` and the routes in a separate `routes.py` | Called `/health` through FastAPI's test client to confirm the setup |
| 2. Data model | SQLAlchemy 2.0 and Pydantic v2 models for `JobOffer` → `Ad` → `AdVariant`, with exact enums, JSON content and the required indexes | Created the tables on an in-memory database and checked the indexes and a round trip |
| 3. Persistence | Engine and session setup, `POST`/`GET /job-offers`, `seed.py` | Extracted the sample JSON from the assignment PDF, noticed it didn't match the step-2 model, and **adapted the model to the real input** instead of the other way round |
| 4. LLM | An Ollama service with the Pydantic schema as `format`, prompts adapted to channel and format, `POST /job-offers/{id}/variants` | Ran real generations for each channel and iterated on the failures (see prompts.md) |
| 5. Edit and query | `PATCH /variants/{id}` (partial edit, `generated_content` untouched) and `GET /variants` with filters | Tested the filters, the merge semantics and the error codes |
| 6. Review | "Validate everything against the PDF" | Produced a gap analysis: missing deliverables, Indeed publishing fields, flyer chips, location not shown, portability risk |
| 7. Fix and document | Fix the gaps found in step 6, then write these docs | Regenerated the sample variants, built the pre-filled database, and checked that the prompts quoted in prompts.md match the code |
| 8. Re-review and portability | A second evaluation of the project, then "make the model easily switchable" | Added a timeout and clearer errors, documented local and Ollama Cloud setups, and tested a second local model (`llama3.2:3b`) |
| 9. Frontend (optional module) | A minimal page to list ads, create one from the job offer, and edit its content, inside `backend/frontend` | Added the two endpoints the page needs (`GET /job-offers`, `GET /channels`), served the page from FastAPI, and checked the endpoints and the script's syntax |
| 10. Test suite | Test the interface in Firefox with Playwright, as a test suite in `backend/tests` | Wrote interface, API and check tests. A first version copied `gyver.db` and broke as soon as the app was used by hand, so each test now gets a fresh database built by `seed.py` |
| 11. Third review | Analyse the PDF and the project again, list what's missing as short bullets, and give a mark | Ran the suite, inspected the database, and flagged delivery gaps (empty root README, a leftover `/page` route, seed count mismatch) and modelling gaps (free-text locations, Instagram and TikTok merged into `social`, no image size) |
| 12. Platforms and locations | "I care only about location, platform modelling and the stray route" | Split `social` into `instagram` and `tiktok`, added a per-channel `image_size`, replaced free-text locations with a structured, filterable area on each variant, and removed `/page`. Migrated seed data, tests, frontend and docs, and ran one real TikTok generation. It was **blocked from deleting `gyver.db`**, so it built the new database in a scratch folder and left the swap to me |
| 13. Image generation | A pasted spec: FLUX.1-schnell through `huggingface_hub`'s `InferenceClient` with `HF_TOKEN`, handling cold starts, 429 and timeouts, with a Pollinations.ai fallback | Before writing code, **checked the installed `huggingface_hub` version and its error classes**, and probed a call with a bad token to see the real failure. Tested the fallback with fake providers and a local fake Pollinations server, so the suite still needs no network, then ran one live generation and looked at the image |
| 14. Fourth review and docs | A new review, then "update prompts.md, write a README linking the other docs" | Added the image prompt to prompts.md and wrote the root README. Its first draft of the image section **invented a "what didn't work" history** for a prompt that had never been iterated; it caught this itself and replaced it with the real rationale and the one real observation |

Why I worked this way:
- **One layer per prompt** (model → persistence → LLM → editing) kept each change small enough to review properly. It also meant each step was verified before the next one built on it.
- **Exact specs in the prompts** (field names, enums, status codes, request bodies) left little room for the agent to invent its own API shape.
- **A dedicated review step against the source document** (step 6) caught problems that step-by-step work had missed. For example, the job-board example in the PDF needs structured fields (address, skills, experience) that the first content model didn't have.
- **Real model runs, not mocks,** drove the prompt work. Every rule in prompts.md answers a specific bad output.
- **Reviews with a mark, then a narrow fix list.** Each review (steps 6, 8, 11, 14) produced more findings than worth fixing. I picked the few that mattered and named them explicitly ("only location, platform modelling and the stray route"), so the agent didn't spread changes across the codebase.
- **Fakes for the tests, one real run for confidence.** The image service is tested with fake providers, so the suite stays fast and offline. A single live call then confirmed the real path works.

## Tools

- **Claude Code in VS Code.** Its access to my open files, my selections and the IDE's diagnostics meant less context to paste by hand.
- **Project rules in `backend/CLAUDE.md`,** which the agent reads automatically:
  - put every route in `routes.py`
  - keep all imports at the top of the file
  - never add comments to the code

  I added the last two partway through the work. Writing a rule down once is cheaper than repeating it in every prompt.
- **Explicit "do not" lines in the prompts** ("do not write any LLM code yet", "no comments") to keep each step in scope.
- **Local Ollama,** so prompt iteration needed no API key, cost nothing per call, and kept the sample data on the machine.
- **Claude Code's permission checks.** Destructive commands need approval. When the agent tried to delete and rebuild `gyver.db`, it was stopped, built the new database in a scratch folder instead, and left it to me to replace the shipped file.
- **Small throwaway scripts** run by the agent through `uv run`: extracting the JSON from the PDF, smoke tests through FastAPI's `TestClient` on scratch SQLite files, and rendering the final prompts for this documentation. None of them are part of the repo. They were how each step was verified.

## Where I stayed in control

- I chose and ordered the steps and wrote the specification for each.
- I asked for an evaluation of the finished project (gaps, issues, a score) and chose which findings to act on.
- I interrupted the agent mid-task and redirected it, for example to finish the remaining work without more LLM calls.
- I made the product calls: which channel/format pairs are allowed, what an `Ad` means, and what is left out of scope.
- I chose the image stack (FLUX.1-schnell on Hugging Face, Pollinations as a free fallback) and wrote its requirements myself.
- I handled the shipped data by hand: I swapped in the rebuilt database and cleared image references that pointed to files that wouldn't be in the repo.
- I read the agent's docs as critically as its code. AI-written documentation can describe things that never happened, as step 14 shows.
