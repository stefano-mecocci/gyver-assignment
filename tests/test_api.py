import sqlite3
from pathlib import Path

import httpx
import pytest


def unedited_variant_ids(database: Path) -> set[int]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT id FROM ad_variants WHERE edited_content IS NULL")
        return {row[0] for row in rows}


def test_create_job_offer_from_the_assignment_json(api: httpx.Client, sample_job_offer: dict) -> None:
    payload = {**sample_job_offer, "job_offer_id": "jo_test"}
    created = api.post("/job-offers", json=payload)
    assert created.status_code == 201
    assert created.json()["location"] == sample_job_offer["location"]
    assert api.get("/job-offers/jo_test").json()["required_skills"] == sample_job_offer["required_skills"]
    assert api.post("/job-offers", json=payload).status_code == 409


def test_invalid_job_offer_is_rejected(api: httpx.Client, sample_job_offer: dict) -> None:
    payload = {**sample_job_offer, "job_offer_id": "jo_bad", "ral_min": 50000, "ral_max": 30000}
    assert api.post("/job-offers", json=payload).status_code == 422


def test_unknown_resources_return_404(api: httpx.Client) -> None:
    assert api.get("/job-offers/missing").status_code == 404
    assert api.get("/variants/9999").status_code == 404
    assert api.patch("/variants/9999", json={"status": "ready"}).status_code == 404
    assert api.post("/job-offers/missing/variants", json={"channel": "indeed", "format": "text_only"}).status_code == 404


def test_channels_list_formats_image_sizes_and_location_rules(api: httpx.Client) -> None:
    channels = api.get("/channels").json()
    assert {channel: info["formats"] for channel, info in channels.items()} == {
        "indeed": ["text_only"],
        "instagram": ["image_only", "text_and_image"],
        "tiktok": ["image_only", "text_and_image"],
        "whatsapp": ["text_only", "image_only", "text_and_image"],
    }
    assert [size["id"] for size in channels["instagram"]["image_sizes"]] == ["square", "portrait", "vertical"]
    assert channels["tiktok"]["image_sizes"] == [{"id": "vertical", "width": 1080, "height": 1920}]
    assert [size["id"] for size in channels["whatsapp"]["image_sizes"]] == ["a4"]
    assert channels["indeed"]["image_sizes"] == []
    assert channels["indeed"]["locality_required"] is True
    assert channels["whatsapp"]["locality_required"] is False


def test_unsupported_channel_format_pair_is_rejected(api: httpx.Client) -> None:
    response = api.post("/job-offers/jo_001/variants", json={"channel": "indeed", "format": "image_only"})
    assert response.status_code == 422
    assert "Allowed: text_only" in response.json()["detail"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"channel": "tiktok", "format": "image_only", "image_size": "square"}, "Allowed: vertical"),
        ({"channel": "whatsapp", "format": "text_only", "image_size": "a4"}, "omit image_size"),
        ({"channel": "indeed", "format": "text_only", "target_location": {"region": "Lombardia"}}, "needs a locality"),
    ],
)
def test_generation_rejects_invalid_image_size_or_location(api: httpx.Client, payload: dict, message: str) -> None:
    response = api.post("/job-offers/jo_001/variants", json=payload)
    assert response.status_code == 422
    assert message in response.json()["detail"]


@pytest.mark.parametrize(
    "target_location",
    [{}, {"country_code": "IT"}, {"province_code": "Brescia"}, {"locality": "Brescia", "city": "x"}],
)
def test_malformed_target_location_is_rejected(api: httpx.Client, target_location: dict) -> None:
    payload = {"channel": "whatsapp", "format": "text_only", "target_location": target_location}
    assert api.post("/job-offers/jo_001/variants", json=payload).status_code == 422


@pytest.mark.parametrize(
    ("params", "expected_count"),
    [
        ({}, 7),
        ({"job_offer_id": "jo_001"}, 7),
        ({"job_offer_id": "missing"}, 0),
        ({"channel": "instagram"}, 2),
        ({"channel": "tiktok"}, 1),
        ({"job_offer_id": "jo_001", "channel": "whatsapp"}, 3),
        ({"status": "published"}, 1),
    ],
)
def test_list_variants_filters(api: httpx.Client, params: dict, expected_count: int) -> None:
    variants = api.get("/variants", params=params).json()
    assert len(variants) == expected_count
    for key, value in params.items():
        assert all(variant[key] == value for variant in variants)


@pytest.mark.parametrize(
    ("params", "expected_localities"),
    [
        ({"region": "lombardia"}, ["Bergamo", "Brescia", "Cremona", "Orzinuovi"]),
        ({"province_code": "BS"}, ["Brescia", "Orzinuovi"]),
        ({"locality": "Cremona"}, ["Cremona"]),
        ({"region": "Piemonte"}, []),
    ],
)
def test_list_variants_filters_by_location(api: httpx.Client, params: dict, expected_localities: list[str]) -> None:
    variants = api.get("/variants", params=params).json()
    assert sorted({variant["location"]["locality"] for variant in variants}) == expected_localities


