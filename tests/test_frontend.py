import re

import pytest
from playwright.sync_api import Page, expect

SEEDED_VARIANTS = 7


def open_app(page: Page, server: str) -> None:
    page.goto(server)
    expect(page.locator("#variants tr")).to_have_count(SEEDED_VARIANTS)


def row_for(page: Page, variant_id: int):
    return page.locator("#variants tr").filter(has=page.locator("td").first.filter(has_text=re.compile(rf"^{variant_id}$")))


def test_lists_seeded_ads_for_the_job_offer(page: Page, server: str) -> None:
    open_app(page, server)
    expect(page).to_have_title("Gyver Annunci")
    expect(page.locator("#jobOffer")).to_contain_text("jo_001")
    expect(page.locator("#jobOfferInfo")).to_contain_text("Orzinuovi")
    expect(page.locator("#generateStatus")).to_have_text("")


def test_format_options_follow_the_channel(page: Page, server: str) -> None:
    open_app(page, server)
    new_format_options = page.locator("#newFormat option")
    page.select_option("#newChannel", "indeed")
    expect(new_format_options).to_have_text(["text_only"])
    expect(page.locator("#newImageSizeField")).to_be_hidden()
    page.select_option("#newChannel", "instagram")
    expect(new_format_options).to_have_text(["image_only", "text_and_image"])
    expect(page.locator("#newImageSizeField")).to_be_visible()
    expect(page.locator("#newImageSize option")).to_have_text(
        ["square (1080x1080)", "portrait (1080x1350)", "vertical (1080x1920)"]
    )
    page.select_option("#newChannel", "whatsapp")
    expect(new_format_options).to_have_text(["text_only", "image_only", "text_and_image"])
    expect(page.locator("#newImageSizeField")).to_be_hidden()
    page.select_option("#newFormat", "image_only")
    expect(page.locator("#newImageSize option")).to_have_text(["a4 (1240x1754)"])


def test_filters_by_channel_and_status(page: Page, server: str) -> None:
    open_app(page, server)
    page.select_option("#filterChannel", "instagram")
    expect(page.locator("#variants tr")).to_have_count(2)
    expect(page.locator("#variants tr td:nth-child(2)")).to_have_text(["instagram"] * 2)
    page.select_option("#filterChannel", "")
    page.select_option("#filterStatus", "ready")
    expect(page.locator("#variants tr")).to_have_count(1)
    expect(page.locator("#variants tr td:nth-child(4)")).to_have_text("ready")


def test_filters_by_location(page: Page, server: str) -> None:
    open_app(page, server)
    page.fill("#filterProvince", "BS")
    page.press("#filterProvince", "Enter")
    page.locator("#filterProvince").dispatch_event("change")
    expect(page.locator("#variants tr")).to_have_count(5)
    expect(page.locator("#variants tr td:nth-child(5)")).to_have_text([re.compile(r"\(BS\)$")] * 5)


def test_indeed_editor_shows_job_board_fields(page: Page, server: str) -> None:
    open_app(page, server)
    row_for(page, 1).click()
    expect(page.locator("#editorTitle")).to_contain_text("indeed")
    expect(page.locator("#publishing")).to_contain_text("Via Artigianato, 27, 25034 Orzinuovi BS")
    expect(page.locator("#publishing")).to_contain_text("3-5 anni")
    expect(page.locator("#imageFields")).to_be_hidden()
    expect(page.locator("#visualField")).to_be_hidden()
    expect(page.locator("#imageSection")).to_be_hidden()


def test_image_format_editor_shows_image_fields(page: Page, server: str) -> None:
    open_app(page, server)
    row_for(page, 2).click()
    expect(page.locator("#editorTitle")).to_contain_text("image_only · square")
    expect(page.locator("#imageFields")).to_be_visible()
    expect(page.locator("#visualField")).to_be_visible()
    expect(page.locator("#publishing")).to_be_hidden()
    expect(page.locator("#generated")).to_contain_text("VISUAL BRIEF")
    expect(page.locator("#imageSection")).to_be_visible()
    expect(page.locator("#imageStatus")).to_have_text("Nessuna immagine generata.")


def test_edit_content_then_restore_generated(page: Page, server: str) -> None:
    open_app(page, server)
    row = row_for(page, 2)
    original_headline = row.locator("td").nth(6).inner_text()
    row.click()
    page.fill("#editHeadline", "Tecnico FV: entra in AB Group")
    page.select_option("#editStatus", "ready")
    page.click("#save")
    expect(page.locator("#saveStatus")).to_have_text("Salvato.")
    expect(row.locator("td")).to_have_text(
        [re.compile(".*")] * 3 + ["ready", re.compile(".*"), re.compile(".*"), "Tecnico FV: entra in AB Group", "sì"]
    )
    expect(page.locator("#generated")).to_contain_text(original_headline)

    page.click("#reset")
    expect(page.locator("#saveStatus")).to_have_text("Ripristinato il contenuto generato.")
    expect(row.locator("td").nth(6)).to_have_text(original_headline)
    expect(row.locator("td").nth(7)).to_have_text("")


def test_saving_without_changes_sends_nothing(page: Page, server: str) -> None:
    open_app(page, server)
    row_for(page, 3).click()
    page.click("#save")
    expect(page.locator("#saveStatus")).to_have_text("Nessuna modifica da salvare.")


def test_invalid_edit_shows_the_api_error(page: Page, server: str) -> None:
    open_app(page, server)
    row_for(page, 3).click()
    page.fill("#editHeadline", "")
    page.click("#save")
    expect(page.locator("#saveStatus")).to_have_class("error")
    expect(page.locator("#saveStatus")).to_contain_text("422")


def test_image_generation_error_is_shown(page: Page, server: str) -> None:
    open_app(page, server)
    row_for(page, 2).click()
    page.click("#generateImage")
    expect(page.locator("#imageStatus")).to_contain_text("503")
    expect(page.locator("#imageStatus")).to_have_class("error")
    expect(page.locator("#generateImage")).to_be_enabled()


@pytest.mark.parametrize("server_env", [{"OLLAMA_HOST": "http://127.0.0.1:9"}])
def test_generation_error_is_shown_when_llm_is_unreachable(page: Page, server: str) -> None:
    open_app(page, server)
    page.select_option("#newChannel", "whatsapp")
    page.select_option("#newFormat", "text_only")
    page.click("#generate")
    expect(page.locator("#generateStatus")).to_contain_text("503")
    expect(page.locator("#generate")).to_be_enabled()
    expect(page.locator("#variants tr")).to_have_count(SEEDED_VARIANTS)


@pytest.mark.llm
def test_generate_ad_with_the_llm(page: Page, server: str) -> None:
    open_app(page, server)
    page.select_option("#newChannel", "whatsapp")
    page.select_option("#newFormat", "text_only")
    page.fill("#newLocality", "Brescia")
    page.fill("#newProvinceCode", "BS")
    page.fill("#newAngle", "stabilità del contratto")
    page.click("#generate")
    expect(page.locator("#generate")).to_be_disabled()
    expect(page.locator("#generateStatus")).to_contain_text("Creato", timeout=400_000)
    expect(page.locator("#variants tr")).to_have_count(SEEDED_VARIANTS + 1)
    expect(page.locator("#editorTitle")).to_contain_text("whatsapp")
    expect(page.locator("#editorMeta")).to_contain_text("Brescia")
