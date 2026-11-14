import pytest

from face_index.models import Skipped

SHA = b"\x01" * 32


@pytest.mark.parametrize(("width", "height"), [(10, None), (None, 10)])
def test_skipped_rejects_half_set_original_dimensions(
    width: int | None, height: int | None
) -> None:
    with pytest.raises(ValueError, match="both be set or both be unset"):
        Skipped(SHA, "too_large", "JPEG", width, height)


def test_skipped_accepts_unknown_or_complete_dimensions() -> None:
    Skipped(SHA, "unsupported_format", "SVG")
    Skipped(SHA, "too_large", "JPEG", 9000, 9000)
