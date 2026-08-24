import base64
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self._parts)).strip()


def _html_to_text(html: str) -> str:
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


def _decode_body_data(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def _extract_text_parts(payload: dict) -> tuple[str | None, str | None]:
    mime_type = payload.get("mimeType", "")
    body_data = payload.get("body", {}).get("data")

    if mime_type == "text/plain" and body_data:
        return _decode_body_data(body_data), None
    if mime_type == "text/html" and body_data:
        return None, _decode_body_data(body_data)

    plain_text = None
    html_text = None
    for part in payload.get("parts", []):
        part_plain, part_html = _extract_text_parts(part)
        if plain_text is None and part_plain:
            plain_text = part_plain
        if html_text is None and part_html:
            html_text = part_html
        if plain_text and html_text:
            break

    return plain_text, html_text


def _find_body(payload: dict) -> str:
    plain_text, html_text = _extract_text_parts(payload)
    if plain_text:
        return plain_text
    if html_text:
        return _html_to_text(html_text)
    return ""


def _get_header(headers: list[dict], name: str) -> str | None:
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value")
    return None


import html as html_lib


def parse_message(raw_message: dict) -> dict:
    payload = raw_message.get("payload", {})
    headers = payload.get("headers", [])

    date_header = _get_header(headers, "Date")
    date: datetime | None
    try:
        date = parsedate_to_datetime(date_header) if date_header else None
    except (TypeError, ValueError):
        date = None

    plain_text, html_text = _extract_text_parts(payload)
    
    if plain_text:
        body_text = html_lib.unescape(plain_text)
    elif html_text:
        body_text = html_lib.unescape(_html_to_text(html_text))
    else:
        body_text = ""

    # Collapse excessive blank lines
    body_text = re.sub(r"\n{3,}", "\n\n", body_text).strip()

    return {
        "message_id": raw_message.get("id"),
        "subject": _get_header(headers, "Subject") or "(no subject)",
        "sender": _get_header(headers, "From") or "(unknown sender)",
        "date": date,
        "body": body_text,
        "body_text": body_text,
        "html_body": html_text,
        "snippet": raw_message.get("snippet") or (body_text[:200] if body_text else ""),
    }
