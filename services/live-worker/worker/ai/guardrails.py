"""Guardrails for untrusted text and for model output.

Two different problems, two different mechanisms.

**Untrusted text** is anything a third party wrote: a headline, a filing, a
telegram message, a scraped page. It is DATA. A model that reads it must not
be able to obey it. Two defences, both applied here:

  1. Sanitise: strip control characters, zero-width joiners and any attempt
     to forge a delimiter or a role marker.
  2. Detect: a pattern list for the classic instruction-override phrasings.
     A hit is recorded, the matched span is neutralised, and the caller can
     decide to drop the item entirely.

**Model output** is checked for language that promises outcomes. The app
advises; it never guarantees. A narration that says "guaranteed" or
"risk-free" is a bug in the prompt, and the fix is to drop the output rather
than to publish it.

These checks are advisory for the LIVE sidecar: they refuse a bad input or a
bad output, they never change a number in a ticket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Control characters except tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Zero-width and bidi characters used to hide instructions from a human reader.
_INVISIBLE = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")
# Any tag or bracket that could imitate a role marker.
_FORGED_MARKER = re.compile(
    r"<\s*/?\s*(system|assistant|user|developer|instructions?)\s*>|\[\s*/?\s*(system|assistant|developer|instructions?)\s*\]",
    re.IGNORECASE,
)


# (name, pattern) pairs. The names are what get logged; the patterns are
# what gets matched.
_INJECTION_RULES: tuple[tuple[str, str], ...] = (
    (
        "ignore_previous",
        r"(?i)\bignore\b.{0,24}\b(previous|prior|earlier|above|all)\b.{0,24}\b(instruction|prompt|rule|direction)s?\b",
    ),
    (
        "disregard_rules",
        r"(?i)\bdisregard\b.{0,24}\b(instruction|prompt|rule|guideline)s?\b",
    ),
    ("you_are_now", r"(?i)\byou are now\b"),
    ("act_as", r"(?i)\bact as (if )?(you are|you|a)\b"),
    ("pretend", r"(?i)\bpretend\b.{0,24}\b(you are|to be)\b"),
    ("role_marker", r"(?i)\bsystem\s*[:=]"),
    ("bracketed_system", r"(?i)\[\s*/?\s*system\s*\]"),
    ("xml_role", r"(?i)<\s*/?\s*(system|assistant|developer)\s*>"),
    ("chatml_marker", r"(?i)<\|\s*(im_start|im_end|system)\s*\|>"),
    ("new_instructions", r"(?i)\bnew instructions?\s*[:=]"),
    (
        "forget",
        r"(?i)\bforget\b.{0,24}\b(everything|all|previous|prior|instructions?)\b",
    ),
    (
        "reveal_prompt",
        r"(?i)\b(reveal|repeat|print|show|output)\b.{0,24}\b(your|the)\b.{0,24}\b(prompt|instructions?|rules?)\b",
    ),
    (
        "override",
        r"(?i)\boverride\b.{0,24}\b(rules?|instructions?|policy|guardrails?|safety)\b",
    ),
    ("developer_mode", r"(?i)\bdeveloper mode\b"),
    ("do_anything_now", r"(?i)\bdo anything now\b"),
    ("jailbreak", r"(?i)\bjailbreak\b"),
    ("end_of_prompt", r"(?i)\bend of (the )?(system )?prompt\b"),
)

_COMPILED_INJECTION = tuple(
    (name, re.compile(pattern)) for name, pattern in _INJECTION_RULES
)

# Language that promises an outcome. The app advises; it never guarantees.
_OUTCOME_PROMISE_RULES: tuple[tuple[str, str], ...] = (
    ("guaranteed", r"(?i)\bguarantee(d|s)?\b"),
    ("risk_free", r"(?i)\brisk[- ]free\b"),
    ("cannot_lose", r"(?i)\b(can'?t|cannot|won'?t) lose\b"),
    ("sure_thing", r"(?i)\bsure thing\b"),
    ("no_risk", r"(?i)\b(no|zero) risk\b"),
    ("certain_win", r"(?i)\b(certain|guaranteed) (win|profit|return)s?\b"),
    ("must_rise", r"(?i)\b(must|will definitely) (go up|rise|rally)\b"),
    ("double", r"(?i)\b(will|to) double (your|the)\b"),
)

_COMPILED_PROMISE = tuple(
    (name, re.compile(pattern)) for name, pattern in _OUTCOME_PROMISE_RULES
)

_DELIMITER = "UNTRUSTED_TEXT"


@dataclass
class InjectionVerdict:
    """What the guard found in an untrusted text."""

    passed: bool
    matches: list[str] = field(default_factory=list)
    sanitized: str = ""

    @property
    def score(self) -> float:
        """0.0 clean, 1.0 clearly hostile. Three distinct rules is the cap."""
        return min(1.0, len(self.matches) / 3.0)


def sanitize_untrusted(text: str) -> str:
    """Make a text safe to put inside a prompt.

    Removes control characters, invisible characters and forged role markers,
    and caps the length so one enormous article cannot crowd out the rest of
    the context.
    """
    if not text:
        return ""
    cleaned = _CONTROL.sub(" ", text)
    cleaned = _INVISIBLE.sub("", cleaned)
    cleaned = _FORGED_MARKER.sub("[removed]", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse long runs of blank lines.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()[:4000]


def detect_prompt_injection(text: str) -> InjectionVerdict:
    """Look for instruction-override language in an untrusted text."""
    if not text:
        return InjectionVerdict(passed=True, matches=[], sanitized="")

    matches: list[str] = []
    neutralised = text
    for name, pattern in _COMPILED_INJECTION:
        if pattern.search(neutralised):
            matches.append(name)
            neutralised = pattern.sub("[removed]", neutralised)

    return InjectionVerdict(
        passed=not matches,
        matches=matches,
        sanitized=sanitize_untrusted(neutralised),
    )


def wrap_untrusted(text: str, label: str = "news") -> str:
    """Fence an untrusted text so a model reads it as data, not as orders."""
    body = sanitize_untrusted(text)
    # A delimiter inside the body could end the fence early.
    body = body.replace(f"<<{_DELIMITER}", "[removed]").replace(
        f"<<END_{_DELIMITER}", "[removed]"
    )
    return (
        f"<<{_DELIMITER} label=\"{label}\">\n"
        f"{body}\n"
        f"<<END_{_DELIMITER}>>"
    )


def find_outcome_promises(text: str) -> list[str]:
    """Names of the promise-language rules that fired in a model output."""
    if not text:
        return []
    hits = [name for name, pattern in _COMPILED_PROMISE if pattern.search(text)]
    return hits
