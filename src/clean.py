"""Deterministic, high-fidelity cleaning of opinion text (see docs/clean-text-design.md).

`clean_opinion(raw_html)` renders an opinion's stored `raw_html` (CourtListener
`html_with_citations`, in either the div-HTML or the Harvard-XML dialect) into a canonical
`clean_text`, and returns alongside it a page-break map. It is:

- deterministic — pure functions, same input -> same output; no LLM/statistical passes;
- conservative — the ONLY content dropped is star-pagination page markers (captured instead as page
  breaks): the structural `<span class="star-pagination">` / `<page-number>` forms, plus the
  *bracketed* inline text form (`[*626`, `*625]`). Bare unbracketed `*54` and all other original
  content — footnote bodies and their inline ref markers, the case caption, citations — is kept;
- non-destructive — `raw_html` + `plain_text` are untouched; this is a derived column;
- no OCR handling of any kind — the text is rendered as the source has it, errors included.

Normalization: `\r`->`\n`, control chars stripped (except `\n`/`\t`), whitespace collapsed, Unicode
NFC. No ASCII folding in the canonical column (that lives in the FTS tokenizer instead). The `■`
OCR "unreadable character" glyph is KEPT verbatim — it marks missing text in the source, and
preserving it is a rendering decision, not a judgment about the text.

Locating or correcting OCR damage is deliberately NOT this module's concern: it belongs to the OCR
application, which owns detection and evaluation end to end and emits occurrence records carrying
their own evidence and provenance. A word list embedded here could only assert that a *token* is
sometimes suspect — never that a given occurrence is wrong — so it does not belong in the cleaner.
"""

import re
import unicodedata
from html.parser import HTMLParser

# Bump when the cleaning logic changes: stored in opinions.clean_version so a rebuild is detectable
# and char_offsets in page_breaks are always interpreted against the matching text.
CLEAN_VERSION = 1

# Block-level tags that should produce a line break in the rendered text.
_BLOCK = {
    "p",
    "div",
    "br",
    "center",
    "blockquote",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "opinion",
    "author",
    "parties",
    "footnote",
    "headnotes",
    "syllabus",
    "page",
}

# Private-use sentinel wrapping the index of a structural page-break marker in the render buffer.
# PUA chars survive whitespace/NFC normalization untouched, then are resolved to offsets at the end
_S0, _S1 = "", ""

# A page break is EITHER a structural sentinel (star-pagination span / page-number element) OR a
# *bracketed* inline marker (`[*626`, `*625]`). Bare unbracketed `*54` is deliberately NOT matched:
# too ambiguous (footnote asterisk vs. real content) — so it is preserved verbatim.
_BREAK_RE = re.compile(_S0 + r"(?P<sidx>\d+)" + _S1 + r"|\[\*(?P<opn>\d+)\]?|\*(?P<cls>\d+)\]")


class _Renderer(HTMLParser):
    """Render opinion markup to text, suppressing structural star-pagination / page-number markers
    and recording a sentinel (+ its page label) at each one's position."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.buf = []
        self.labels = []  # page_label per structural break, in document order
        self._pb_tag = None  # tag name of the page-break element currently open (else None)
        self._pb_text = ""  # its text content, to parse a label from when the attr is absent

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if (tag == "span" and "star-pagination" in classes) or tag == "page-number":
            self.buf.append(f"{_S0}{len(self.labels)}{_S1}")
            self.labels.append(a.get("label"))  # may be None -> filled from text at end
            self._pb_tag = tag
            self._pb_text = ""
            return
        if tag in _BLOCK:
            self.buf.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK:
            self.buf.append("\n")

    def handle_endtag(self, tag):
        if self._pb_tag is not None and tag == self._pb_tag:
            if self.labels[-1] is None:  # no label attr: parse from the '*NNN' text
                self.labels[-1] = self._pb_text.lstrip("*").strip() or None
            self._pb_tag = None
            return
        if tag in _BLOCK:
            self.buf.append("\n")

    def handle_data(self, data):
        if self._pb_tag is not None:
            self._pb_text += data  # captured for the label, not emitted
        else:
            self.buf.append(data)


def _normalize(s):
    """Whitespace/control/Unicode normalization that preserves the page-break sentinels."""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # strip control chars except \n and \t (keeps the private-use sentinels, which aren't controls)
    s = "".join(ch for ch in s if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)  # trim spaces around newlines
    s = re.sub(r"\n{3,}", "\n\n", s)  # collapse blank runs to one blank line
    s = unicodedata.normalize("NFC", s)
    return s.strip()


def clean_opinion(raw_html):
    """Return (clean_text, page_breaks) for one opinion's raw_html.

    page_breaks: list of {ordinal, page_label, char_offset, anchor}, ordered by position;
    char_offset indexes into the returned clean_text (where the reporter's page begins)."""
    if not raw_html or not raw_html.strip():
        return "", []

    # Defensive: drop any pre-existing sentinel chars from the input so they can't be mistaken for
    # renderer-inserted page-break markers (they are private-use and never legitimate content).
    raw_html = raw_html.replace(_S0, "").replace(_S1, "")

    r = _Renderer()
    r.feed(raw_html)
    r.close()
    normalized = _normalize("".join(r.buf))

    # Single pass over both marker kinds: build clean_text with markers removed, noting each
    # break's position (= where the following page text begins).
    out, raw_breaks, last = [], [], 0
    grown = 0
    for m in _BREAK_RE.finditer(normalized):
        seg = normalized[last : m.start()]
        out.append(seg)
        grown += len(seg)
        if m.group("sidx") is not None:
            label = r.labels[int(m.group("sidx"))]
        else:
            label = m.group("opn") or m.group("cls")
        raw_breaks.append((label, grown))
        last = m.end()
    out.append(normalized[last:])
    clean = "".join(out)

    breaks = []
    for ordinal, (label, off) in enumerate(raw_breaks, 1):
        while off < len(clean) and clean[off] in " \n\t":  # advance to the page's first real char
            off += 1
        anchor = " ".join(clean[off : off + 80].split()[:6])
        breaks.append(
            {"ordinal": ordinal, "page_label": label, "char_offset": off, "anchor": anchor}
        )

    return clean, breaks
