"""Image sanitising shared by avatar and public asset uploads."""

from __future__ import annotations

import io

IMAGE_TYPES = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class ImageRejected(ValueError):
    """The bytes are not an acceptable image; the message is user-facing."""


def sanitize_image(raw: bytes, *, max_pixels: int) -> tuple[bytes, str]:
    """Decode, check, drop metadata and re-encode. Returns (bytes, content type).

    Format and dimensions are checked from the header before any pixel data is
    decoded, so oversized images are rejected without allocating them.
    """

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
        image: Image.Image = Image.open(io.BytesIO(raw))
        if image.format not in IMAGE_TYPES.values():
            raise ImageRejected("Unsupported image type.")
        if image.width > max_pixels or image.height > max_pixels:
            raise ImageRejected(f"Images must be at most {max_pixels} px per side.")
        target_format = image.format
        image.load()
        # Apply the EXIF orientation before the metadata is dropped.
        image = ImageOps.exif_transpose(image)
    except ImageRejected:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageRejected("The file is not a valid image.") from exc
    if target_format == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    # Copy pixels only: EXIF, ICC, XMP and text chunks are not carried over.
    clean = Image.new(image.mode, image.size)
    clean.paste(image)
    if image.mode == "P" and image.getpalette():
        clean.putpalette(image.getpalette() or [])
    buffer = io.BytesIO()
    clean.save(buffer, format=target_format)
    content_type = next(key for key, value in IMAGE_TYPES.items() if value == target_format)
    return buffer.getvalue(), content_type
