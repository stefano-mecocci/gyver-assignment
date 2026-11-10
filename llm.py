import json
import logging
import os
import re
from dataclasses import dataclass

import httpx
from ollama import Client, ResponseError
from pydantic import ValidationError

from channels import IMAGE_SPECS, UnsupportedCombinationError
from models import AdFormat, Channel, ImageSize, JobOffer
from schemas import VariantContent

logger = logging.getLogger(__name__)

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma4:e4b-mlx")
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT", "180"))
MAX_ATTEMPTS = 3
LIMIT_TOLERANCE = 0.15

client = Client(host=OLLAMA_HOST, timeout=OLLAMA_TIMEOUT_SECONDS)

EMOJI_PATTERN = re.compile("[\\U0001F000-\\U0001FAFF\\u2600-\\u27BF\\u2B00-\\u2BFF]")
COMPANY_FIRST_PERSON_PATTERN = re.compile(
    r"\b(offriamo|cerchiamo|assumiamo|stiamo cercando|siamo alla ricerca|la nostra azienda|il nostro team|unisciti a noi)\b",
    re.IGNORECASE,
)
ABBREVIATED_AMOUNT_PATTERN = re.compile(r"\b\d+(?:[.,]\d+)?\s?[kK]\b")
SPELLED_NUMBER_PATTERN = re.compile(r"\b(\w*mila|cento)\b", re.IGNORECASE)
FIELD_NAME_PATTERN = re.compile(
    r"\b(headline|highlights|bullet_points|call_to_action|visual_brief)\b", re.IGNORECASE
)
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


class LLMError(Exception):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMOutputError(LLMError):
    pass


@dataclass(frozen=True)
class ChannelRules:
    guidelines: str
    max_emoji: int
    allow_asterisks: bool


@dataclass(frozen=True)
class ContentLimits:
    headline_max: int
    body_min: int
    body_max: int
    bullets_min: int
    bullets_max: int
    bullet_max: int
    cta_max: int
    highlights_min: int = 0
    highlights_max: int = 0
    highlight_max: int = 0

    @property
    def has_image(self) -> bool:
        return self.highlights_max > 0


@dataclass(frozen=True)
class VariantSpec:
    instructions: str
    limits: ContentLimits
    required_sections: tuple[str, ...] = ()
    trailing_sections_to_drop: tuple[str, ...] = ()


SOCIAL_GUIDELINES = (
    "Canale: sponsorizzata su {platform}. Tono diretto, energico, da pari a pari, dai del tu. "
    "Apri con un gancio forte che fermi lo scroll. Emoji consentite con moderazione (massimo 3 in totale). "
    "Frasi brevissime, nessun gergo da ufficio HR."
)

CHANNEL_RULES: dict[Channel, ChannelRules] = {
    Channel.INDEED: ChannelRules(
        guidelines=(
            "Canale: job board Indeed. Tono professionale, chiaro e concreto, dai del tu al candidato. "
            "Niente emoji, niente hashtag, niente markdown (no asterischi, no grassetto, no #). "
            "Il testo deve essere leggibile da un tecnico che confronta più offerte."
        ),
        max_emoji=0,
        allow_asterisks=False,
    ),
    Channel.INSTAGRAM: ChannelRules(
        guidelines=SOCIAL_GUIDELINES.format(platform="Instagram"),
        max_emoji=3,
        allow_asterisks=False,
    ),
    Channel.TIKTOK: ChannelRules(
        guidelines=SOCIAL_GUIDELINES.format(platform="TikTok"),
        max_emoji=3,
        allow_asterisks=False,
    ),
    Channel.WHATSAPP: ChannelRules(
        guidelines=(
            "Canale: WhatsApp, messaggio inviato a tecnici in una community. Tono colloquiale e affidabile, "
            "come un collega che segnala un'opportunità, dai del tu. Emoji consentite con moderazione "
            "(massimo 4 in totale). Puoi usare la formattazione WhatsApp *grassetto* per i punti chiave. "
            "Nessun hashtag."
        ),
        max_emoji=4,
        allow_asterisks=True,
    ),
}

