"""Pure text/span utilities for the resume judge.

Stdlib only (no bs4) so this stays cheap to import and trivially typed.
Used by the judge runner (HTML normalization), the eval metric
(finding alignment), and the synthetic-data post-checks (span findability).
"""

import re
from difflib import SequenceMatcher
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "header",
    "footer",
    "li",
    "ul",
    "ol",
    "table",
    "tr",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
}

_SKIP_TAGS = {"script", "style", "head", "title"}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        return "\n".join(line for line in lines if line)


def looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|body|div|p|ul|li|h[1-6]|table)\b", text, re.IGNORECASE))


def html_to_text(html: str) -> str:
    """Extract readable text from HTML, one block element per line."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def normalize_resume_text(text: str) -> str:
    """Normalize a generated resume (HTML or plain text) to judgeable text."""
    return html_to_text(text) if looks_like_html(text) else text


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def span_similarity(a: str, b: str) -> float:
    """Fuzzy similarity between two quoted spans, in [0, 1].

    Max of character-level SequenceMatcher ratio and token-set Jaccard, so
    both close paraphrases and reordered/partial quotes score high.
    """
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 1.0 if ta == tb else 0.0
    ratio = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    sa, sb = set(ta), set(tb)
    jaccard = len(sa & sb) / len(sa | sb)
    return max(ratio, jaccard)


def find_span_in_text(span: str, text: str) -> float:
    """Best fuzzy-containment score of `span` inside `text`, in [0, 1].

    1.0 for exact (token-normalized) substring; otherwise the best
    span_similarity against sliding token windows of about the span's size.
    """
    span_tokens = _tokens(span)
    text_tokens = _tokens(text)
    if not span_tokens:
        return 0.0
    if " ".join(span_tokens) in " ".join(text_tokens):
        return 1.0
    if len(span_tokens) >= len(text_tokens):
        return span_similarity(span, text)
    best = 0.0
    for window in {max(1, len(span_tokens) - 1), len(span_tokens), len(span_tokens) + 1}:
        for i in range(len(text_tokens) - window + 1):
            window_text = " ".join(text_tokens[i : i + window])
            best = max(best, span_similarity(span, window_text))
            if best == 1.0:
                return best
    return best


def align_spans(predicted: list[str], gold: list[str], threshold: float = 0.6) -> list[tuple[int, int, float]]:
    """Greedy one-to-one alignment of predicted spans to gold spans.

    Returns (predicted_index, gold_index, similarity) triples for every pair
    with similarity >= threshold, highest-similarity pairs matched first.
    Unmatched indices on either side are misses / false alarms respectively.
    """
    scored: list[tuple[float, int, int]] = []
    for pi, pspan in enumerate(predicted):
        for gi, gspan in enumerate(gold):
            sim = span_similarity(pspan, gspan)
            if sim >= threshold:
                scored.append((sim, pi, gi))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]))
    used_pred: set[int] = set()
    used_gold: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for sim, pi, gi in scored:
        if pi in used_pred or gi in used_gold:
            continue
        used_pred.add(pi)
        used_gold.add(gi)
        matches.append((pi, gi, sim))
    return matches
