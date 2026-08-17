from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 1280, 720

# DejaVu (fonts-dejavu-core, installed in the Dockerfile) is what production
# actually uses — permissively licensed (Bitstream Vera License), safe to
# bundle. The Windows path is local-dev convenience only, never shipped.
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: float) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def compose_thumbnail(image_bytes: bytes, text: str, max_lines: int = 3) -> bytes:
    """Crops a base image to standard 1280x720 thumbnail size, adds a dark
    gradient band at the bottom, and overlays wrapped bold title text.
    Returns PNG bytes."""
    base = Image.open(BytesIO(image_bytes)).convert("RGB")
    base = ImageOps.fit(base, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)

    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    band_height = int(HEIGHT * 0.34)
    draw.rectangle([0, HEIGHT - band_height, WIDTH, HEIGHT], fill=(0, 0, 0, 165))

    font = _load_font(72)
    margin = 40
    lines = _wrap_text(draw, text.upper(), font, WIDTH - margin * 2)[:max_lines]

    line_height = 84
    total_text_height = len(lines) * line_height
    y = HEIGHT - band_height + max((band_height - total_text_height) // 2, 10)
    for line in lines:
        draw.text((margin, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height

    composed = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
    out = BytesIO()
    composed.save(out, format="PNG")
    return out.getvalue()
