"""
Canonical blocklist of file extensions that cannot be sent as email
attachments (D-04, D-04a).

This module is the **single source of truth** for the union of Gmail's
public list of disallowed attachment types
(https://support.google.com/mail/answer/6590) and Outlook's default
"level 1" blocked extension list documented at
https://learn.microsoft.com/en-us/exchange/security-and-compliance/mail-flow-rules/common-attachment-blocking-scenarios.

Why a *union*:

- **Gmail rejects synchronously** with ``400 badRequest "The attachment
  is invalid"`` — any blocked extension is detectable from the API
  response.
- **Outlook does NOT reject synchronously**: ``sendMail`` and
  ``POST /attachments`` return ``202 Accepted`` / ``201 Created`` even
  when the attachment is a level-1 blocked type. The rejection arrives
  later as a delayed NDR in the sender's inbox; **there is no way to
  detect it from the Graph API response**.

Applying the union ensures that any file passing this filter reaches the
recipient regardless of which provider sends it.

A frontend mirror of this list lives at
``frontend/src/lib/blocked_extensions.json`` and **must contain exactly
the same entries**. A test of parity (``tests/integration/...``)
compares both files byte-for-byte; CI breaks if they diverge. Sync is
manual on purpose — there is no cross-language build step in MVP.

Limitations accepted (D-19): no magic-bytes / signature inspection. A
``.exe`` renamed to ``.pdf`` slips through this filter. The provider's
own anti-malware pipeline handles the residual risk.
"""

from __future__ import annotations


# Sorted alphabetically for deterministic comparison with the frontend
# JSON. Each entry is the lowercase extension WITHOUT the leading dot.
BLOCKED_EXTENSIONS: tuple[str, ...] = (
    "ade",
    "adp",
    "apk",
    "app",
    "appcontent-ms",
    "application",
    "appref-ms",
    "appx",
    "appxbundle",
    "asp",
    "aspx",
    "asx",
    "bas",
    "bat",
    "bgi",
    "cab",
    "cdxml",
    "cer",
    "chm",
    "cmd",
    "cnt",
    "com",
    "cpl",
    "crt",
    "csh",
    "der",
    "diagcab",
    "diagcfg",
    "diagpkg",
    "dll",
    "dmg",
    "ex",
    "ex_",
    "exe",
    "fxp",
    "gadget",
    "grp",
    "hlp",
    "hpj",
    "hta",
    "htc",
    "img",
    "inf",
    "ins",
    "iso",
    "isp",
    "its",
    "jar",
    "jnlp",
    "js",
    "jse",
    "ksh",
    "lib",
    "lnk",
    "mad",
    "maf",
    "mag",
    "mam",
    "maq",
    "mar",
    "mas",
    "mat",
    "mau",
    "mav",
    "maw",
    "mcf",
    "mda",
    "mdb",
    "mde",
    "mdt",
    "mdw",
    "mdz",
    "mht",
    "mhtml",
    "mjs",
    "msc",
    "msh",
    "msh1",
    "msh1xml",
    "msh2",
    "msh2xml",
    "mshxml",
    "msi",
    "msix",
    "msixbundle",
    "msp",
    "mst",
    "msu",
    "nsh",
    "ops",
    "osd",
    "pcd",
    "pif",
    "pl",
    "plg",
    "prf",
    "prg",
    "printerexport",
    "ps1",
    "ps1xml",
    "ps2",
    "ps2xml",
    "psc1",
    "psc2",
    "psd1",
    "psdm1",
    "pssc",
    "pst",
    "py",
    "pyc",
    "pyo",
    "pyw",
    "pyz",
    "pyzw",
    "reg",
    "scf",
    "scr",
    "sct",
    "settingcontent-ms",
    "shb",
    "shs",
    "sys",
    "theme",
    "tmp",
    "udl",
    "url",
    "vb",
    "vbe",
    "vbp",
    "vbs",
    "vhd",
    "vhdx",
    "vsmacros",
    "vsw",
    "vxd",
    "webpnp",
    "website",
    "ws",
    "wsb",
    "wsc",
    "wsf",
    "wsh",
    "xbap",
    "xll",
    "xnk",
)

_BLOCKED_SET: frozenset[str] = frozenset(BLOCKED_EXTENSIONS)


def is_blocked_extension(filename: str) -> bool:
    """Return True if ``filename`` ends in a blocked extension.

    The extension is the substring after the last ``.`` in the filename,
    case-insensitive. Filenames without a ``.`` or with an empty
    extension are NOT blocked (a file called ``Makefile`` is allowed).
    """
    if not filename:
        return False
    idx = filename.rfind(".")
    if idx < 0 or idx == len(filename) - 1:
        return False
    extension = filename[idx + 1 :].lower()
    return extension in _BLOCKED_SET
