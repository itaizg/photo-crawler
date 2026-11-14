import pytest

from face_index.models import Skipped

SHA = b"\x01" * 32


@pytest.mark.parametrize(("width", "height"), [(10, None), (None, 10)])
def test_skipped_rejects_half_set_original_dimensions(
    width: int | None, height: int | None
) -> None:
    with pytest.raises(ValueError, match="both be set or both be unset"):
        Skipped(
            sha256=SHA,
            reason="too_large",
            original_format="JPEG",
            original_width=width,
            original_height=height,
        )


def test_skipped_accepts_unknown_or_complete_dimensions() -> None:
    Skipped(sha256=SHA, reason="unsupported_format", original_format="SVG")
    Skipped(
        sha256=SHA,
        reason="too_large",
        original_format="JPEG",
        original_width=9000,
        original_height=9000,
    )
