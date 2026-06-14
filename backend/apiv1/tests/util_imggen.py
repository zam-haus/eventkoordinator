from __future__ import annotations

import io
import os
from pathlib import Path

from PIL import Image


def write_large_noise_png(path: Path) -> None:
    """Write a high-entropy PNG around 9 MiB so upload progress becomes visible."""
    width = 1800
    height = 1800
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    image.save(path, format="PNG", compress_level=0)

    file_size = path.stat().st_size
    assert file_size >= 9 * 1024 * 1024, (
        f"Generated upload fixture is too small: {file_size} bytes"
    )


def encode_large_noise_png() -> bytes:
    """Write a high-entropy PNG around 9 MiB so upload progress becomes visible."""
    width = 1800
    height = 1800
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG', compress_level=0)
    data = img_byte_arr.getvalue()
    file_size = len(data)
    assert file_size >= 9 * 1024 * 1024, (
        f"Generated upload fixture is too small: {file_size} bytes"
    )
    return data
