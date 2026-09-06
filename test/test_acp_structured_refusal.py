"""Structured model-side refusals: one ``RefusalInfo`` for every harness.

The Kiro service declines a turn on the ``_kiro.dev/metadata`` channel
(``stopReason: CONTENT_FILTERED`` + a ``refusal`` object) while the terminal
reads ``end_turn``; Anthropic's adapter says only ``stopReason: "refusal"``.
These tests pin that both are folded onto ``STOP_REASON_REFUSAL`` with a
``RefusalInfo`` attached, that the parser is opt-in by capability set, that
provider text is redacted, and that the dashboard card renders exactly the
fields the provider filled.
"""

from __future__ import annotations

import logging

import pytest

from kiro_crew.acp import _dispatch
from kiro_crew.acp._dispatch import parse_metadata, parse_refusal
from kiro_crew.acp.types import (
    ACP_BACKEND_CLAUDE,
    ACP_BACKEND_CODEX,
    ACP_BACKEND_KAS,
    ACP_BACKEND_KIRO,
    ACP_BACKENDS_ACP_RUNTIME,
    ACP_BACKENDS_STRUCTURED_REFUSAL,
    REFUSAL_FROM_STOP_REASON,
    STOP_REASON_CONTENT_FILTERED_WIRE,
    STOP_REASON_END_TURN,
    STOP_REASON_REFUSAL,
    AcpPromptStats,
    RefusalInfo,
)
from kiro_crew.dashboard.chat_runner import refusal_card_text

CANNED = (
    "The selected model cannot continue this conversation. Please select a different "
    "model, or start a new conversation, or rewind the current conversation to an "
    "earlier point and try a different approach."
)

INCIDENT_FRAME = {
    "sessionId": "sess-1",
    "stopReason": STOP_REASON_CONTENT_FILTERED_WIRE,
    "refusal": {"category": "CYBER", "explanation": CANNED, "recommendedModel": None},
}


@pytest.fixture(autouse=True)
def _clear_reported_fields():
    _dispatch._reported_metadata_fields.clear()
    yield
    _dispatch._reported_metadata_fields.clear()


class TestCapabilitySet:
    def test_kiro_and_kas_carry_the_envelope(self):
        assert ACP_BACKEND_KIRO in ACP_BACKENDS_STRUCTURED_REFUSAL
        assert ACP_BACKEND_KAS in ACP_BACKENDS_STRUCTURED_REFUSAL

    def test_claude_and_codex_do_not(self):
        # Not "unsupported": they land on the same RefusalInfo with no fields.
        assert ACP_BACKEND_CLAUDE not in ACP_BACKENDS_STRUCTURED_REFUSAL
        assert ACP_BACKEND_CODEX not in ACP_BACKENDS_STRUCTURED_REFUSAL

    def test_every_shared_runtime_harness_is_a_member(self):
        # AcpSessionHandle reads the envelope unconditionally on the strength of
        # this subset relation; a runtime harness outside the set would have its
        # metadata guessed at.
        assert ACP_BACKENDS_ACP_RUNTIME <= ACP_BACKENDS_STRUCTURED_REFUSAL


