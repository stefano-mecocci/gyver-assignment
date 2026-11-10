import json
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from channels import check_location, resolve_image_size
from database import SessionLocal, init_db
from models import Ad, AdFormat, AdVariant, Channel, ImageSize, JobOffer, VariantStatus
from schemas import JobOfferCreate, TargetLocation, VariantContent

DATA_DIR = Path(__file__).resolve().parent / "data"
JOB_OFFERS_FILE = DATA_DIR / "sample_job_offer.json"
VARIANTS_FILE = DATA_DIR / "sample_variants.json"


class VariantFixture(BaseModel):
    job_offer_id: str
    channel: Channel
    format: AdFormat
    status: VariantStatus = VariantStatus.DRAFT
    image_size: ImageSize | None = None
    target_location: TargetLocation | None = None
    angle: str | None = None
    generated_content: VariantContent
    edited_content: VariantContent | None = None


def read_items(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, list) else [raw]


def seed_job_offers(db: Session, path: Path) -> None:
    for offer in (JobOfferCreate.model_validate(item) for item in read_items(path)):
        if db.get(JobOffer, offer.job_offer_id) is not None:
            print(f"Skipped job offer {offer.job_offer_id}: already present")
            continue
        db.add(offer.to_model())
        db.commit()
        print(f"Inserted job offer {offer.job_offer_id}")


def seed_variants(db: Session, path: Path) -> None:
    fixtures = [VariantFixture.model_validate(item) for item in read_items(path)]
    for job_offer_id in sorted({fixture.job_offer_id for fixture in fixtures}):
        has_ads = db.scalars(select(Ad.id).where(Ad.job_offer_id == job_offer_id)).first() is not None
        if has_ads:
            print(f"Skipped variants for {job_offer_id}: ads already present")
            continue
        job_offer = db.get(JobOffer, job_offer_id)
        if job_offer is None:
            print(f"Skipped variants for {job_offer_id}: job offer not found")
            continue
        ad = Ad(job_offer_id=job_offer_id)
        for fixture in (f for f in fixtures if f.job_offer_id == job_offer_id):
            area = fixture.target_location.model_dump() if fixture.target_location else job_offer.area()
            check_location(fixture.channel, area["locality"])
            ad.variants.append(
                AdVariant(
                    channel=fixture.channel,
                    format=fixture.format,
                    image_size=resolve_image_size(fixture.channel, fixture.format, fixture.image_size),
                    status=fixture.status,
                    **area,
                    angle=fixture.angle,
                    generated_content=fixture.generated_content.model_dump(mode="json"),
                    edited_content=fixture.edited_content.model_dump(mode="json") if fixture.edited_content else None,
                )
            )
        db.add(ad)
        db.commit()
        print(f"Inserted {len(ad.variants)} variants for {job_offer_id}")


def seed() -> None:
    init_db()
    with SessionLocal() as db:
        seed_job_offers(db, JOB_OFFERS_FILE)
        seed_variants(db, VARIANTS_FILE)


if __name__ == "__main__":
    seed()
