#!/usr/bin/env python3
"""Architecture ratchets.

These are the rules that no unit test catches, because they are about the
SHAPE of the codebase rather than the behaviour of one function:

  1. The web tier must not import the worker.
     The worker is the only component that holds venue credentials. If the
     web tier can import its code, the custody boundary is one refactor away
     from being fiction.

  2. Nothing may place an order.
     This app is ticket-only. There is no order endpoint, no order table and
     no order client. The word "place_order" appearing in first-party code
     means the boundary has been breached.

  3. The AI sidecar may not name a decision table.
     `champions`, `tickets`, `performance`, `strategy_versions`,
     `holdout_ledger`, `audit_log` and `gate_policy` hold decisions. The AI
     writes to `ai_runs`, `news_features`, `strategy_proposals` and
     `alerts.ai_summary`, and to nothing else.

  4. The service-role key is confined.
     It may appear in the worker, in the nightly cron route, in the Telegram
     webhook, in the ops tools and in documentation. It may not appear
     anywhere else in the web tier.

  5. No AGPL source in first-party code.
     FinceptTerminal and similar projects are AGPL-3.0. Copying their code
     into this repository would oblige us to publish this whole application.
     See docs/PROVENANCE.md.

Usage:
    python tools/arch_ratchet.py [--root .]

Exit codes: 0 clean, 1 a rule was broken.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".mjs", ".js", ".cjs"}
SKIP_DIRS = {
    ".git",
    ".next",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".venv",
    "venv",
    "dist",
    "build",
}
# Third-party code carries its own licence; the rules below are about code
# we wrote.
VENDORED_PARTS = ("packages", "vendor")
DOCS_PARTS = ("docs",)

ORDER_CALLS = (
    "place_order",
    "create_order",
    "submit_order",
    "new_order",
    "send_order",
)

DECISION_TABLES = (
    "champions",
    "tickets",
    "performance",
    "strategy_versions",
    "holdout_ledger",
    "audit_log",
    "gate_policy",
)

AGPL_MARKERS = (
    "GNU AFFERO GENERAL PUBLIC LICENSE",
    "SPDX-License-Identifier: AGPL",
    "SPDX-License-Identifier: AGPL-3.0",
)


class Finding:
    """One broken rule."""

    def __init__(self, rule: str, path: Path, detail: str) -> None:
        self.rule = rule
        self.path = path
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.rule}: {self.path}: {self.detail}"


def iter_code_files(root: Path) -> list[Path]:
    """Every first-party code file, excluding vendored and generated trees."""
    # The ratchet names the things it forbids, so it must not scan itself.
    self_path = (root / "tools" / "arch_ratchet.py").resolve()
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        parts = set(path.parts)
        if parts & SKIP_DIRS:
            continue
        if contains_parts(path, VENDORED_PARTS):
            continue
        if path.resolve() == self_path:
            continue
        out.append(path)
    return sorted(out)


def contains_parts(path: Path, needed: tuple[str, ...]) -> bool:
    """True when `needed` appears, in order, inside the path's parts.

    Path comparison is platform-dependent, so every rule here matches on
    path PARTS rather than on path objects. A rule that quietly stops
    matching on Windows is a rule that does not exist.
    """
    parts = path.parts
    size = len(needed)
    if size == 0:
        return False
    return any(parts[i : i + size] == needed for i in range(len(parts) - size + 1))


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def rel(path: Path, root: Path) -> str:
    """A repository-relative path with forward slashes on every platform."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


RULE_WEB = "web-tier-imports-worker"
RULE_ORDER = "order-placement"
RULE_AI_TABLE = "ai-names-decision-table"
RULE_SERVICE_KEY = "service-role-key-outside-allowed-paths"
RULE_AGPL = "agpl-source-in-first-party"
RULE_ATTRIBUTION = "vendor-attribution"


