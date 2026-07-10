"""Tests espejo de ``core.email.helpers.hilos_respuesta`` (Reply/Forward: asunto, destinatarios, coherencia)."""

from __future__ import annotations

import pytest

from core.email.errors import EmailReplyContextFetchError
from core.email.helpers import (
    build_in_reply_to_and_references,
    build_reply_subject,
    compute_reply_recipients,
    validate_reply_threading_coherence,
)


# ── build_reply_subject ────────────────────────────────────────────


class TestBuildReplySubject:
    """Covers the prefix-detect-and-prepend logic shared by Reply and Forward."""

    def test_reply_no_existing_prefix(self):
        assert build_reply_subject("Hello", "reply") == "Re: Hello"

    def test_reply_canonical_prefix_preserved(self):
        # The function preserves the original prefix shape (R-05 / build_reply_subject doc).
        assert build_reply_subject("Re: Hello", "reply") == "Re: Hello"

    def test_reply_uppercase_prefix_preserved(self):
        assert build_reply_subject("RE: Hello", "reply") == "RE: Hello"

    def test_reply_german_aw_prefix(self):
        # German "Aw:" is a recognised re-prefix.
        assert build_reply_subject("Aw: Hallo", "reply") == "Aw: Hallo"

    def test_reply_swedish_sv_prefix(self):
        assert build_reply_subject("Sv: Hej", "reply") == "Sv: Hej"

    def test_reply_mixed_case(self):
        # The match is case-insensitive — no extra "Re:" is prepended.
        assert build_reply_subject("rE: Hello", "reply") == "rE: Hello"

    def test_reply_all_same_as_reply(self):
        # reply_all uses the same prefix logic as reply.
        assert build_reply_subject("Hello", "reply_all") == "Re: Hello"

    def test_forward_no_existing_prefix(self):
        assert build_reply_subject("Hello", "forward") == "Fwd: Hello"

    def test_forward_fwd_prefix_preserved(self):
        assert build_reply_subject("Fwd: Hello", "forward") == "Fwd: Hello"

    def test_forward_fw_prefix_preserved(self):
        # "Fw:" is the shorter Outlook-style variant — also recognised.
        assert build_reply_subject("Fw: Hello", "forward") == "Fw: Hello"

    def test_forward_spanish_rv_prefix(self):
        assert build_reply_subject("RV: Hola", "forward") == "RV: Hola"

    def test_forward_spanish_reenv_prefix(self):
        assert build_reply_subject("Reenv: Hola", "forward") == "Reenv: Hola"

    def test_empty_subject_reply(self):
        # Empty subject collapses to just the prefix (no trailing space).
        assert build_reply_subject("", "reply") == "Re:"

    def test_empty_subject_forward(self):
        assert build_reply_subject("", "forward") == "Fwd:"

    def test_none_subject_reply(self):
        # ``original_subject=None`` is tolerated (best-effort).
        assert build_reply_subject(None, "reply") == "Re:"

    def test_subject_with_surrounding_whitespace_trimmed(self):
        assert build_reply_subject("   Hello   ", "reply") == "Re: Hello"

    def test_unknown_action_defaults_to_reply(self):
        # Soft fallback per docstring — unknown action behaves like reply.
        assert build_reply_subject("Hello", "weird") == "Re: Hello"


# ── compute_reply_recipients ───────────────────────────────────────


class TestComputeReplyRecipients:
    """Covers To/Cc derivation for all three actions (§5.1 + R-10)."""

    def test_reply_to_overrides_from(self):
        # R-10: mailing-list pattern — Reply-To wins over From.
        to, cc = compute_reply_recipients(
            original_from="bounce@list.com",
            original_reply_to=["editor@list.com"],
            original_to=["someone@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == ["editor@list.com"]
        assert cc == []

    def test_reply_without_reply_to_falls_back_to_from(self):
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["someone@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]
        assert cc == []

    def test_reply_with_whitespace_only_reply_to_falls_back(self):
        # ``reply_to`` filtered for empties; falls back to ``from``.
        to, _cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=["   ", ""],
            original_to=[],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]

    def test_self_reply_from_sent_uses_original_to(self):
        # Replying to my own SENT message → To becomes original To.
        to, cc = compute_reply_recipients(
            original_from="me@me.com",
            original_reply_to=[],
            original_to=["client@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="SENT",
        )
        assert to == ["client@x.com"]
        assert cc == []

    def test_self_reply_from_sent_case_insensitive_box(self):
        # The box check is case-insensitive (upper()).
        to, _cc = compute_reply_recipients(
            original_from="me@me.com",
            original_reply_to=[],
            original_to=["client@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="sent",
        )
        assert to == ["client@x.com"]

    def test_reply_all_excludes_current_account_email_from_cc(self):
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["me@me.com", "carol@x.com"],
            original_cc=["dan@x.com"],
            current_account_email="me@me.com",
            action="reply_all",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]
        assert "me@me.com" not in cc
        assert "carol@x.com" in cc
        assert "dan@x.com" in cc

    def test_reply_all_case_insensitive_dedupe(self):
        # ``Foo@x`` and ``foo@x`` collapse to a single address.
        _to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["DUP@x.com", "dup@x.com"],
            original_cc=["DUP@X.COM"],
            current_account_email="me@me.com",
            action="reply_all",
            original_box="INBOX",
        )
        # Only one of the dup variants survives.
        assert sum(1 for a in cc if a.lower() == "dup@x.com") == 1

    def test_reply_all_when_current_email_unknown_does_not_filter(self):
        # If we don't know our own address we cannot filter; the user
        # ends up in CC — explicit edge case documented in the helper.
        _to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["me@me.com", "carol@x.com"],
            original_cc=[],
            current_account_email=None,
            action="reply_all",
            original_box="INBOX",
        )
        assert "me@me.com" in cc
        assert "carol@x.com" in cc

    def test_reply_all_excludes_primary_from_cc(self):
        # The Reply-To / From address must not appear duplicated in CC.
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=[],
            original_to=["ana@x.com", "carol@x.com"],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply_all",
            original_box="INBOX",
        )
        assert to == ["ana@x.com"]
        assert "ana@x.com" not in cc
        assert "carol@x.com" in cc

    def test_forward_returns_empty_tuple_regardless_of_inputs(self):
        # Forward never pre-fills recipients (§5.1).
        to, cc = compute_reply_recipients(
            original_from="ana@x.com",
            original_reply_to=["editor@list.com"],
            original_to=["me@me.com", "carol@x.com"],
            original_cc=["dan@x.com"],
            current_account_email="me@me.com",
            action="forward",
            original_box="INBOX",
        )
        assert to == []
        assert cc == []

    def test_empty_from_returns_empty_to_for_reply(self):
        # No ``From`` and no ``Reply-To`` → empty primary list, the
        # service surfaces a UX-level error.
        to, _cc = compute_reply_recipients(
            original_from="",
            original_reply_to=[],
            original_to=[],
            original_cc=[],
            current_account_email="me@me.com",
            action="reply",
            original_box="INBOX",
        )
        assert to == []


