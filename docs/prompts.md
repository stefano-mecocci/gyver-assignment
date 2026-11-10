# Prompts

There are two kinds of prompt:
- **Copy prompts** (`backend/llm.py`): turn the job offer into the ad's text. Written in Italian because the output is Italian copy.
- **Image prompt** (`backend/images.py`): turns a variant's `visual_brief` into the creative's background photo. See [Image prompt](#image-prompt) at the end.

## Copy prompts

Each generation call sends:
1. **System prompt**: the same for every call.
2. **User prompt**: assembled from channel rules, format instructions, image size (image formats only), length limits, location label, optional angle, and the job offer data.
3. **`format=VariantContent.model_json_schema()`**: Ollama's structured output, which constrains decoding to the JSON schema.

Settings: model `gemma4:e4b-mlx`, `temperature=0.7`. The temperature is deliberately above zero so that repeated generations produce different copy to A/B test.

### Final versions

#### System prompt

```
Sei un copywriter esperto di recruiting per tecnici qualificati del settore elettrico in Italia. Ricevi una job offer interna, densa e non pubblicabile, e la trasformi in un annuncio rivolto ai candidati.
Regole:
- Scrivi sempre in italiano.
- Scrivi come Gyver, il marketplace che segnala l'offerta: parla dell'azienda sempre in terza persona (es. 'AB Group cerca', 'l'azienda offre', mai 'cerchiamo', 'offriamo' o 'la nostra azienda').
- Usa solo informazioni presenti nella job offer: non inventare benefit, cifre, certificazioni o requisiti.
- Seleziona e riscrivi: non copiare blocchi di testo interni, metti in evidenza ciò che interessa a un tecnico (stabilità del contratto, retribuzione, crescita, tipo di impianti, trasferte).
- Scrivi sempre i numeri in cifre, come nei dati (es. 32.000 €, 100 kW, 3-5 anni): mai abbreviati (32k) e mai in lettere (trentaduemila).
- Non ripetere frasi e non aggiungere testo di riempimento per raggiungere una lunghezza: se mancano contenuti, approfondisci informazioni presenti nella job offer.
- Indica come luogo solo quello fornito come 'Luogo da comunicare', senza indirizzi completi.
- Non citare informazioni interne come identificativi, stato della posizione o date di creazione.
- bullet_points e highlights contengono frasi senza simboli iniziali (niente '-', '•' o numeri).
- Rispondi solo con un oggetto JSON conforme allo schema richiesto.
```

#### Channel rules (first block of the user prompt)

| Channel | Text | Checked in code |
|---|---|---|
| indeed | `Canale: job board Indeed. Tono professionale, chiaro e concreto, dai del tu al candidato. Niente emoji, niente hashtag, niente markdown (no asterischi, no grassetto, no #). Il testo deve essere leggibile da un tecnico che confronta più offerte.` | 0 emoji, no `*` |
| instagram, tiktok | `Canale: sponsorizzata su {platform}. Tono diretto, energico, da pari a pari, dai del tu. Apri con un gancio forte che fermi lo scroll. Emoji consentite con moderazione (massimo 3 in totale). Frasi brevissime, nessun gergo da ufficio HR.` (`{platform}` is `Instagram` or `TikTok`) | ≤ 3 emoji, no `*` |
| whatsapp | `Canale: WhatsApp, messaggio inviato a tecnici in una community. Tono colloquiale e affidabile, come un collega che segnala un'opportunità, dai del tu. Emoji consentite con moderazione (massimo 4 in totale). Puoi usare la formattazione WhatsApp *grassetto* per i punti chiave. Nessun hashtag.` | ≤ 4 emoji, single `*` allowed |

#### Format instructions (second block)

**indeed × text_only**
```
Formato: solo testo, job description per la job board. headline: titolo dell'annuncio con ruolo e specializzazione. body: job description in testo semplice divisa in sezioni separate da una riga vuota (usa il carattere di a capo \n, ogni voce di elenco su una riga propria). Prima sezione senza titolo: 2-3 righe su tipo di assunzione, chi è l'azienda e dove si lavora. Poi la sezione 'Il ruolo:' e la sezione 'Cosa ti offre l'azienda:', ciascuna con il titolo su una riga propria seguito da 3-4 righe che iniziano con '- '. Non aggiungere altre sezioni: i requisiti vanno solo in bullet_points. bullet_points: i requisiti della sezione 'Il tuo profilo', che verrà aggiunta dopo il body. call_to_action: invito a candidarsi tramite Indeed. highlights: lista vuota. visual_brief: null.
```

**instagram/tiktok × image_only** (same spec object for both; the channel rules differ only in the platform name)
```
Formato: solo immagine, creative sponsorizzata. Tutto il testo viene stampato sull'immagine e deve essere leggibile in 2 secondi. headline: ruolo in poche parole, il testo più grande. highlights: 2-3 righe di 3-5 parole sotto il titolo, il gancio più forte prima (es. 'Entra in una multinazionale', 'Impianti FV oltre 100 kW'). body: una sola riga di supporto con luogo o vantaggio principale. bullet_points: al massimo 2 vantaggi, pochissime parole. call_to_action: testo del bottone, brevissimo. visual_brief: descrizione della foto o illustrazione, con soggetto, ambientazione e atmosfera.
```

**instagram/tiktok × text_and_image**
```
Formato: creative sponsorizzata con didascalia del post. headline: ruolo in poche parole, stampato sull'immagine. highlights: 2-3 righe di 3-5 parole stampate sotto il titolo, il gancio più forte prima. body: didascalia del post sotto l'immagine, che espande il gancio con ruolo, luogo e cosa offre l'azienda. bullet_points: i vantaggi principali, ripresi nella didascalia. call_to_action: invito breve a candidarsi dal link. visual_brief: descrizione della foto o illustrazione, con soggetto, ambientazione e atmosfera.
```

**whatsapp × text_only**
```
Formato: solo testo, un messaggio WhatsApp che si legge senza scorrere troppo. headline: prima riga del messaggio, con ruolo e luogo. body: 2-3 frasi brevi su azienda, ruolo e cosa offre. bullet_points: i punti chiave (contratto, RAL, trasferte, requisiti essenziali). call_to_action: invito a rispondere al messaggio per candidarsi. highlights: lista vuota. visual_brief: null.
```

**whatsapp × image_only**
```
Formato: solo immagine, volantino da condividere in chat. Tutto il testo viene stampato sul volantino. headline: ruolo e specializzazione. highlights: 3-5 etichette brevi in evidenza in cima (es. retribuzione, contratto, crescita, luogo e trasferte, tipo di impianti). body: 1-2 righe di apertura su tipo di assunzione e azienda. bullet_points: righe del volantino in ordine: cosa farai, cosa offre l'azienda, il tuo profilo. call_to_action: come candidarsi, molto breve. visual_brief: impaginazione del volantino A4 (logo azienda, colori delle etichette, eventuale foto).
```

**whatsapp × text_and_image**
```
Formato: volantino accompagnato da un breve messaggio WhatsApp. headline: ruolo e specializzazione, titolo del volantino. highlights: 3-5 etichette brevi in evidenza in cima al volantino (es. retribuzione, contratto, luogo e trasferte). bullet_points: righe del volantino (cosa farai, cosa offre l'azienda, il tuo profilo). body: il messaggio che accompagna l'immagine in chat, 1-3 frasi che invogliano ad aprirla. call_to_action: invito a rispondere al messaggio per candidarsi. visual_brief: impaginazione del volantino A4 (logo azienda, colori delle etichette, eventuale foto).
```

#### Length limits (third block, written from `ContentLimits`)

| Channel × format | headline | body | bullets (count / chars) | CTA | highlights (count / chars) |
|---|---|---|---|---|---|
| indeed × text_only | 80 | 400–1800 | 3–6 / 120 | 120 | — |
| instagram/tiktok × image_only | 35 | 10–90 | 0–2 / 40 | 25 | 2–3 / 40 |
| instagram/tiktok × text_and_image | 35 | 150–500 | 0–4 / 60 | 40 | 2–3 / 40 |
| whatsapp × text_only | 70 | 120–500 | 3–6 / 70 | 80 | — |
| whatsapp × image_only | 60 | 20–220 | 4–8 / 90 | 60 | 3–5 / 35 |
| whatsapp × text_and_image | 60 | 60–350 | 4–8 / 90 | 80 | 3–5 / 35 |

#### Image size (inserted after the format instructions, from `channels.IMAGE_SPECS`)

Size used to be hard-coded in the format text ("creative quadrata 1080x1080", "volantino A4"). It is now a field of the variant, so the size line is added separately:

| image_size | Prompt line |
|---|---|
| square | `Dimensione dell'immagine: quadrata 1:1, 1080x1080 px, per il feed.` |
| portrait | `Dimensione dell'immagine: verticale 4:5, 1080x1350 px, per il feed: c'è spazio per una riga in più sotto il titolo.` |
| vertical | `Dimensione dell'immagine: verticale 9:16, 1080x1920 px, a schermo intero (storie, reel, TikTok): titolo e testi nella parte centrale, lontani dal 15% superiore e inferiore coperto dall'interfaccia dell'app.` |
| a4 | `Dimensione dell'immagine: volantino A4 verticale, 1240x1754 px: l'unico formato che si vede intero anche nell'anteprima della chat.` |

#### Example of the full user prompt (tiktok × image_only, vertical, location overridden to Brescia, with an angle)

```
Canale: sponsorizzata su TikTok. Tono diretto, energico, da pari a pari, dai del tu. Apri con un gancio forte che fermi lo scroll. Emoji consentite con moderazione (massimo 3 in totale). Frasi brevissime, nessun gergo da ufficio HR.

Formato: solo immagine, creative sponsorizzata. [...format instructions above...]

Dimensione dell'immagine: verticale 9:16, 1080x1920 px, a schermo intero (storie, reel, TikTok): titolo e testi nella parte centrale, lontani dal 15% superiore e inferiore coperto dall'interfaccia dell'app.

Vincoli di lunghezza (caratteri, spazi inclusi):
- headline: massimo 35
- body: tra 10 e 90
- bullet_points: da 0 a 2 elementi, ciascuno massimo 40
- call_to_action: massimo 25
- highlights: da 2 a 3 elementi, ciascuno massimo 40
- visual_brief: obbligatorio, 1-3 frasi

Luogo da comunicare: Brescia (BS)

Angolo creativo di questa variante: impianti fotovoltaici oltre 100 kW. Costruisci headline e gancio attorno a questo angolo, restando fedele ai dati.

Job offer interna:
{ "titolo": ..., "azienda": ..., "contratto": ..., "esperienza_anni": {...}, "ral": {...},
  "competenze_richieste": [...], "descrizione_ruolo": ..., "sede_e_orari": ...,
  "descrizione_azienda": ..., "requisiti": ..., "pacchetto_retributivo": ... }
```

The job offer is passed as JSON with Italian keys. Internal metadata (ID, status, creation date, street address) is left out on purpose, so the model cannot leak it.

#### Correction message (sent when a reply fails a check)

```
La risposta non rispetta i vincoli:
- <problem 1>
- <problem 2>
Correggi e restituisci di nuovo l'intero oggetto JSON.
```

Each problem is specific and measurable, e.g. `headline ha 47 caratteri, massimo 35`, `numeri scritti in lettere (trentottomila): scrivili in cifre come nei dati`.

### What didn't work in earlier versions, and what changed

Every change below came from a failure seen in a real generation against `gemma4:e4b-mlx` with the sample job offer.

| # | What went wrong | Change |
|---|---|---|
| 1 | **Indeed body too short.** With a 600-character minimum, the model produced 412 → 513 → 577 characters over three attempts, and the request failed. | Lowered to 400. The requirements moved to `bullet_points`, so the body doesn't have to carry everything. |
| 2 | **Off by one or two characters** (a 61-character headline against a 60 limit) caused a full failure. Small models can't count characters exactly. | The prompt states the target limits, but the check allows **15% leeway** (`LIMIT_TOLERANCE`). |
| 3 | **Wrong voice:** "Siamo una multinazionale…", "Ti offriamo…", as if the company were writing. | Rule: write as Gyver and refer to the company in the third person. A regex check catches `offriamo`, `cerchiamo`, `la nostra azienda`, … |
| 4 | **Filler to reach the minimum length:** the Indeed body ended with "2000 caratteri circa." three times. | Rule against filler and repetition, plus a repeated-sentence check (one that doesn't split "32.000" at the dot). |
| 5 | After "32k" was banned, the model **spelled numbers out in words** ("trentadue mila", "cento kW"). | Rule: always digits, as in the data. A regex check catches `…mila` and `cento`. |
| 6 | **Social highlights written as sentences** (42–50 characters, three times in a row). | Instructions now say "3-5 words" with two examples taken from the PDF creative; limit raised from 35 to 40. |
| 7 | **No line breaks in the Indeed body.** The model wouldn't produce `\n` inside a JSON string, even after three explicit corrections. | The sections are laid out **in code** (`format_sections`): each section title on its own line, each " - " item on a new line. The model only has to produce the content. |
| 8 | **Requirements appended to the Indeed body** ("Il tuo profilo: …", "Requisiti del candidato: …"), duplicating `bullet_points`. One run even contained a literal `call_to_action:` followed by "0.0.0". | The trailing requirements section is **removed in code** (`drop_trailing_sections`), because it always belongs in the bullets. JSON field names appearing in the copy are now a check failure. |
| 9 | The model once **copied part of the instructions** into the copy ("2-3 righe su tipo di assunzione…"). | Caught by the repeated-sentence check and fixed on the retry. Kept as a known risk. |
| 10 | The original content shape (headline / body / bullets / CTA) **couldn't express** the WhatsApp flyer's chips or what the image should show. | Added `highlights` and `visual_brief` to the schema. They are required for image formats and cleared in code for text-only formats. |

### How the model is held to the expected output

From hardest to softest constraint:

1. **JSON-schema-constrained decoding.** Ollama's `format` parameter receives the Pydantic schema, so the reply is always JSON with the expected keys.
2. **Pydantic validation.** `VariantContent.model_validate_json` rejects missing or invalid fields and unknown keys (`extra="forbid"`).
3. **Normalization in code.** Text-only formats get empty `highlights` and a null `visual_brief`. The Indeed body is laid out into sections, and its trailing requirements block is removed. These are fixes the model can't make reliably, and they need no judgement.
4. **Programmatic checks.** Lengths (with 15% leeway), number of bullets and highlights, required Indeed sections, the visual brief on image formats, emoji per channel, markdown, company first person, abbreviated or spelled-out numbers, repeated sentences, JSON field names in the text.
5. **Prompt rules.** Tone, the facts-only rule, what to emphasise, and how location is used.

Facts that must never be paraphrased are **not generated**: the Indeed address, skills and experience are copied directly from the job offer into `publishing_fields`.

### What happens when the reply doesn't conform

```
attempt 1 ── valid? ── yes ──▶ saved as a draft variant (201)
   │ no
   ▼
send the model its previous reply + the list of problems, ask for the full JSON again
   │
attempt 2 … attempt 3 ── still failing ──▶ 502 Bad Gateway with the remaining problems; nothing is saved
```

- Each failed attempt is logged as a warning, with its list of problems.
- If Ollama is unreachable, the model isn't installed, or a reply takes longer than `OLLAMA_TIMEOUT` (180 s by default), the endpoint returns **503**, with no retry.
- Nothing is written to the database until a reply passes every check. A failure never leaves an empty `Ad` or a half-saved variant behind.

## Image prompt

### Final version

`creative_prompt()` wraps the variant's effective `visual_brief` (the edited one if present). The brief itself comes from the copy prompt, so it's in Italian; the wrapper is in English, which FLUX follows more reliably.

```
Professional recruiting advertisement photo, photorealistic, natural light, high quality. {visual_brief} No text, no letters, no logos, no watermark. Leave clean empty space for a text overlay.
```

Example for the seeded Instagram square variant:

```
Professional recruiting advertisement photo, photorealistic, natural light, high quality. Un tecnico giovane e dinamico in un contesto industriale ad alta tecnologia (impianto fotovoltaico). L'atmosfera deve essere ambiziosa, moderna, utilizzando colori come il blu e l'arancione. L'inquadratura deve essere professionale e potente. No text, no letters, no logos, no watermark. Leave clean empty space for a text overlay.
```

Parameters sent with it:

| Parameter | Value | Why |
|---|---|---|
| model | `black-forest-labs/FLUX.1-schnell` (`HF_IMAGE_MODEL`) | Fast distilled model, usable on Hugging Face's free tier |
| `num_inference_steps` | 4 | What schnell is distilled for; more steps add latency, not quality |
| width × height | the variant's `image_size` scaled to a 1024 px long side, multiples of 16 | square 1024×1024, portrait 816×1024, vertical 576×1024, a4 720×1024 |
| Pollinations | same prompt in the URL path, `width`, `height`, `model=flux`, `nologo=true` | Same model family, so the fallback reads the prompt the same way |

### Earlier versions

There were none: unlike the copy prompts, the image prompt was written once and not iterated. Each part is there for a reason known in advance, not one learned from failures:

| Part | Why |
|---|---|
| "recruiting advertisement photo, photorealistic, natural light" | Fixes the genre and style, so the result looks like a job ad and not an illustration. |
| "No text, no letters, no logos, no watermark" | Diffusion models write text badly. Headline and highlights stay as data for a template overlay. |
| "Leave clean empty space for a text overlay" | The copy has to fit somewhere on the creative. |
| Size scaled to 1024 px in multiples of 16 | Channel sizes (1080×1920, 1240×1754) aren't valid for every provider. |

The only real run so far (Pollinations, no `HF_TOKEN`) showed two limits:
- the result left out part of the brief (the solar plant);
- it still carried a Pollinations watermark despite `nologo=true`, which only works for registered accounts.

The Hugging Face path hasn't been tested with a real token.

### How the output is constrained

An image can't be validated like JSON, so the checks are on the response, not the content:
- the HTTP status must be success;
- Pollinations must answer with an `image/*` content type;
- the bytes must open as an image with Pillow; they are then re-encoded as PNG, so every stored file has the same format.

Prompt-level constraints (no text, empty space) are best-effort. Nothing checks the picture itself.

### What happens when generation fails

```
Hugging Face ── no HF_TOKEN ─────────────────────────────┐
   │ 503 (cold start) → wait HF_COLD_START_WAIT s, retry  │
   │   (HF_COLD_START_RETRIES times)                      │
   │ 429 / 402 / 401 / 403 / other HTTP / timeout ────────┤
   ▼ ok                                                   ▼
saved as variant_{id}.png                          Pollinations.ai
                                                     │ non-200 / not an image / timeout
                                                     ▼
                                     503 with every provider's reason; nothing saved
```

- Each provider failure is logged as a warning with its reason.
- The error message names both failures, e.g. `huggingface: Hugging Face returned 429: rate limit or quota reached; pollinations: Pollinations timed out`.
- The variant is only updated (`image_file`, `image_provider`) after a file has been written, so a failure never leaves a variant pointing to a missing image.
