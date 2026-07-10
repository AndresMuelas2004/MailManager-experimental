"""Constantes y builders compartidos por los tests espejo de ``emails_service``."""

from __future__ import annotations


_MAILBOX_ID = "mb1"
_ACCOUNT_ID = "acc1"
_USER_ID = "user1"
_PROVIDER = "gmail"
_LABEL = f"{_MAILBOX_ID}__{_ACCOUNT_ID}"
_ACCOUNT_ID_2 = "acc2"
_LABEL_2 = f"{_MAILBOX_ID}__{_ACCOUNT_ID_2}"


def _fake_account(account_id=_ACCOUNT_ID, provider=_PROVIDER) -> dict:
    return {
        "account_id": account_id,
        "mailbox_id": _MAILBOX_ID,
        "provider": provider,
        "display_label": f"{provider}:{account_id}",
    }
