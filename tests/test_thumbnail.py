"""Real Pillow test — no mocks needed, compositing is pure local compute
(same reasoning as Phase 4's ffmpeg tests)."""

from io import BytesIO

from PIL import Image

from src.workers.thumbnail_utils import HEIGHT, WIDTH, compose_thumbnail


def _synthetic_base_image(size=(800, 600), color=(80, 120, 200)) -> bytes:
    img = Image.new("RGB", size, color=color)
    buf = BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_compose_thumbnail_produces_correct_dimensions():
    result = compose_thumbnail(_synthetic_base_image(), "A Short Title")
    out = Image.open(BytesIO(result))
    assert out.size == (WIDTH, HEIGHT)
    assert out.format == "PNG"


def test_compose_thumbnail_handles_long_wrapping_text():
    long_text = (
        "This Is A Fairly Long Thumbnail Title That Definitely Needs To Wrap "
        "Across Several Lines To Fit"
    )
    result = compose_thumbnail(_synthetic_base_image(), long_text, max_lines=3)
    out = Image.open(BytesIO(result))
    assert out.size == (WIDTH, HEIGHT)


def test_compose_thumbnail_handles_non_square_source_image():
    # Portrait source — ImageOps.fit must crop/scale to the fixed 16:9 output
    # rather than distorting or erroring.
    result = compose_thumbnail(_synthetic_base_image(size=(400, 900)), "Portrait Source")
    out = Image.open(BytesIO(result))
    assert out.size == (WIDTH, HEIGHT)


def test_compose_thumbnail_handles_empty_text():
    result = compose_thumbnail(_synthetic_base_image(), "")
    out = Image.open(BytesIO(result))
    assert out.size == (WIDTH, HEIGHT)
