from datetime import datetime
from unittest.mock import MagicMock, patch

from app.agents.summarizer_agent import SummaryOutput, format_email_batch, summarize_emails


def _sample_emails() -> list[dict]:
    return [
        {
            "message_id": "msg1",
            "sender": "boss@example.com",
            "subject": "Please send the report",
            "date": datetime(2026, 8, 18, 9, 0, 0),
            "body": "Can you send the Q3 report by Friday?",
        }
    ]


def test_format_email_batch_includes_key_fields():
    batch_text = format_email_batch(_sample_emails())
    assert "msg1" in batch_text
    assert "boss@example.com" in batch_text
    assert "Please send the report" in batch_text
    assert "Q3 report" in batch_text


def test_summarize_emails_returns_agent_output_without_calling_real_api():
    expected_output = SummaryOutput(
        summary="One email asking for the Q3 report by Friday.",
        triage=[
            {
                "message_id": "msg1",
                "priority": "action",
                "category": "work",
                "suggested_action": "Send the Q3 report",
                "deadline": "2026-08-21",
            }
        ],
        action_items=[
            {
                "description": "Send the Q3 report",
                "deadline": "2026-08-21",
                "source_message_id": "msg1",
            }
        ],
    )
    mock_result = MagicMock(final_output=expected_output)

    with patch("app.agents.summarizer_agent.Runner.run_sync", return_value=mock_result) as mock_run:
        result = summarize_emails(_sample_emails())

    mock_run.assert_called_once()
    assert result is expected_output
    assert result.action_items[0].source_message_id == "msg1"
