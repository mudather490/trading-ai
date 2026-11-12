"""The research worker's write set.

The runner holds the service-role key, because a candidate belongs to
nobody until a human promotes it. Holding the key is not a blank cheque:
the tables that hold DECISIONS are refused in code, so a future change to
the runner cannot quietly start promoting its own candidates.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from research.db import ForbiddenWrite, SupabaseRest  # noqa: E402


def _client() -> SupabaseRest:
    return SupabaseRest("http://127.0.0.1:1", "service-role-key")


def test_the_runner_may_not_promote_its_own_candidate():
    with pytest.raises(ForbiddenWrite):
        _client().post("champions", {"asset_class": "crypto"})


def test_the_runner_may_not_change_the_gate():
    with pytest.raises(ForbiddenWrite):
        _client().post("gate_policy", {"version": "v2"})


def test_the_runner_may_not_write_the_audit_log():
    """It must not be able to write its own audit trail."""
    with pytest.raises(ForbiddenWrite):
        _client().post("audit_log", {"action": "promotion"})


def test_the_runner_may_not_write_user_rows():
    with pytest.raises(ForbiddenWrite):
        _client().post("user_profiles", {"role": "owner"})


def test_the_runner_may_publish_a_candidate():
    """The allowed writes still work. A guard that blocks everything is a
    guard that stops the research."""
    client = _client()
    # The request itself will fail (nothing is listening on port 1), but
    # it must fail at the network, not at the guard.
    with pytest.raises(RuntimeError) as excinfo:
        client.post("strategy_versions", {"version": 1})
    assert not isinstance(excinfo.value, ForbiddenWrite)


def test_the_query_string_does_not_hide_the_table():
    client = _client()
    with pytest.raises(ForbiddenWrite):
        client.post("champions?on_conflict=asset_class", {"a": 1})
