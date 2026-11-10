from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from channels import (
    CHANNEL_IMAGE_SIZES,
    CHANNELS_REQUIRING_LOCALITY,
    IMAGE_SPECS,
    UnsupportedCombinationError,
    check_location,
    generation_dimensions,
    resolve_image_size,
)
from database import get_db
from images import IMAGES_DIR, ImageGenerationError, creative_prompt, generator
from llm import (
    LLMOutputError,
    LLMUnavailableError,
    generate_variant_content,
    get_spec,
    supported_formats,
)
from models import Ad, AdFormat, AdVariant, Channel, JobOffer, VariantStatus, location_label
from schemas import (
    AdVariantRead,
    AdVariantUpdate,
    ChannelInfo,
    ImageSizeInfo,
    JobOfferCreate,
    JobOfferRead,
    VariantContent,
    VariantGenerateRequest,
)

router = APIRouter()

DbSession = Annotated[Session, Depends(get_db)]

FRONTEND_INDEX = Path(__file__).resolve().parent / "frontend" / "index.html"


@router.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_INDEX)


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/channels")
def list_channels() -> dict[Channel, ChannelInfo]:
    return {
        channel: ChannelInfo(
            formats=supported_formats(channel),
            image_sizes=[
                ImageSizeInfo(id=size, width=IMAGE_SPECS[size].width, height=IMAGE_SPECS[size].height)
                for size in CHANNEL_IMAGE_SIZES[channel]
            ],
            locality_required=channel in CHANNELS_REQUIRING_LOCALITY,
        )
        for channel in Channel
    }


@router.get("/job-offers", response_model=list[JobOfferRead])
def list_job_offers(db: DbSession) -> list[JobOffer]:
    return list(db.scalars(select(JobOffer).order_by(JobOffer.created_at.desc())))


@router.post(
    "/job-offers", response_model=JobOfferRead, status_code=status.HTTP_201_CREATED
)
def create_job_offer(payload: JobOfferCreate, db: DbSession) -> JobOffer:
    if db.get(JobOffer, payload.job_offer_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job offer '{payload.job_offer_id}' already exists",
        )
    job_offer = payload.to_model()
    db.add(job_offer)
    db.commit()
    db.refresh(job_offer)
    return job_offer


def get_job_offer_or_404(db: Session, job_offer_id: str) -> JobOffer:
    job_offer = db.get(JobOffer, job_offer_id)
    if job_offer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job offer '{job_offer_id}' not found",
        )
    return job_offer


def get_or_create_ad(db: Session, job_offer_id: str) -> Ad:
    ad = db.scalars(
        select(Ad).where(Ad.job_offer_id == job_offer_id).order_by(Ad.id).limit(1)
    ).first()
    if ad is None:
        ad = Ad(job_offer_id=job_offer_id)
        db.add(ad)
        db.flush()
    return ad


@router.get("/job-offers/{job_offer_id}", response_model=JobOfferRead)
def get_job_offer(job_offer_id: str, db: DbSession) -> JobOffer:
    return get_job_offer_or_404(db, job_offer_id)


@router.post(
    "/job-offers/{job_offer_id}/variants",
    response_model=AdVariantRead,
    status_code=status.HTTP_201_CREATED,
)
def create_variant(
    job_offer_id: str, payload: VariantGenerateRequest, db: DbSession
) -> AdVariant:
    job_offer = get_job_offer_or_404(db, job_offer_id)
    area = (
        payload.target_location.model_dump()
        if payload.target_location is not None
        else job_offer.area()
    )
    try:
        get_spec(payload.channel, payload.format)
        image_size = resolve_image_size(payload.channel, payload.format, payload.image_size)
        check_location(payload.channel, area["locality"])
    except UnsupportedCombinationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    try:
        content = generate_variant_content(
            job_offer,
            payload.channel,
            payload.format,
            location_label(area["locality"], area["province"], area["province_code"], area["region"]),
            image_size,
            payload.angle,
        )
    except LLMUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except LLMOutputError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    ad = get_or_create_ad(db, job_offer.id)
    variant = AdVariant(
        ad=ad,
        channel=payload.channel,
        format=payload.format,
        image_size=image_size,
        status=VariantStatus.DRAFT,
        **area,
        angle=payload.angle,
        generated_content=content.model_dump(mode="json"),
        edited_content=None,
    )
    db.add(variant)
    db.commit()
    db.refresh(variant)
    return variant