TEXT_ONLY_FIELDS = "highlights: lista vuota. visual_brief: null."

SOCIAL_IMAGE_ONLY = VariantSpec(
    instructions=(
        "Formato: solo immagine, creative sponsorizzata. Tutto il testo viene stampato sull'immagine "
        "e deve essere leggibile in 2 secondi. "
        "headline: ruolo in poche parole, il testo più grande. "
        "highlights: 2-3 righe di 3-5 parole sotto il titolo, il gancio più forte prima "
        "(es. 'Entra in una multinazionale', 'Impianti FV oltre 100 kW'). "
        "body: una sola riga di supporto con luogo o vantaggio principale. "
        "bullet_points: al massimo 2 vantaggi, pochissime parole. "
        "call_to_action: testo del bottone, brevissimo. "
        "visual_brief: descrizione della foto o illustrazione, con soggetto, ambientazione e atmosfera."
    ),
    limits=ContentLimits(
        headline_max=35, body_min=10, body_max=90,
        bullets_min=0, bullets_max=2, bullet_max=40, cta_max=25,
        highlights_min=2, highlights_max=3, highlight_max=40,
    ),
)

SOCIAL_TEXT_AND_IMAGE = VariantSpec(
    instructions=(
        "Formato: creative sponsorizzata con didascalia del post. "
        "headline: ruolo in poche parole, stampato sull'immagine. "
        "highlights: 2-3 righe di 3-5 parole stampate sotto il titolo, il gancio più forte prima. "
        "body: didascalia del post sotto l'immagine, che espande il gancio con ruolo, luogo e "
        "cosa offre l'azienda. "
        "bullet_points: i vantaggi principali, ripresi nella didascalia. "
        "call_to_action: invito breve a candidarsi dal link. "
        "visual_brief: descrizione della foto o illustrazione, con soggetto, ambientazione e atmosfera."
    ),
    limits=ContentLimits(
        headline_max=35, body_min=150, body_max=500,
        bullets_min=0, bullets_max=4, bullet_max=60, cta_max=40,
        highlights_min=2, highlights_max=3, highlight_max=40,
    ),
)