# ── build_in_reply_to_and_references ───────────────────────────────


class TestBuildInReplyToAndReferences:
    """Covers RFC 5322 header construction (§5.4)."""

    def test_id_without_angle_brackets_gets_wrapped(self):
        irt, refs = build_in_reply_to_and_references("orig@x", "")
        assert irt == "<orig@x>"
        assert refs == "<orig@x>"

    def test_id_already_wrapped_preserved(self):
        irt, refs = build_in_reply_to_and_references("<orig@x>", "")
        assert irt == "<orig@x>"
        assert refs == "<orig@x>"

    def test_existing_references_extended(self):
        # The new id appends to the existing References chain (RFC 5322 § 3.6.4).
        irt, refs = build_in_reply_to_and_references(
            "orig@x", "<oldest@x> <middle@x>",
        )
        assert irt == "<orig@x>"
        assert refs == "<oldest@x> <middle@x> <orig@x>"

    def test_empty_id_returns_empty_strings(self):
        # Defensive: malformed Message-ID should not produce <> headers
        # on the wire (which SMTP rejects).
        irt, refs = build_in_reply_to_and_references("", "<prev@x>")
        assert irt == ""
        assert refs == ""

    def test_whitespace_only_id_returns_empty_strings(self):
        irt, refs = build_in_reply_to_and_references("   ", "<prev@x>")
        assert irt == ""
        assert refs == ""


# ── validate_reply_threading_coherence ─────────────────────────────


class TestValidateReplyThreadingCoherence:
    """Covers the local Gmail-side guard for thread membership (§4.1, R-09 / §14.9)."""

    def _ok_kwargs(self):
        return dict(
            thread_id="thr-1",
            in_reply_to="<orig@x>",
            references="<orig@x>",
            original_message_id="orig@x",
            original_thread_id="thr-1",
            original_subject="Hello",
            new_subject="Re: Hello",
        )

    def test_happy_path_returns_none(self):
        # No exception, returns None (sentinel for "OK").
        assert validate_reply_threading_coherence(**self._ok_kwargs()) is None

    def test_thread_id_mismatch_raises(self):
        kwargs = self._ok_kwargs()
        kwargs["thread_id"] = "thr-DIFFERENT"
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "thread_id_mismatch"

    def test_message_id_not_referenced_raises(self):
        # neither in_reply_to nor references mention the original id.
        kwargs = self._ok_kwargs()
        kwargs["in_reply_to"] = "<other@x>"
        kwargs["references"] = "<other@x>"
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "message_id_not_referenced"

    def test_subject_mismatch_raises(self):
        kwargs = self._ok_kwargs()
        kwargs["new_subject"] = "Re: Completely Different Subject"
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "subject_mismatch"

    def test_subject_stacked_re_prefixes_normalised(self):
        # ``Re: Re: Re: Hello`` matches ``Hello`` after normalisation —
        # the helper must not reject for cosmetic prefix stacks.
        kwargs = self._ok_kwargs()
        kwargs["new_subject"] = "Re: Re: Re: Hello"
        # Should NOT raise.
        assert validate_reply_threading_coherence(**kwargs) is None

    def test_missing_thread_ids_raises_mismatch(self):
        # Calling with empty thread ids is a programming bug, surfaced as
        # ``thread_id_mismatch``.
        kwargs = self._ok_kwargs()
        kwargs["thread_id"] = ""
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "thread_id_mismatch"

    def test_empty_original_message_id_raises(self):
        kwargs = self._ok_kwargs()
        kwargs["original_message_id"] = ""
        with pytest.raises(EmailReplyContextFetchError) as exc_info:
            validate_reply_threading_coherence(**kwargs)
        assert exc_info.value.detail.get("reason") == "message_id_not_referenced"

    def test_message_id_matched_via_references_chain(self):
        # The id can match either ``in_reply_to`` OR somewhere in
        # ``references`` — both forms count as "referenced".
        kwargs = self._ok_kwargs()
        kwargs["in_reply_to"] = "<unrelated@x>"
        kwargs["references"] = "<oldest@x> <orig@x>"
        # Should NOT raise — references contains orig@x.
        assert validate_reply_threading_coherence(**kwargs) is None
