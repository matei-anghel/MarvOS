from __future__ import annotations

import base64
from io import BytesIO


def capture_png_base64() -> str | None:
    try:
        from PIL import ImageGrab  # type: ignore
    except Exception:
        return None

    try:
        img = ImageGrab.grab(all_screens=True)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None

