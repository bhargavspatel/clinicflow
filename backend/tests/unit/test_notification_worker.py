"""Unit tests for notification_worker._generate_sms_body.

All external calls (OpenAI) are mocked.  No database, no Twilio, no Redis.
Tests exercise the three outcome paths:
  1. LLM returns a valid, compliant, ≤160-char message  → use it, fallback=False
  2. LLM raises an exception                             → rule-based fallback
  3. LLM returns a message longer than 160 chars         → rule-based fallback
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.workers.notification_worker import _generate_sms_body

# ── Shared fake model objects (no DB required) ────────────────────────────────

_SCHEDULED_AT = datetime(2026, 6, 10, 15, 30, tzinfo=timezone.utc)

_APPT    = SimpleNamespace(scheduled_at=_SCHEDULED_AT, appointment_type="follow_up")
_PATIENT = SimpleNamespace(full_name="Jane Doe")
_PROV    = SimpleNamespace(full_name="John Smith")
_TENANT  = SimpleNamespace(name="Sunrise PT")
_LINK    = "https://sunrise-pt.clinicflow.app/reschedule/abc123"
_MODEL   = "gpt-4o-mini"


def _make_openai_response(message: str | None, compliant: bool, tokens: int = 42) -> MagicMock:
    """Build a minimal mock of the OpenAI chat completions response."""
    content_json = json.dumps({
        "message":         message,
        "character_count": len(message) if message else 0,
        "compliant":       compliant,
    })
    mock_resp = MagicMock()
    mock_resp.choices[0].message.content = content_json
    mock_resp.usage.total_tokens = tokens
    return mock_resp


def _make_openai_client(response: MagicMock) -> AsyncMock:
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_llm_generates_valid_sms_under_160_chars() -> None:
    """When the LLM returns a compliant message ≤160 chars, it is used as-is."""
    sms_text = (
        "Hi Jane, reminder: follow-up with Dr. Smith on Jun 10 at 3:30 PM. "
        "Reply YES to confirm or tap the link. – Sunrise PT"
    )
    assert len(sms_text) <= 160, "test fixture itself must be ≤160 chars"

    client = _make_openai_client(_make_openai_response(sms_text, compliant=True, tokens=35))

    body, fallback, tokens = await _generate_sms_body(
        client, _MODEL, _APPT, _PATIENT, _PROV, _TENANT, _LINK
    )

    assert fallback is False
    assert body == sms_text
    assert len(body) <= 160
    assert tokens == 35


async def test_fallback_on_openai_exception() -> None:
    """When the OpenAI call raises any exception, the rule-based fallback fires.

    NOTE: _generate_sms_body returns the raw fallback string; it is the
    caller's (send_sms_task) responsibility to run _truncate_to_160 before
    sending.  We therefore assert on content, not length.
    """
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(
        side_effect=Exception("OpenAI network error")
    )

    body, fallback, tokens = await _generate_sms_body(
        client, _MODEL, _APPT, _PATIENT, _PROV, _TENANT, _LINK
    )

    assert fallback is True
    # Fallback template must include patient first name and provider last name
    assert "Jane" in body
    assert "Smith" in body
    # Template key phrases
    assert "reminder" in body
    assert "YES" in body
    assert tokens == 0   # no tokens consumed when the call never completes


async def test_fallback_on_message_over_160_chars() -> None:
    """When the LLM returns a message longer than 160 chars, fallback is triggered.

    This covers the case where the model marks itself compliant=True but the
    output exceeds the hard character limit.

    NOTE: _generate_sms_body returns the raw fallback template; the caller
    (send_sms_task) applies _truncate_to_160 before sending.  We assert on
    fallback trigger and template content, not final length.
    """
    oversized = "A" * 200   # 200 chars — well over the 160-char limit
    client = _make_openai_client(
        _make_openai_response(oversized, compliant=True, tokens=60)
    )

    body, fallback, tokens = await _generate_sms_body(
        client, _MODEL, _APPT, _PATIENT, _PROV, _TENANT, _LINK
    )

    assert fallback is True
    # Fallback template must include patient first name and provider last name
    assert "Jane" in body
    assert "Smith" in body
    assert "YES" in body
    # LLM was still called, so tokens are reported
    assert tokens == 60
