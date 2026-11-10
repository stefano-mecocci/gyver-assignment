from dataclasses import dataclass

from models import AdFormat, Channel, ImageSize


class UnsupportedCombinationError(ValueError):
    pass


@dataclass(frozen=True)
class ImageSpec:
    width: int
    height: int
    prompt: str


IMAGE_SPECS: dict[ImageSize, ImageSpec] = {
    ImageSize.SQUARE: ImageSpec(
        width=1080,
        height=1080,
        prompt="quadrata 1:1, 1080x1080 px, per il feed",
    ),
    ImageSize.PORTRAIT: ImageSpec(
        width=1080,
        height=1350,
        prompt="verticale 4:5, 1080x1350 px, per il feed: c'è spazio per una riga in più sotto il titolo",
    ),
    ImageSize.VERTICAL: ImageSpec(
        width=1080,
        height=1920,
        prompt=(
            "verticale 9:16, 1080x1920 px, a schermo intero (storie, reel, TikTok): titolo e testi nella parte "
            "centrale, lontani dal 15% superiore e inferiore coperto dall'interfaccia dell'app"
        ),
    ),
    ImageSize.A4: ImageSpec(
        width=1240,
        height=1754,
        prompt="volantino A4 verticale, 1240x1754 px: l'unico formato che si vede intero anche nell'anteprima della chat",
    ),
}

CHANNEL_IMAGE_SIZES: dict[Channel, tuple[ImageSize, ...]] = {
    Channel.INDEED: (),
    Channel.INSTAGRAM: (ImageSize.SQUARE, ImageSize.PORTRAIT, ImageSize.VERTICAL),
    Channel.TIKTOK: (ImageSize.VERTICAL,),
    Channel.WHATSAPP: (ImageSize.A4,),
}

GENERATION_LONG_SIDE = 1024
GENERATION_MULTIPLE = 16

CHANNELS_REQUIRING_LOCALITY: frozenset[Channel] = frozenset({Channel.INDEED})


def resolve_image_size(channel: Channel, ad_format: AdFormat, requested: ImageSize | None) -> ImageSize | None:
    if ad_format == AdFormat.TEXT_ONLY:
        if requested is not None:
            raise UnsupportedCombinationError("Format 'text_only' has no image: omit image_size")
        return None
    allowed = CHANNEL_IMAGE_SIZES[channel]
    if requested is None:
        return allowed[0]
    if requested not in allowed:
        names = ", ".join(size.value for size in allowed)
        raise UnsupportedCombinationError(
            f"Image size '{requested.value}' is not supported for channel '{channel.value}'. Allowed: {names}"
        )
    return requested


def generation_dimensions(image_size: ImageSize) -> tuple[int, int]:
    spec = IMAGE_SPECS[image_size]
    scale = GENERATION_LONG_SIDE / max(spec.width, spec.height)
    return (
        round(spec.width * scale / GENERATION_MULTIPLE) * GENERATION_MULTIPLE,
        round(spec.height * scale / GENERATION_MULTIPLE) * GENERATION_MULTIPLE,
    )


def check_location(channel: Channel, locality: str | None) -> None:
    if channel in CHANNELS_REQUIRING_LOCALITY and not locality:
        raise UnsupportedCombinationError(
            f"Channel '{channel.value}' needs a locality: job boards publish each ad for a specific town"
        )
