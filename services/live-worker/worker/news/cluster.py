"""Cluster near-duplicate headlines.

One story reaches us from five outlets. If the agent reads all five, the
story looks like five events, the token bill grows, and the thesis tilts
toward whoever shouted loudest. Clustering collapses the duplicates into one
cluster with a representative headline and a count of how many outlets ran
it.

The similarity measure is word overlap (Jaccard): the share of words two
headlines share. It is crude, deterministic, needs no model, and costs
nothing. Two headlines are the same story when they share roughly a quarter
of their words; two headlines in the same category need a bit less, because a
category-specific vocabulary ("NFP", "payrolls") is shared by unrelated
stories as well.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

_TOKEN = re.compile(r"[a-z0-9]+")

# Words that carry no discriminating power in a headline.
_STOPWORDS = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "into", "over",
        "after", "before", "says", "said", "will", "has", "have", "are", "was",
        "its", "his", "her", "their", "they", "you", "our", "but", "not",
        "more", "than", "amid", "as", "at", "by", "on", "in", "of", "to",
        "a", "an", "is", "be", "it", "up", "down", "out",
    }
)


@dataclass(frozen=True)
class Headline:
    """One article, as far as the clusterer cares."""

    id: str
    title: str
    source: str = ""
    published_at: datetime | None = None
    category: str = ""
    instruments: tuple[str, ...] = ()


@dataclass(frozen=True)
class Cluster:
    """A group of headlines that tell the same story."""

    key: str
    representative: Headline
    members: tuple[Headline, ...]
    sources: tuple[str, ...]
    first_seen: datetime | None
    last_seen: datetime | None

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def source_count(self) -> int:
        return len({m.source for m in self.members if m.source})

    @property
    def is_duplicate(self) -> bool:
        return self.size > 1


def tokenize(title: str) -> frozenset[str]:
    """The meaningful words of a headline."""
    words = _TOKEN.findall((title or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOPWORDS)


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Share of words two sets have in common. 0.0 to 1.0."""
    if not left and not right:
        return 0.0
    if not left or not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    return intersection / union if union else 0.0


def cluster_headlines(
    headlines: list[Headline],
    *,
    threshold: float = 0.25,
    category_threshold: float = 0.2,
    window_hours: float = 24.0,
) -> list[Cluster]:
    """Group headlines that tell the same story.

    The result is deterministic: the same input list always produces the same
    clusters in the same order, whatever order the input arrived in.
    """
    if not headlines:
        return []

    window = timedelta(hours=window_hours)
    ordered = sorted(
        headlines,
        key=lambda h: (_epoch(h.published_at), h.id),
    )

    clusters: list[Cluster] = []
    assigned: set[str] = set()

    for item in ordered:
        if item.id in assigned:
            continue

        matched: Cluster | None = None
        for existing in clusters:
            rep = existing.representative
            if _too_far(rep.published_at, item.published_at, window):
                continue
            limit = (
                category_threshold
                if rep.category and rep.category == item.category
                else threshold
            )
            if jaccard(tokenize(rep.title), tokenize(item.title)) >= limit:
                matched = existing
                break

        if matched is not None:
            # The story is already known: join it instead of starting again.
            members = list(matched.members) + [item]
            clusters[clusters.index(matched)] = _build_cluster(members)
            assigned.add(item.id)
            continue

        clusters.append(_build_cluster([item]))
        assigned.add(item.id)

    return clusters


def _build_cluster(members: list[Headline]) -> Cluster:
    """Build a cluster from its members, earliest story first."""
    members = sorted(members, key=lambda h: (_epoch(h.published_at), h.id))
    representative = members[0]
    sources = tuple(dict.fromkeys(m.source for m in members if m.source))
    return Cluster(
        key=cluster_key(members),
        representative=representative,
        members=tuple(members),
        sources=sources,
        first_seen=min(
            (m.published_at for m in members if m.published_at), default=None
        ),
        last_seen=max(
            (m.published_at for m in members if m.published_at), default=None
        ),
    )


def cluster_key(members: list[Headline]) -> str:
    """A stable identifier for a cluster: the same members, the same key."""
    ids = sorted(m.id for m in members)
    digest = hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()
    return digest[:12]


def _epoch(value: datetime | None) -> float:
    if value is None:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _too_far(
    left: datetime | None, right: datetime | None, window: timedelta
) -> bool:
    if left is None or right is None:
        return False
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return abs(left - right) > window
