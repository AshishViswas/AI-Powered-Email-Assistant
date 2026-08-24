import base64

from app.gmail.parser import parse_message


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def _headers(subject: str, sender: str) -> list[dict]:
    return [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": sender},
        {"name": "Date", "value": "Tue, 18 Aug 2026 10:00:00 +0000"},
    ]


def test_parse_plain_text_only():
    raw = {
        "id": "msg1",
        "payload": {
            "mimeType": "text/plain",
            "headers": _headers("Plain subject", "alice@example.com"),
            "body": {"data": _b64("Hello, this is plain text.")},
        },
    }
    result = parse_message(raw)
    assert result["subject"] == "Plain subject"
    assert result["sender"] == "alice@example.com"
    assert result["body"] == "Hello, this is plain text."


def test_parse_html_only():
    raw = {
        "id": "msg2",
        "payload": {
            "mimeType": "text/html",
            "headers": _headers("HTML subject", "bob@example.com"),
            "body": {"data": _b64("<p>Hello <b>world</b></p>")},
        },
    }
    result = parse_message(raw)
    assert "Hello" in result["body"]
    assert "world" in result["body"]
    assert "<b>" not in result["body"]


def test_parse_multipart_alternative_prefers_plain_text():
    raw = {
        "id": "msg3",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": _headers("Multipart subject", "carol@example.com"),
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("Plain version")}},
                {"mimeType": "text/html", "body": {"data": _b64("<p>HTML version</p>")}},
            ],
        },
    }
    result = parse_message(raw)
    assert result["body"] == "Plain version"


def test_parse_missing_headers_fall_back_to_defaults():
    raw = {"id": "msg4", "payload": {"mimeType": "text/plain", "headers": [], "body": {}}}
    result = parse_message(raw)
    assert result["subject"] == "(no subject)"
    assert result["sender"] == "(unknown sender)"
    assert result["body"] == ""
