import pytest

from llm import (
    CHANNEL_RULES,
    check_limits,
    check_sections,
    check_style,
    get_spec,
    normalize,
)
from channels import UnsupportedCombinationError, resolve_image_size
from models import AdFormat, Channel, ImageSize
from schemas import VariantContent

INDEED_SPEC = get_spec(Channel.INDEED, AdFormat.TEXT_ONLY)
SOCIAL_IMAGE_SPEC = get_spec(Channel.INSTAGRAM, AdFormat.IMAGE_ONLY)


def content(**overrides) -> VariantContent:
    fields = {
        "headline": "Tecnico fotovoltaico",
        "body": "AB Group cerca un tecnico per grandi impianti. Contratto a tempo indeterminato.",
        "bullet_points": ["RAL da 32.000 € a 38.000 €"],
        "call_to_action": "Rispondi a questo messaggio",
    }
    return VariantContent(**{**fields, **overrides})


def test_clean_copy_passes_style_checks() -> None:
    assert check_style(content(), CHANNEL_RULES[Channel.WHATSAPP]) == []


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"body": "Offriamo un contratto stabile a chi cerca crescita."}, "prima persona"),
        ({"body": "Siamo alla ricerca di un tecnico esperto in impianti."}, "prima persona"),
        ({"bullet_points": ["RAL fino a 38k"]}, "cifre abbreviate"),
        ({"bullet_points": ["RAL fino a trentottomila euro"]}, "lettere"),
        ({"bullet_points": ["Impianti oltre cento kW"]}, "lettere"),
        ({"body": "Lavoro stabile e ben pagato. Lavoro stabile e ben pagato."}, "frasi ripetute"),
        ({"body": "Testo del body. call_to_action: candidati"}, "nomi di campi"),
    ],
)
def test_style_checks_catch_known_bad_outputs(overrides: dict, expected: str) -> None:
    problems = check_style(content(**overrides), CHANNEL_RULES[Channel.WHATSAPP])
    assert any(expected in problem for problem in problems), problems


def test_style_checks_avoid_false_positives() -> None:
    copy = content(body="Sede a Milano, impianti da 100 kW e RAL da 32.000 €. Contratto stabile.")
    assert check_style(copy, CHANNEL_RULES[Channel.WHATSAPP]) == []


def test_emoji_and_markdown_limits_depend_on_channel() -> None:
    copy = content(headline="⚡ Tecnico *fotovoltaico*")
    assert check_style(copy, CHANNEL_RULES[Channel.WHATSAPP]) == []
    indeed_problems = check_style(copy, CHANNEL_RULES[Channel.INDEED])
    assert any("emoji" in problem for problem in indeed_problems)
    assert any("asterischi" in problem for problem in indeed_problems)


def test_length_limits_allow_tolerance() -> None:
    limits = SOCIAL_IMAGE_SPEC.limits
    base = {"body": "Lavora a Brescia", "highlights": ["Entra in una multinazionale", "Impianti FV"], "visual_brief": "Tecnico in cantiere."}
    assert check_limits(content(headline="x" * 40, **base), limits) == []
    assert any("headline" in problem for problem in check_limits(content(headline="x" * 41, **base), limits))


def test_image_formats_require_highlights_and_visual_brief() -> None:
    problems = check_limits(content(body="Lavora a Brescia"), SOCIAL_IMAGE_SPEC.limits)
    assert any("highlights" in problem for problem in problems)
    assert any("visual_brief" in problem for problem in problems)


def test_normalize_clears_image_fields_on_text_formats() -> None:
    copy = content(highlights=["chip"], visual_brief="foto")
    normalized = normalize(copy, get_spec(Channel.WHATSAPP, AdFormat.TEXT_ONLY))
    assert normalized.highlights == []
    assert normalized.visual_brief is None


def test_normalize_lays_out_indeed_sections_and_drops_requirements() -> None:
    raw_body = (
        "AB Group cerca un tecnico. Il ruolo: - Collaudi. - Manutenzione. "
        "Cosa ti offre l'azienda: - Contratto stabile. - RAL 32.000 €. Il tuo profilo: Diploma tecnico."
    )
    normalized = normalize(content(body=raw_body), INDEED_SPEC)
    assert normalized.body == (
        "AB Group cerca un tecnico.\n\n"
        "Il ruolo:\n- Collaudi.\n- Manutenzione.\n\n"
        "Cosa ti offre l'azienda:\n- Contratto stabile.\n- RAL 32.000 €."
    )
    assert check_sections(normalized, INDEED_SPEC) == []


def test_missing_indeed_sections_are_reported() -> None:
    problems = check_sections(content(body="Solo un paragrafo senza sezioni."), INDEED_SPEC)
    assert problems and "sezioni" in problems[0]


def test_unsupported_combination_raises() -> None:
    with pytest.raises(UnsupportedCombinationError):
        get_spec(Channel.INDEED, AdFormat.IMAGE_ONLY)


def test_image_size_defaults_to_the_first_one_the_channel_allows() -> None:
    assert resolve_image_size(Channel.TIKTOK, AdFormat.IMAGE_ONLY, None) == ImageSize.VERTICAL
    assert resolve_image_size(Channel.WHATSAPP, AdFormat.TEXT_AND_IMAGE, None) == ImageSize.A4
    assert resolve_image_size(Channel.WHATSAPP, AdFormat.TEXT_ONLY, None) is None


@pytest.mark.parametrize(
    ("channel", "ad_format", "image_size"),
    [
        (Channel.TIKTOK, AdFormat.IMAGE_ONLY, ImageSize.SQUARE),
        (Channel.WHATSAPP, AdFormat.IMAGE_ONLY, ImageSize.VERTICAL),
        (Channel.WHATSAPP, AdFormat.TEXT_ONLY, ImageSize.A4),
    ],
)
def test_unsupported_image_size_raises(channel: Channel, ad_format: AdFormat, image_size: ImageSize) -> None:
    with pytest.raises(UnsupportedCombinationError):
        resolve_image_size(channel, ad_format, image_size)