VARIANT_SPECS: dict[tuple[Channel, AdFormat], VariantSpec] = {
    (Channel.INDEED, AdFormat.TEXT_ONLY): VariantSpec(
        instructions=(
            "Formato: solo testo, job description per la job board. "
            "headline: titolo dell'annuncio con ruolo e specializzazione. "
            "body: job description in testo semplice divisa in sezioni separate da una riga vuota "
            "(usa il carattere di a capo \\n, ogni voce di elenco su una riga propria). "
            "Prima sezione senza titolo: 2-3 righe su tipo di assunzione, chi è l'azienda e dove si lavora. "
            "Poi la sezione 'Il ruolo:' e la sezione 'Cosa ti offre l'azienda:', ciascuna con il titolo su una "
            "riga propria seguito da 3-4 righe che iniziano con '- '. Non aggiungere altre sezioni: i requisiti "
            "vanno solo in bullet_points. "
            "bullet_points: i requisiti della sezione 'Il tuo profilo', che verrà aggiunta dopo il body. "
            "call_to_action: invito a candidarsi tramite Indeed. "
            + TEXT_ONLY_FIELDS
        ),
        limits=ContentLimits(
            headline_max=80, body_min=400, body_max=1800,
            bullets_min=3, bullets_max=6, bullet_max=120, cta_max=120,
        ),
        required_sections=("Il ruolo:", "Cosa ti offre l'azienda:"),
        trailing_sections_to_drop=("Il tuo profilo", "Requisiti"),
    ),
    (Channel.INSTAGRAM, AdFormat.IMAGE_ONLY): SOCIAL_IMAGE_ONLY,
    (Channel.INSTAGRAM, AdFormat.TEXT_AND_IMAGE): SOCIAL_TEXT_AND_IMAGE,
    (Channel.TIKTOK, AdFormat.IMAGE_ONLY): SOCIAL_IMAGE_ONLY,
    (Channel.TIKTOK, AdFormat.TEXT_AND_IMAGE): SOCIAL_TEXT_AND_IMAGE,
    (Channel.WHATSAPP, AdFormat.TEXT_ONLY): VariantSpec(
        instructions=(
            "Formato: solo testo, un messaggio WhatsApp che si legge senza scorrere troppo. "
            "headline: prima riga del messaggio, con ruolo e luogo. "
            "body: 2-3 frasi brevi su azienda, ruolo e cosa offre. "
            "bullet_points: i punti chiave (contratto, RAL, trasferte, requisiti essenziali). "
            "call_to_action: invito a rispondere al messaggio per candidarsi. "
            + TEXT_ONLY_FIELDS
        ),
        limits=ContentLimits(
            headline_max=70, body_min=120, body_max=500,
            bullets_min=3, bullets_max=6, bullet_max=70, cta_max=80,
        ),
    ),
    (Channel.WHATSAPP, AdFormat.IMAGE_ONLY): VariantSpec(
        instructions=(
            "Formato: solo immagine, volantino da condividere in chat. Tutto il testo viene stampato sul volantino. "
            "headline: ruolo e specializzazione. "
            "highlights: 3-5 etichette brevi in evidenza in cima (es. retribuzione, contratto, crescita, "
            "luogo e trasferte, tipo di impianti). "
            "body: 1-2 righe di apertura su tipo di assunzione e azienda. "
            "bullet_points: righe del volantino in ordine: cosa farai, cosa offre l'azienda, il tuo profilo. "
            "call_to_action: come candidarsi, molto breve. "
            "visual_brief: impaginazione del volantino A4 (logo azienda, colori delle etichette, eventuale foto)."
        ),
        limits=ContentLimits(
            headline_max=60, body_min=20, body_max=220,
            bullets_min=4, bullets_max=8, bullet_max=90, cta_max=60,
            highlights_min=3, highlights_max=5, highlight_max=35,
        ),
    ),
    (Channel.WHATSAPP, AdFormat.TEXT_AND_IMAGE): VariantSpec(
        instructions=(
            "Formato: volantino accompagnato da un breve messaggio WhatsApp. "
            "headline: ruolo e specializzazione, titolo del volantino. "
            "highlights: 3-5 etichette brevi in evidenza in cima al volantino (es. retribuzione, contratto, "
            "luogo e trasferte). "
            "bullet_points: righe del volantino (cosa farai, cosa offre l'azienda, il tuo profilo). "
            "body: il messaggio che accompagna l'immagine in chat, 1-3 frasi che invogliano ad aprirla. "
            "call_to_action: invito a rispondere al messaggio per candidarsi. "
            "visual_brief: impaginazione del volantino A4 (logo azienda, colori delle etichette, eventuale foto)."
        ),
        limits=ContentLimits(
            headline_max=60, body_min=60, body_max=350,
            bullets_min=4, bullets_max=8, bullet_max=90, cta_max=80,
            highlights_min=3, highlights_max=5, highlight_max=35,
        ),
    ),
}

