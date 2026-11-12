"""News pipeline helpers.

The pipeline itself is Phase 4. What lives here now is the deterministic
half that everything else depends on: the parts that decide whether two
headlines are the same story and whether a headline is allowed to speak to
the model at all.

Both are pure functions with no model, no network and no database, so they
are cheap to test and impossible to misconfigure.
"""

from .cluster import (
    Cluster,
    Headline,
    cluster_headlines,
    cluster_key,
    jaccard,
    tokenize,
)

__all__ = [
    "Cluster",
    "Headline",
    "cluster_headlines",
    "cluster_key",
    "jaccard",
    "tokenize",
]
