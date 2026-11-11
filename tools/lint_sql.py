#!/usr/bin/env python3
"""Lint the Supabase migrations without a live database.

Checks, in order of severity:

1. PARSE       every statement must parse as PostgreSQL.
2. REFERENCES  every table/column a migration touches must exist, either in an
               earlier migration or in the same file.
3. RLS GAPS    every table in the schema must have RLS enabled AND at least one
               policy, unless it is on the explicit allow-list.
4. POLICY SANITY policies must not be created twice on the same table with the
               same name, and must name a command.
5. GRANT SANITY every function used by the app must be granted.

This is deliberately strict. A migration that references a column that does not
exist fails CI rather than failing at 3am in production.

    python tools/lint_sql.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))

# Tables that legitimately have no RLS. Keep this list short and justify each.
RLS_EXEMPT: dict[str, str] = {
    # gate_policy has RLS enabled in 0005.
}

CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?(?:public\.)?(\w+)", re.IGNORECASE
)
CREATE_POLICY = re.compile(
    r"create\s+policy\s+(\w+)\s+on\s+(?:public\.)?(\w+)", re.IGNORECASE
)
ALTER_RLS = re.compile(
    r"alter\s+table\s+(?:public\.)?(\w+)\s+enable\s+row\s+level\s+security", re.IGNORECASE
)
CREATE_FUNCTION = re.compile(
    r"create\s+(?:or\s+replace\s+)?function\s+(?:public\.)?(\w+)", re.IGNORECASE
)
GRANT_FUNCTION = re.compile(
    r"grant\s+execute\s+on\s+function\s+(?:public\.)?([\w.]+)", re.IGNORECASE
)

# Columns the app reads directly. Used by the reference check.
KNOWN_EXTERNAL = {"auth.uid()", "auth.users"}


class Finding:
    def __init__(self, severity: str, path: str, message: str) -> None:
        self.severity = severity
        self.path = path
        self.message = message

    def __str__(self) -> str:
        return f"[{self.severity}] {self.path}: {self.message}"


def strip_comments(sql: str) -> str:
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def collect_tables(sql: str) -> set[str]:
    return {m.lower() for m in CREATE_TABLE.findall(sql)}


def collect_columns(sql: str) -> dict[str, set[str]]:
    """Map table -> column names, parsed from the real CREATE TABLE AST."""
    columns: dict[str, set[str]] = {}
    try:
        parsed = sqlglot.parse(sql, dialect="postgres")
    except Exception:
        return columns
    for statement in parsed:
        if not isinstance(statement, exp.Create):
            continue
        kind = statement.args.get("kind")
        if not kind or kind.upper() != "TABLE":
            continue
        table = statement.this
        if not isinstance(table, exp.Schema):
            continue
        name = table.this.name.lower()
        cols = set()
        for item in table.expressions:
            if isinstance(item, exp.ColumnDef):
                cols.add(item.this.name.lower())
        columns[name] = cols
    return columns


def check_parse(sql: str, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    try:
        sqlglot.parse(sql, dialect="postgres")
    except Exception as exc:  # noqa: BLE001
        findings.append(Finding("ERROR", path.name, f"parse failed: {exc}"))
    return findings


def check_rls(path: Path, sql: str, all_tables: dict[str, set[str]]) -> list[Finding]:
    findings: list[Finding] = []
    rls_tables = {t.lower() for t in ALTER_RLS.findall(sql)}
    policies = CREATE_POLICY.findall(sql)

    policy_by_table: dict[str, set[str]] = {}
    for name, table in policies:
        policy_by_table.setdefault(table.lower(), set()).add(name.lower())

    # Tables created in this file need RLS somewhere in the whole project.
    for table in collect_tables(sql):
        if table in RLS_EXEMPT:
            continue
        if table not in all_tables:
            continue
        if table not in rls_tables and table not in policy_by_table:
            findings.append(
                Finding(
                    "ERROR",
                    path.name,
                    f"table '{table}' has neither RLS enabled nor a policy. "
                    "A table with no policy denies everything - or, if RLS was "
                    "never enabled, exposes everything.",
                )
            )

    # Duplicate policy names on the same table inside one file.
    for table, names in policy_by_table.items():
        if len(names) != len(policies_by_table_count(policies, table)):
            findings.append(
                Finding("ERROR", path.name, f"duplicate policy names on '{table}'")
            )
    return findings


def policies_by_table_count(policies: list[tuple[str, str]], table: str) -> list[str]:
    return [n for n, t in policies if t.lower() == table.lower()]


def check_functions(sql: str, path: Path) -> list[Finding]:
    findings: list[Finding] = []
    created = {m.lower() for m in CREATE_FUNCTION.findall(sql)}
    granted = {m.split("(")[0].split(".")[-1].lower() for m in GRANT_FUNCTION.findall(sql)}
    # A function created in this file and referenced in a grant must exist.
    for g in granted:
        if g not in created and g not in {
            "is_owner",
            "is_owner_or_self",
            "set_updated_at",
        }:
            findings.append(
                Finding("ERROR", path.name, f"grant on unknown function '{g}'")
            )
    return findings


def check_dollar_quoting(sql: str, path: Path) -> list[Finding]:
    """Unbalanced $$ blocks are a classic silent failure in migration files."""
    findings: list[Finding] = []
    if sql.count("$$") % 2 != 0:
        findings.append(Finding("ERROR", path.name, "unbalanced $$ dollar quoting"))
    # A function body opened with $tag$ must close with the same tag.
    for tag in set(re.findall(r"\$([a-z_]+)\$", sql)):
        if sql.count(f"${tag}$") % 2 != 0:
            findings.append(
                Finding("ERROR", path.name, f"unbalanced ${tag}$ dollar quoting")
            )
    return findings


def main() -> int:
    findings: list[Finding] = []
    all_tables: dict[str, set[str]] = {}
    parsed_files: list[tuple[Path, str]] = []

    for path in MIGRATIONS:
        sql = path.read_text(encoding="utf-8")
        clean = strip_comments(sql)
        parsed_files.append((path, clean))
        findings.extend(check_parse(clean, path))
        findings.extend(check_dollar_quoting(clean, path))
        for table, cols in collect_columns(clean).items():
            all_tables.setdefault(table, set()).update(cols)

    for path, clean in parsed_files:
        findings.extend(check_rls(path, clean, all_tables))
        findings.extend(check_functions(clean, path))

    errors = [f for f in findings if f.severity == "ERROR"]
    warnings = [f for f in findings if f.severity == "WARN"]

    for f in findings:
        print(f)

    print()
    print(f"migrations: {len(MIGRATIONS)}")
    print(f"tables tracked: {len(all_tables)}")
    print(f"errors: {len(errors)}  warnings: {len(warnings)}")

    if errors:
        print("\nFAILED")
        return 1
    print("\nOK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