SYSTEM_PROMPT = (
    "Sei un copywriter esperto di recruiting per tecnici qualificati del settore elettrico in Italia. "
    "Ricevi una job offer interna, densa e non pubblicabile, e la trasformi in un annuncio rivolto ai candidati.\n"
    "Regole:\n"
    "- Scrivi sempre in italiano.\n"
    "- Scrivi come Gyver, il marketplace che segnala l'offerta: parla dell'azienda sempre in terza persona "
    "(es. 'AB Group cerca', 'l'azienda offre', mai 'cerchiamo', 'offriamo' o 'la nostra azienda').\n"
    "- Usa solo informazioni presenti nella job offer: non inventare benefit, cifre, certificazioni o requisiti.\n"
    "- Seleziona e riscrivi: non copiare blocchi di testo interni, metti in evidenza ciò che interessa a un tecnico "
    "(stabilità del contratto, retribuzione, crescita, tipo di impianti, trasferte).\n"
    "- Scrivi sempre i numeri in cifre, come nei dati (es. 32.000 €, 100 kW, 3-5 anni): "
    "mai abbreviati (32k) e mai in lettere (trentaduemila).\n"
    "- Non ripetere frasi e non aggiungere testo di riempimento per raggiungere una lunghezza: "
    "se mancano contenuti, approfondisci informazioni presenti nella job offer.\n"
    "- Indica come luogo solo quello fornito come 'Luogo da comunicare', senza indirizzi completi.\n"
    "- Non citare informazioni interne come identificativi, stato della posizione o date di creazione.\n"
    "- bullet_points e highlights contengono frasi senza simboli iniziali (niente '-', '•' o numeri).\n"
    "- Rispondi solo con un oggetto JSON conforme allo schema richiesto."
)


def supported_formats(channel: Channel) -> list[AdFormat]:
    return [fmt for (ch, fmt) in VARIANT_SPECS if ch == channel]


def get_spec(channel: Channel, ad_format: AdFormat) -> VariantSpec:
    spec = VARIANT_SPECS.get((channel, ad_format))
    if spec is None:
        allowed = ", ".join(fmt.value for fmt in supported_formats(channel))
        raise UnsupportedCombinationError(
            f"Format '{ad_format.value}' is not supported for channel '{channel.value}'. Allowed: {allowed}"
        )
    return spec


def job_offer_payload(job_offer: JobOffer) -> dict:
    return {
        "titolo": job_offer.title,
        "azienda": job_offer.company_name,
        "contratto": job_offer.contract_type,
        "esperienza_anni": {"min": job_offer.min_exp_years, "max": job_offer.max_exp_years},
        "ral": {"min": job_offer.ral_min, "max": job_offer.ral_max, "valuta": job_offer.currency},
        "competenze_richieste": job_offer.required_skills,
        "descrizione_ruolo": job_offer.role_description,
        "sede_e_orari": job_offer.location_and_hours,
        "descrizione_azienda": job_offer.company_description,
        "requisiti": job_offer.requirements_description,
        "pacchetto_retributivo": job_offer.compensation_package,
    }


def describe_limits(limits: ContentLimits) -> str:
    lines = [
        "Vincoli di lunghezza (caratteri, spazi inclusi):",
        f"- headline: massimo {limits.headline_max}",
        f"- body: tra {limits.body_min} e {limits.body_max}",
        f"- bullet_points: da {limits.bullets_min} a {limits.bullets_max} elementi, "
        f"ciascuno massimo {limits.bullet_max}",
        f"- call_to_action: massimo {limits.cta_max}",
    ]
    if limits.has_image:
        lines.append(
            f"- highlights: da {limits.highlights_min} a {limits.highlights_max} elementi, "
            f"ciascuno massimo {limits.highlight_max}"
        )
        lines.append("- visual_brief: obbligatorio, 1-3 frasi")
    return "\n".join(lines)


def build_user_prompt(
    job_offer: JobOffer,
    channel: Channel,
    ad_format: AdFormat,
    location: str,
    image_size: ImageSize | None,
    angle: str | None,
) -> str:
    spec = get_spec(channel, ad_format)
    sections = [CHANNEL_RULES[channel].guidelines, spec.instructions]
    if image_size is not None:
        sections.append(f"Dimensione dell'immagine: {IMAGE_SPECS[image_size].prompt}.")
    sections += [describe_limits(spec.limits), f"Luogo da comunicare: {location}"]
    if angle:
        sections.append(
            f"Angolo creativo di questa variante: {angle}. Costruisci headline e gancio attorno a questo angolo, "
            "restando fedele ai dati."
        )
    sections.append("Job offer interna:\n" + json.dumps(job_offer_payload(job_offer), ensure_ascii=False, indent=2))
    return "\n\n".join(sections)