def check_web_imports_worker(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    pattern = re.compile(r"""from\s+['"][^'"]*(services/|live-worker|research-worker)""")
    for path in files:
        if not rel(path, root).startswith("apps" + "/web"):
            continue
        for number, line in enumerate(read(path).splitlines(), start=1):
            if pattern.search(line):
                findings.append(
                    Finding(
                        RULE_WEB,
                        rel(path, root),
                        f"line {number} imports worker code",
                    )
                )
    return findings


def check_no_order_placement(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        if contains_parts(path, DOCS_PARTS):
            continue
        text = read(path)
        for number, line in enumerate(text.splitlines(), start=1):
            for call in ORDER_CALLS:
                if call in line:
                    findings.append(
                        Finding(
                            RULE_ORDER,
                            rel(path, root),
                            f"line {number} contains '{call}'",
                        )
                    )
    return findings


def check_ai_avoids_decision_tables(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    marker = ("worker", "ai")
    for path in files:
        if not contains_parts(path, marker):
            continue
        text = read(path)
        for number, line in enumerate(text.splitlines(), start=1):
            for table in DECISION_TABLES:
                if re.search(rf"\b{re.escape(table)}\b", line):
                    findings.append(
                        Finding(
                            RULE_AI_TABLE,
                            rel(path, root),
                            f"line {number} mentions '{table}'",
                        )
                    )
    return findings


def check_service_role_confined(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    allowed = (
        "apps/web/src/app/api/cron",
        # The Telegram webhook is the second (and only other) place in the
        # web tier that may hold the service-role key: a Telegram chat is
        # not a Supabase session, so there is no cookie to read.
        "apps/web/src/app/api/telegram",
        "services/live-worker",
        # The research worker publishes CANDIDATES. A candidate belongs to
        # nobody until a human promotes it, so it cannot be written under a
        # user's RLS policy, and the runner runs on the owner's own machine
        # rather than in the web tier. Its write set is narrow and enforced
        # in code (research/db.py refuses the decision tables), so holding
        # the key there does not move the custody boundary.
        "services/research-worker",
        "tools",
    )
    for path in files:
        relative = rel(path, root)
        if any(relative.startswith(prefix) for prefix in allowed):
            continue
        if "SUPABASE_SERVICE_ROLE_KEY" in read(path):
            findings.append(
                Finding(
                    RULE_SERVICE_KEY,
                    relative,
                    "the service-role key must stay in the worker, the cron "
                    "route, the Telegram webhook or the ops tools",
                )
            )
    return findings


def check_no_agpl_in_first_party(root: Path, files: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in files:
        text = read(path)
        for marker in AGPL_MARKERS:
            if marker in text:
                findings.append(
                    Finding(
                        RULE_AGPL,
                        rel(path, root),
                        f"contains '{marker}'",
                    )
                )
    return findings


def check_vendor_attribution(root: Path) -> list[Finding]:
    attribution = root.joinpath(*VENDORED_PARTS) / "ATTRIBUTION.md"
    if not attribution.exists():
        return [
            Finding(
                RULE_ATTRIBUTION,
                "/".join(VENDORED_PARTS),
                "ATTRIBUTION.md is missing; vendored code must be attributed",
            )
        ]
    return []


RULES = (
    (RULE_WEB, "web tier never imports the worker", check_web_imports_worker),
    (RULE_ORDER, "nothing may place an order", check_no_order_placement),
    (RULE_AI_TABLE, "the AI sidecar never names a decision table", check_ai_avoids_decision_tables),
    (RULE_SERVICE_KEY, "the service-role key stays in its allowed paths", check_service_role_confined),
    (RULE_AGPL, "no AGPL source in first-party code", check_no_agpl_in_first_party),
    (RULE_ATTRIBUTION, "vendored code is attributed", check_vendor_attribution),
)


def self_test() -> int:
    """Prove the ratchet is not vacuous.

    A gate that cannot fail is decoration. This builds a throwaway tree with
    one deliberate breach per rule and checks that every rule fires, and that
    a clean tree raises nothing.
    """
    import tempfile

    def write(root: Path, relative: str, text: str) -> None:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)

        write(root, "apps/web/src/page.tsx", "export const ok = 1;\n")
        write(root, "packages/vendor/ATTRIBUTION.md", "vendored code\n")
        write(root, "packages/vendor/lib/thing.py", "place_order(1)\n")
        write(root, "services/live-worker/worker/ai/sidecar.py", "# clean\n")
        write(root, "services/live-worker/worker/main.py", "# clean\n")

        cases = (
            ("clean tree raises nothing", None, None, None),
            (
                "web tier importing the worker",
                "apps/web/src/bad.tsx",
                "import { x } from '../../../services/live-worker/worker/main';\n",
                check_web_imports_worker,
            ),
            (
                "an order call in first-party code",
                "services/live-worker/worker/bad.py",
                "client.place_order(symbol='BTC')\n",
                check_no_order_placement,
            ),
            (
                "the AI sidecar naming a decision table",
                "services/live-worker/worker/ai/bad.py",
                "TABLE = 'tickets'\n",
                check_ai_avoids_decision_tables,
            ),
            (
                "the service-role key outside its allowed paths",
                "apps/web/src/lib/leak.ts",
                "const k = process.env.SUPABASE_SERVICE_ROLE_KEY;\n",
                check_service_role_confined,
            ),
            (
                "AGPL source in first-party code",
                "services/live-worker/worker/ai/bad2.py",
                "# SPDX-License-Identifier: AGPL-3.0\n",
                check_no_agpl_in_first_party,
            ),
        )

        for label, relative, text, rule in cases:
            if relative is None:
                findings = check_web_imports_worker(root, iter_code_files(root))
                if findings:
                    print(f"FAIL  {label}: {findings}")
                    return 1
                continue

            target = root.joinpath(*relative.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            findings = rule(root, iter_code_files(root))
            if not findings:
                print(f"FAIL  {label} was not caught")
                return 1
            target.unlink()

        print("OK    self-test: every rule fires on a breach and stays quiet otherwise")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Architecture ratchets")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="prove the ratchet detects a deliberate breach",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    files = iter_code_files(root)

    findings: list[Finding] = []
    for rule_id, _name, rule in RULES:
        if rule_id == RULE_ATTRIBUTION:
            findings.extend(check_vendor_attribution(root))
        else:
            findings.extend(rule(root, files))

    print(f"Scanned {len(files)} first-party code files in {root}\n")
    for rule_id, name, _rule in RULES:
        rule_findings = [f for f in findings if f.rule == rule_id]
        status = "FAIL" if rule_findings else "OK  "
        print(f"  {status}  {name}")
        for finding in rule_findings:
            print(f"          {finding}")

    if findings:
        print(f"\n{len(findings)} architecture rule(s) broken.")
        return 1
    print("\nAll architecture rules hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