class TestParseRefusal:
    def test_incident_frame(self):
        info = parse_refusal(INCIDENT_FRAME)
        assert info == RefusalInfo(
            source="metadata", category="CYBER", explanation=CANNED, recommended_model=""
        )

    def test_ordinary_usage_frame_is_not_a_refusal(self):
        assert parse_refusal({"contextUsagePercentage": 12.0, "meteringUsage": []}) is None
        assert parse_refusal({}) is None

    def test_stop_reason_alone_is_a_refusal(self):
        info = parse_refusal({"stopReason": "content_filtered"})
        assert info is not None and info.source == "metadata" and info.category == ""

    def test_refusal_object_alone_is_a_refusal(self):
        # A future stop-reason spelling must not hide a reason the service sent.
        info = parse_refusal({"stopReason": "SOMETHING_NEW", "refusal": {"category": "X"}})
        assert info is not None and info.category == "X"

    def test_non_string_fields_are_empty_not_guessed(self):
        info = parse_refusal(
            {"stopReason": "CONTENT_FILTERED", "refusal": {"category": 7, "explanation": ["a"]}}
        )
        assert info == RefusalInfo(source="metadata")

    def test_explanation_is_redacted_and_capped(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        info = parse_refusal(
            {
                "stopReason": "CONTENT_FILTERED",
                "refusal": {"explanation": f"key {secret} " + "x" * 2000},
            }
        )
        assert info is not None
        assert secret not in info.explanation
        assert len(info.explanation) <= _dispatch._REFUSAL_EXPLANATION_MAX

    def test_log_names_category_but_never_explanation(self, caplog):
        with caplog.at_level(logging.INFO, logger=_dispatch.logger.name):
            parse_refusal(INCIDENT_FRAME)
        line = next(
            r.getMessage() for r in caplog.records if "content-filter refusal" in r.getMessage()
        )
        assert "CYBER" in line
        assert "cannot continue" not in line

    def test_envelope_keys_are_consumed_not_reported(self, caplog):
        with caplog.at_level(logging.DEBUG, logger=_dispatch.logger.name):
            parse_metadata(INCIDENT_FRAME)
        assert not [r for r in caplog.records if "unconsumed field" in r.getMessage()]

    def test_novel_refusal_key_is_reported_by_name_only(self, caplog):
        frame = {"stopReason": "CONTENT_FILTERED", "refusal": {"policyId": "p-secret"}}
        with caplog.at_level(logging.DEBUG, logger=_dispatch.logger.name):
            parse_metadata(frame)
        line = next(r.getMessage() for r in caplog.records if "unconsumed field" in r.getMessage())
        assert "refusal.policyId:str" in line
        assert "p-secret" not in line


class TestTerminalFold:
    def test_metadata_refusal_rewrites_end_turn(self):
        stats = AcpPromptStats()
        stats.refusal = parse_refusal(INCIDENT_FRAME)
        reason, info = stats.terminal_refusal(STOP_REASON_END_TURN)
        assert reason == STOP_REASON_REFUSAL
        assert info is not None and info.category == "CYBER"

    def test_bare_refusal_stop_reason_gets_the_sentinel(self):
        reason, info = AcpPromptStats().terminal_refusal(STOP_REASON_REFUSAL)
        assert reason == STOP_REASON_REFUSAL
        assert info is REFUSAL_FROM_STOP_REASON
        assert info.category == "" and info.explanation == ""

    def test_ordinary_terminal_is_untouched(self):
        assert AcpPromptStats().terminal_refusal(STOP_REASON_END_TURN) == (
            STOP_REASON_END_TURN,
            None,
        )

    def test_refusal_does_not_survive_the_turn_boundary(self):
        stats = AcpPromptStats()
        stats.refusal = parse_refusal(INCIDENT_FRAME)
        assert stats.carry_over().refusal is None


class TestClientGate:
    """``AcpClient._track_metadata`` consults the parser only for members (H6)."""

    @staticmethod
    def _client(backend: str):
        from kiro_crew.acp import client as acp_client

        c = acp_client.AcpClient.__new__(acp_client.AcpClient)
        c._acp_backend = backend
        c.last_prompt_stats = AcpPromptStats()
        c._resolved_model_id = ""
        c._model = ""
        return c

    def test_kiro_records_the_refusal(self):
        from kiro_crew.acp.types import JsonRpcMessage

        c = self._client(ACP_BACKEND_KIRO)
        c._track_metadata(JsonRpcMessage(method="_kiro.dev/metadata", params=dict(INCIDENT_FRAME)))
        assert c.last_prompt_stats.refusal is not None
        assert c.last_prompt_stats.refusal.category == "CYBER"

    def test_claude_ignores_the_envelope(self):
        from kiro_crew.acp.types import JsonRpcMessage

        c = self._client(ACP_BACKEND_CLAUDE)
        c._track_metadata(JsonRpcMessage(method="_kiro.dev/metadata", params=dict(INCIDENT_FRAME)))
        assert c.last_prompt_stats.refusal is None


class TestCard:
    def test_full_kiro_card(self):
        text = refusal_card_text(parse_refusal(INCIDENT_FRAME))
        assert text.startswith("Response declined by the model.")
        assert "Content filter: cyber." in text
        assert CANNED in text
        assert "rephrasing" in text

    def test_recommended_model_replaces_the_rephrase_hint(self):
        text = refusal_card_text(RefusalInfo(source="metadata", recommended_model="m-2"))
        assert "suggests model 'm-2'" in text
        assert "rephrasing" not in text

    def test_bare_card_has_no_empty_labels(self):
        text = refusal_card_text(REFUSAL_FROM_STOP_REASON)
        assert "Content filter" not in text
        assert "suggests model" not in text
        assert text == refusal_card_text(None)