def within_max(length: int, limit: int) -> bool:
    return length <= limit * (1 + LIMIT_TOLERANCE)


def within_min(length: int, limit: int) -> bool:
    return length >= limit * (1 - LIMIT_TOLERANCE)


def check_limits(content: VariantContent, limits: ContentLimits) -> list[str]:
    problems = []
    if not within_max(len(content.headline), limits.headline_max):
        problems.append(f"headline ha {len(content.headline)} caratteri, massimo {limits.headline_max}")
    if not (within_min(len(content.body), limits.body_min) and within_max(len(content.body), limits.body_max)):
        problems.append(
            f"body ha {len(content.body)} caratteri, deve stare tra {limits.body_min} e {limits.body_max}"
        )
    if not limits.bullets_min <= len(content.bullet_points) <= limits.bullets_max:
        problems.append(
            f"bullet_points ha {len(content.bullet_points)} elementi, "
            f"devono essere da {limits.bullets_min} a {limits.bullets_max}"
        )
    for index, bullet in enumerate(content.bullet_points, start=1):
        if not within_max(len(bullet), limits.bullet_max):
            problems.append(f"bullet_points[{index}] ha {len(bullet)} caratteri, massimo {limits.bullet_max}")
    if not within_max(len(content.call_to_action), limits.cta_max):
        problems.append(f"call_to_action ha {len(content.call_to_action)} caratteri, massimo {limits.cta_max}")
    if limits.has_image:
        if not limits.highlights_min <= len(content.highlights) <= limits.highlights_max:
            problems.append(
                f"highlights ha {len(content.highlights)} elementi, "
                f"devono essere da {limits.highlights_min} a {limits.highlights_max}"
            )
        for index, highlight in enumerate(content.highlights, start=1):
            if not within_max(len(highlight), limits.highlight_max):
                problems.append(
                    f"highlights[{index}] ha {len(highlight)} caratteri, massimo {limits.highlight_max}"
                )
        if not content.visual_brief or not content.visual_brief.strip():
            problems.append("visual_brief è obbligatorio per i formati con immagine")
    return problems


def all_text(content: VariantContent) -> str:
    return "\n".join([content.headline, *content.highlights, content.body, *content.bullet_points, content.call_to_action])


def check_style(content: VariantContent, rules: ChannelRules) -> list[str]:
    problems = []
    text = all_text(content)
    leaked = sorted({match.group(0) for match in FIELD_NAME_PATTERN.finditer(text)})
    if leaked:
        problems.append(
            f"il testo contiene nomi di campi JSON ({', '.join(leaked)}): ogni contenuto va solo nel suo campo"
        )
    first_person = sorted({match.group(0).lower() for match in COMPANY_FIRST_PERSON_PATTERN.finditer(text)})
    if first_person:
        problems.append(
            f"l'azienda parla in prima persona ({', '.join(first_person)}): usa la terza persona"
        )
    abbreviated = sorted({match.group(0) for match in ABBREVIATED_AMOUNT_PATTERN.finditer(text)})
    if abbreviated:
        problems.append(f"cifre abbreviate ({', '.join(abbreviated)}): scrivile in cifre come nei dati")
    spelled = sorted({match.group(0).lower() for match in SPELLED_NUMBER_PATTERN.finditer(text)})
    if spelled:
        problems.append(f"numeri scritti in lettere ({', '.join(spelled)}): scrivili in cifre come nei dati")
    sentences = [part.strip().lower() for part in SENTENCE_SPLIT_PATTERN.split(content.body) if part.strip()]
    repeated = sorted({sentence for sentence in sentences if sentences.count(sentence) > 1 and len(sentence) > 15})
    if repeated:
        problems.append(f"frasi ripetute nel body ({'; '.join(repeated)}): elimina le ripetizioni")
    emoji_count = len(EMOJI_PATTERN.findall(text))
    if emoji_count > rules.max_emoji:
        problems.append(f"ci sono {emoji_count} emoji, massimo {rules.max_emoji} per questo canale")
    if "**" in text or (not rules.allow_asterisks and "*" in text):
        problems.append("rimuovi la formattazione markdown con asterischi")
    return problems