@router.get("/variants", response_model=list[AdVariantRead])
def list_variants(
    db: DbSession,
    job_offer_id: Annotated[str | None, Query()] = None,
    channel: Annotated[Channel | None, Query()] = None,
    variant_status: Annotated[VariantStatus | None, Query(alias="status")] = None,
    locality: Annotated[str | None, Query(min_length=1)] = None,
    province_code: Annotated[str | None, Query(min_length=1)] = None,
    region: Annotated[str | None, Query(min_length=1)] = None,
) -> list[AdVariant]:
    query = select(AdVariant)
    if job_offer_id is not None:
        query = query.join(Ad, AdVariant.ad_id == Ad.id).where(
            Ad.job_offer_id == job_offer_id
        )
    if channel is not None:
        query = query.where(AdVariant.channel == channel)
    if variant_status is not None:
        query = query.where(AdVariant.status == variant_status)
    for column, value in (
        (AdVariant.locality, locality),
        (AdVariant.province_code, province_code),
        (AdVariant.region, region),
    ):
        if value is not None:
            query = query.where(func.lower(column) == value.lower())
    return list(
        db.scalars(query.order_by(AdVariant.created_at.desc(), AdVariant.id.desc()))
    )


def get_variant_or_404(db: Session, variant_id: int) -> AdVariant:
    variant = db.get(AdVariant, variant_id)
    if variant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Variant {variant_id} not found",
        )
    return variant


@router.get("/variants/{variant_id}", response_model=AdVariantRead)
def get_variant(variant_id: int, db: DbSession) -> AdVariant:
    return get_variant_or_404(db, variant_id)


@router.patch("/variants/{variant_id}", response_model=AdVariantRead)
def update_variant(
    variant_id: int, payload: AdVariantUpdate, db: DbSession
) -> AdVariant:
    variant = get_variant_or_404(db, variant_id)

    if "edited_content" in payload.model_fields_set:
        if payload.edited_content is None:
            variant.edited_content = None
        else:
            base = variant.edited_content or variant.generated_content
            changes = payload.edited_content.model_dump(
                exclude_unset=True, exclude_none=True
            )
            try:
                merged = VariantContent.model_validate({**base, **changes})
            except ValidationError as exc:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=exc.errors(include_url=False, include_context=False),
                ) from exc
            variant.edited_content = merged.model_dump(mode="json")

    if payload.status is not None:
        variant.status = payload.status

    db.commit()
    db.refresh(variant)
    return variant


@router.post("/variants/{variant_id}/image", response_model=AdVariantRead)
def generate_variant_image(variant_id: int, db: DbSession) -> AdVariant:
    variant = get_variant_or_404(db, variant_id)
    if variant.format == AdFormat.TEXT_ONLY or variant.image_size is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Variant {variant_id} is text only: it has no image to generate",
        )
    visual_brief = (variant.edited_content or variant.generated_content).get("visual_brief")
    if not visual_brief:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Variant {variant_id} has no visual_brief to generate the image from",
        )

    width, height = generation_dimensions(variant.image_size)
    try:
        image = generator.generate(creative_prompt(visual_brief), width, height)
    except ImageGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    file_name = f"variant_{variant.id}.png"
    image.save(IMAGES_DIR / file_name)
    variant.image_file = file_name
    variant.image_provider = image.provider
    db.commit()
    db.refresh(variant)
    return variant


@router.get("/variants/{variant_id}/image", response_class=FileResponse)
def get_variant_image(variant_id: int, db: DbSession) -> FileResponse:
    variant = get_variant_or_404(db, variant_id)
    path = IMAGES_DIR / variant.image_file if variant.image_file else None
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Variant {variant_id} has no generated image",
        )
    return FileResponse(path, media_type="image/png")
