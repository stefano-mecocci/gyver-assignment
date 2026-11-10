import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Channel(enum.StrEnum):
    INDEED = "indeed"
    INSTAGRAM = "instagram"
    TIKTOK = "tiktok"
    WHATSAPP = "whatsapp"


class AdFormat(enum.StrEnum):
    TEXT_ONLY = "text_only"
    IMAGE_ONLY = "image_only"
    TEXT_AND_IMAGE = "text_and_image"


class ImageSize(enum.StrEnum):
    SQUARE = "square"
    PORTRAIT = "portrait"
    VERTICAL = "vertical"
    A4 = "a4"


class VariantStatus(enum.StrEnum):
    DRAFT = "draft"
    READY = "ready"
    PUBLISHED = "published"


def _str_enum(enum_cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=False,
        validate_strings=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


AREA_FIELDS = ("locality", "province", "province_code", "region", "country_code")


def location_label(
    locality: str | None,
    province: str | None,
    province_code: str | None,
    region: str | None,
) -> str:
    if locality:
        return f"{locality} ({province_code})" if province_code else locality
    if province or province_code:
        return f"Provincia di {province or province_code}"
    return region or ""


LOCATION_FIELDS = (
    "street_name",
    "street_number",
    "postal_code",
    "locality",
    "province",
    "province_code",
    "region",
    "country_code",
)


class JobOffer(TimestampMixin, Base):
    __tablename__ = "job_offers"
    __table_args__ = (
        CheckConstraint(
            "ral_min IS NULL OR ral_max IS NULL OR ral_min <= ral_max",
            name="ck_job_offers_ral_range",
        ),
        CheckConstraint(
            "min_exp_years IS NULL OR max_exp_years IS NULL OR min_exp_years <= max_exp_years",
            name="ck_job_offers_exp_range",
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)

    street_name: Mapped[str | None] = mapped_column(String(255))
    street_number: Mapped[str | None] = mapped_column(String(16))
    postal_code: Mapped[str | None] = mapped_column(String(16))
    locality: Mapped[str] = mapped_column(String(128), nullable=False)
    province: Mapped[str | None] = mapped_column(String(128))
    province_code: Mapped[str | None] = mapped_column(String(8))
    region: Mapped[str | None] = mapped_column(String(128))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)

    contract_type: Mapped[str | None] = mapped_column(String(64))
    min_exp_years: Mapped[int | None] = mapped_column(Integer)
    max_exp_years: Mapped[int | None] = mapped_column(Integer)
    ral_min: Mapped[int | None] = mapped_column(Integer)
    ral_max: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default="EUR"
    )
    required_skills: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )

    role_description: Mapped[str | None] = mapped_column(Text)
    location_and_hours: Mapped[str | None] = mapped_column(Text)
    company_description: Mapped[str | None] = mapped_column(Text)
    requirements_description: Mapped[str | None] = mapped_column(Text)
    compensation_package: Mapped[str | None] = mapped_column(Text)

    ads: Mapped[list["Ad"]] = relationship(
        back_populates="job_offer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def location(self) -> dict[str, str | None]:
        return {field: getattr(self, field) for field in LOCATION_FIELDS}

    def area(self) -> dict[str, str | None]:
        return {field: getattr(self, field) for field in AREA_FIELDS}

    def display_location(self) -> str:
        return location_label(self.locality, self.province, self.province_code, self.region)

    def full_address(self) -> str:
        street = ", ".join(part for part in (self.street_name, self.street_number) if part)
        town = " ".join(part for part in (self.postal_code, self.locality, self.province_code) if part)
        return ", ".join(part for part in (street, town) if part)

    def experience_label(self) -> str | None:
        low, high = self.min_exp_years, self.max_exp_years
        if low is not None and high is not None:
            return f"{low}-{high} anni" if low != high else f"{low} anni"
        if low is not None:
            return f"almeno {low} anni"
        if high is not None:
            return f"fino a {high} anni"
        return None


class Ad(TimestampMixin, Base):
    __tablename__ = "ads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_offer_id: Mapped[str] = mapped_column(
        ForeignKey("job_offers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    job_offer: Mapped[JobOffer] = relationship(back_populates="ads", lazy="joined")
    variants: Mapped[list["AdVariant"]] = relationship(
        back_populates="ad",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class AdVariant(TimestampMixin, Base):
    __tablename__ = "ad_variants"
    __table_args__ = (
        Index("ix_ad_variants_ad_id_channel", "ad_id", "channel"),
        Index("ix_ad_variants_area", "country_code", "region", "province_code", "locality"),
        CheckConstraint(
            "locality IS NOT NULL OR province IS NOT NULL OR province_code IS NOT NULL OR region IS NOT NULL",
            name="ck_ad_variants_location_present",
        ),
        CheckConstraint(
            "(format = 'text_only') = (image_size IS NULL)",
            name="ck_ad_variants_image_size_matches_format",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ad_id: Mapped[int] = mapped_column(
        ForeignKey("ads.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(
        _str_enum(Channel, "channel"), nullable=False
    )
    format: Mapped[AdFormat] = mapped_column(
        _str_enum(AdFormat, "ad_format"), nullable=False
    )
    status: Mapped[VariantStatus] = mapped_column(
        _str_enum(VariantStatus, "variant_status"),
        nullable=False,
        default=VariantStatus.DRAFT,
        server_default=VariantStatus.DRAFT.value,
    )
    image_size: Mapped[ImageSize | None] = mapped_column(_str_enum(ImageSize, "image_size"))
    image_file: Mapped[str | None] = mapped_column(String(255))
    image_provider: Mapped[str | None] = mapped_column(String(32))

    locality: Mapped[str | None] = mapped_column(String(128))
    province: Mapped[str | None] = mapped_column(String(128))
    province_code: Mapped[str | None] = mapped_column(String(8))
    region: Mapped[str | None] = mapped_column(String(128))
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)

    angle: Mapped[str | None] = mapped_column(String(255))
    generated_content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    edited_content: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))

    ad: Mapped[Ad] = relationship(back_populates="variants", lazy="joined")

    @property
    def job_offer_id(self) -> str:
        return self.ad.job_offer_id

    @property
    def image_url(self) -> str | None:
        if self.image_file is None:
            return None
        return f"/variants/{self.id}/image?v={int(self.updated_at.timestamp())}"

    @property
    def location_label(self) -> str:
        return location_label(self.locality, self.province, self.province_code, self.region)

    @property
    def location(self) -> dict[str, str | None]:
        return {**{field: getattr(self, field) for field in AREA_FIELDS}, "label": self.location_label}

    def is_at_job_offer_site(self) -> bool:
        job_offer = self.ad.job_offer
        return (self.locality, self.province_code) == (job_offer.locality, job_offer.province_code)

    @property
    def publishing_fields(self) -> dict[str, Any] | None:
        if self.channel != Channel.INDEED:
            return None
        job_offer = self.ad.job_offer
        return {
            "address": job_offer.full_address() if self.is_at_job_offer_site() else self.location_label,
            "required_skills": list(job_offer.required_skills),
            "experience": job_offer.experience_label(),
        }