def check_sections(content: VariantContent, spec: VariantSpec) -> list[str]:
    lines = [line.strip().lower() for line in content.body.splitlines()]
    missing = [section for section in spec.required_sections if section.lower() not in lines]
    if missing:
        return [
            f"nel body mancano le sezioni {', '.join(missing)}: ogni titolo di sezione va su una riga propria"
        ]
    return []


def drop_trailing_sections(body: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        body = re.sub(rf"\s*\b{re.escape(marker)}[^:\n]{{0,40}}:.*$", "", body, flags=re.IGNORECASE | re.DOTALL)
    return body


def format_sections(body: str, sections: tuple[str, ...]) -> str:
    for section in sections:
        body = re.sub(rf"\s*{re.escape(section)}\s*", f"\n\n{section}\n", body, flags=re.IGNORECASE)
    body = re.sub(r"[ \t]+-\s+", "\n- ", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def normalize(content: VariantContent, spec: VariantSpec) -> VariantContent:
    updates = {}
    if not spec.limits.has_image:
        updates.update(highlights=[], visual_brief=None)
    if spec.required_sections or spec.trailing_sections_to_drop:
        body = drop_trailing_sections(content.body, spec.trailing_sections_to_drop)
        updates["body"] = format_sections(body, spec.required_sections)
    return content.model_copy(update=updates)


def chat(messages: list[dict[str, str]]) -> str:
    try:
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            format=VariantContent.model_json_schema(),
            options={"temperature": 0.7},
        )
    except ResponseError as exc:
        raise LLMUnavailableError(f"Ollama error: {exc.error}") from exc
    except ConnectionError as exc:
        raise LLMUnavailableError(f"Cannot reach Ollama at {OLLAMA_HOST}") from exc
    except httpx.TimeoutException as exc:
        raise LLMUnavailableError(
            f"Model '{OLLAMA_MODEL}' did not answer within {OLLAMA_TIMEOUT_SECONDS:g} seconds"
        ) from exc
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(f"Error talking to Ollama at {OLLAMA_HOST}: {exc}") from exc
    return response.message.content or ""


def generate_variant_content(
    job_offer: JobOffer,
    channel: Channel,
    ad_format: AdFormat,
    location: str,
    image_size: ImageSize | None = None,
    angle: str | None = None,
) -> VariantContent:
    spec = get_spec(channel, ad_format)
    rules = CHANNEL_RULES[channel]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(job_offer, channel, ad_format, location, image_size, angle)},
    ]
    problems: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = chat(messages)
        try:
            content = normalize(VariantContent.model_validate_json(raw), spec)
        except ValidationError as exc:
            problems = [f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()]
        else:
            problems = (
                check_limits(content, spec.limits)
                + check_sections(content, spec)
                + check_style(content, rules)
            )
            if not problems:
                return content
        logger.warning("Attempt %d produced non-conforming output: %s", attempt, problems)
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": "La risposta non rispetta i vincoli:\n- "
                + "\n- ".join(problems)
                + "\nCorreggi e restituisci di nuovo l'intero oggetto JSON.",
            }
        )
    raise LLMOutputError(
        f"Model output did not satisfy constraints after {MAX_ATTEMPTS} attempts: {'; '.join(problems)}"
    )