def test_variant_exposes_location_image_size_and_publishing_fields(api: httpx.Client) -> None:
    variants = {variant["id"]: variant for variant in api.get("/variants").json()}
    indeed = variants[1]
    assert indeed["location"] == {
        "locality": "Orzinuovi",
        "province": "Brescia",
        "province_code": "BS",
        "region": "Lombardia",
        "country_code": "IT",
        "label": "Orzinuovi (BS)",
    }
    assert indeed["image_size"] is None
    assert indeed["publishing_fields"] == {
        "address": "Via Artigianato, 27, 25034 Orzinuovi BS",
        "required_skills": ["Fotovoltaico industriale", "Cabine secondarie - MT/BT"],
        "experience": "3-5 anni",
    }
    tiktok = variants[3]
    assert tiktok["channel"] == "tiktok"
    assert tiktok["image_size"] == "vertical"
    assert tiktok["location"]["label"] == "Brescia (BS)"
    assert tiktok["publishing_fields"] is None


def test_patch_merges_edits_and_keeps_generated_content(api: httpx.Client) -> None:
    before = api.get("/variants/2").json()
    first = api.patch("/variants/2", json={"edited_content": {"headline": "Nuovo titolo"}, "status": "ready"}).json()
    second = api.patch("/variants/2", json={"edited_content": {"call_to_action": "Candidati"}}).json()
    assert first["status"] == "ready"
    assert second["edited_content"]["headline"] == "Nuovo titolo"
    assert second["edited_content"]["call_to_action"] == "Candidati"
    assert second["edited_content"]["body"] == before["generated_content"]["body"]
    assert second["generated_content"] == before["generated_content"]
    assert second["effective_content"] == second["edited_content"]

    restored = api.patch("/variants/2", json={"edited_content": None}).json()
    assert restored["edited_content"] is None
    assert restored["effective_content"] == before["generated_content"]


def test_missing_edits_are_stored_as_sql_null(api: httpx.Client, database: Path) -> None:
    assert unedited_variant_ids(database) == {1, 2, 3, 4, 6, 7}
    api.patch("/variants/2", json={"edited_content": {"headline": "Nuovo titolo"}})
    assert 2 not in unedited_variant_ids(database)
    api.patch("/variants/2", json={"edited_content": None})
    assert 2 in unedited_variant_ids(database)


@pytest.mark.parametrize(
    "payload",
    [
        {"edited_content": {"headline": ""}},
        {"edited_content": {"unknown_field": "x"}},
        {"status": "archived"},
    ],
)
def test_invalid_patch_is_rejected(api: httpx.Client, payload: dict) -> None:
    assert api.patch("/variants/2", json=payload).status_code == 422


@pytest.mark.parametrize("server_env", [{"OLLAMA_HOST": "http://127.0.0.1:9"}])
def test_generation_returns_503_when_llm_is_unreachable(api: httpx.Client) -> None:
    response = api.post("/job-offers/jo_001/variants", json={"channel": "whatsapp", "format": "text_only"})
    assert response.status_code == 503
    assert len(api.get("/variants").json()) == 7


def test_image_generation_rejects_text_only_and_unknown_variants(api: httpx.Client) -> None:
    assert api.post("/variants/1/image").status_code == 422
    assert api.post("/variants/9999/image").status_code == 404
    assert api.get("/variants/2/image").status_code == 404
    assert api.get("/variants/2").json()["image_url"] is None


def test_image_generation_returns_503_when_every_provider_fails(api: httpx.Client) -> None:
    response = api.post("/variants/2/image")
    assert response.status_code == 503
    assert "huggingface: HF_TOKEN is not set" in response.json()["detail"]
    assert "pollinations: Pollinations unreachable" in response.json()["detail"]
    assert api.get("/variants/2").json()["image_url"] is None


@pytest.mark.llm
@pytest.mark.parametrize(
    ("channel", "ad_format"),
    [("indeed", "text_only"), ("tiktok", "image_only"), ("whatsapp", "text_and_image")],
)
def test_generate_variant_with_the_llm(api: httpx.Client, channel: str, ad_format: str) -> None:
    response = api.post(
        "/job-offers/jo_001/variants",
        json={
            "channel": channel,
            "format": ad_format,
            "target_location": {"locality": "Brescia", "province_code": "BS", "region": "Lombardia"},
        },
    )
    assert response.status_code == 201
    variant = response.json()
    assert variant["status"] == "draft"
    assert variant["location"]["label"] == "Brescia (BS)"
    assert variant["edited_content"] is None
    assert variant["generated_content"]["headline"]
