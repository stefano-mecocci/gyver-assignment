from datetime import datetime
from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, model_validator

from models import AREA_FIELDS, AdFormat, Channel, ImageSize, JobOffer, VariantStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class VariantContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str = Field(min_length=1, max_length=255)
    highlights: list[str] = Field(
        default_factory=list,
        description="Short chips printed on the creative (salary, contract, location). Empty for text-only formats.",
    )
    body: str = Field(min_length=1)
    bullet_points: list[str] = Field(default_factory=list)
    call_to_action: str = Field(min_length=1, max_length=255)
    visual_brief: str | None = Field(
        default=None,
        description="Art direction for the image: subject, setting, mood. Null for text-only formats.",
    )


class JobOfferLocation(BaseModel):
    street_name: str | None = Field(default=None, max_length=255)
    street_number: str | None = Field(default=None, max_length=16)
    postal_code: str | None = Field(default=None, max_length=16)
    locality: str = Field(min_length=1, max_length=128)
    province: str | None = Field(default=None, max_length=128)
    province_code: str | None = Field(default=None, max_length=8)
    region: str | None = Field(default=None, max_length=128)
    country_code: str = Field(min_length=2, max_length=2)


class TargetLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locality: str | None = Field(default=None, min_length=1, max_length=128)
    province: str | None = Field(default=None, min_length=1, max_length=128)
    province_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, min_length=1, max_length=128)
    country_code: str = Field(default="IT", pattern=r"^[A-Z]{2}$")

    @model_validator(mode="after")
    def _require_an_area(self) -> Self:
        if not any(getattr(self, field) for field in AREA_FIELDS if field != "country_code"):
            raise ValueError("Give at least one of locality, province, province_code or region")
        return self


class VariantLocation(BaseModel):
    locality: str | None
    province: str | None
    province_code: str | None
    region: str | None
    country_code: str
    label: str


class ImageSizeInfo(BaseModel):
    id: ImageSize
    width: int
    height: int


class ChannelInfo(BaseModel):
    formats: list[AdFormat]
    image_sizes: list[ImageSizeInfo]
    locality_required: bool


class JobOfferBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    company_name: str = Field(min_length=1, max_length=255)
    status: str = Field(default="active", min_length=1, max_length=32)
    location: JobOfferLocation

    contract_type: str | None = Field(default=None, max_length=64)
    min_exp_years: int | None = Field(default=None, ge=0)
    max_exp_years: int | None = Field(default=None, ge=0)
    ral_min: int | None = Field(default=None, ge=0)
    ral_max: int | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    required_skills: list[str] = Field(default_factory=list)

    role_description: str | None = None
    location_and_hours: str | None = None
    company_description: str | None = None
    requirements_description: str | None = None
    compensation_package: str | None = None

    @model_validator(mode="after")
    def _check_ranges(self) -> Self:
        if self.ral_min is not None and self.ral_max is not None and self.ral_min > self.ral_max:
            raise ValueError("ral_min must be less than or equal to ral_max")
        if (
            self.min_exp_years is not None
            and self.max_exp_years is not None
            and self.min_exp_years > self.max_exp_years
        ):
            raise ValueError("min_exp_years must be less than or equal to max_exp_years")
        return self


class JobOfferCreate(JobOfferBase):
    job_offer_id: str = Field(min_length=1, max_length=64)
    created_at: datetime | None = None

    def to_model(self) -> JobOffer:
        data = self.model_dump(exclude={"job_offer_id", "location", "created_at"})
        if self.created_at is not None:
            data["created_at"] = self.created_at
        return JobOffer(id=self.job_offer_id, **data, **self.location.model_dump())


class JobOfferRead(JobOfferBase, ORMModel):
    job_offer_id: str = Field(validation_alias=AliasChoices("job_offer_id", "id"))
    created_at: datetime
    updated_at: datetime


class VariantContentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str | None = Field(default=None, min_length=1, max_length=255)
    highlights: list[str] | None = None
    body: str | None = Field(default=None, min_length=1)
    bullet_points: list[str] | None = None
    call_to_action: str | None = Field(default=None, min_length=1, max_length=255)
    visual_brief: str | None = Field(default=None, min_length=1)


class AdVariantUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edited_content: VariantContentPatch | None = None
    status: VariantStatus | None = None


class PublishingFields(BaseModel):
    address: str
    required_skills: list[str]
    experience: str | None


class AdVariantRead(ORMModel):
    id: int
    ad_id: int
    job_offer_id: str
    channel: Channel
    format: AdFormat
    image_size: ImageSize | None
    image_url: str | None
    image_provider: str | None
    status: VariantStatus
    location: VariantLocation
    angle: str | None
    publishing_fields: PublishingFields | None
    generated_content: VariantContent
    edited_content: VariantContent | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def effective_content(self) -> VariantContent:
        return self.edited_content or self.generated_content


class VariantGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Channel
    format: AdFormat
    image_size: ImageSize | None = None
    target_location: TargetLocation | None = None
    angle: str | None = Field(default=None, min_length=1, max_length=255)
