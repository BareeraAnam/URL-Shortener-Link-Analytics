from io import BytesIO

import qrcode
import qrcode.image.svg
from qrcode.constants import ERROR_CORRECT_M


def render_qr(data: str, fmt: str = "png", box_size: int = 10, border: int = 4) -> tuple[bytes, str]:
    """Return (bytes, content_type) for a QR code encoding ``data``."""
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    buffer = BytesIO()
    if fmt == "svg":
        qr.make_image(image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
        return buffer.getvalue(), "image/svg+xml"
    qr.make_image(fill_color="black", back_color="white").save(buffer)
    return buffer.getvalue(), "image/png"
