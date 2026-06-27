"""Unit tests for the NL->SQL agent's SQL guardrails (`_validate_sql_text`).

These tests are deliberately light: they import only the pure validator and the
``GOLD_SCHEMA`` constant from :mod:`ai.nl_to_sql_agent`. That module import-guards
all heavy/optional dependencies (langgraph, langchain, the SQL connector), so
these tests run with **no network and no optional packages installed**.

The cases below pin down the four documented security bypasses (A/B/C/D) plus the
broader hardening rules, and confirm that legitimate gold queries -- including a
CTE and a query whose only "keyword" lives inside a string literal -- still pass.
"""

from __future__ import annotations

from ai.nl_to_sql_agent import GOLD_SCHEMA, _validate_sql_text


def test_gold_schema_constant_is_gold() -> None:
    assert GOLD_SCHEMA == "gold"


# ---------------------------------------------------------------------------
# ACCEPT: legitimate gold-only queries return None (no rejection reason).
# ---------------------------------------------------------------------------
def test_accepts_simple_gold_table() -> None:
    assert _validate_sql_text("select * from gold.fct_transaction") is None


def test_accepts_catalog_qualified_gold_table() -> None:
    sql = "select count(*) from txn_intelligence.gold.fraud_signals"
    assert _validate_sql_text(sql) is None


def test_accepts_unqualified_known_gold_table() -> None:
    assert _validate_sql_text("select * from fraud_signals") is None


def test_accepts_cte_query() -> None:
    sql = "with f as (select * from gold.fraud_signals) select * from f"
    assert _validate_sql_text(sql) is None


def test_accepts_keyword_inside_string_literal() -> None:
    # After scrubbing the literal is gone, so the 'delete' text cannot trigger.
    sql = "select * from gold.fct_transaction where channel = 'delete'"
    assert _validate_sql_text(sql) is None


def test_accepts_trailing_semicolon() -> None:
    assert _validate_sql_text("select * from gold.fct_transaction;") is None


# ---------------------------------------------------------------------------
# REJECT: the four documented bypasses (A/B/C/D).
# ---------------------------------------------------------------------------
def test_rejects_bypass_a_fake_gold_string_literal() -> None:
    # A: a fake `gold.` inside a string literal must not satisfy the gold check
    # while a non-gold table is actually read.
    sql = "select secret from other_catalog.silver.pii where 'gold.x' = 'gold.x'"
    assert _validate_sql_text(sql) is not None


def test_rejects_bypass_b_table_name_in_comment() -> None:
    # B: a known table name hidden in a comment must not satisfy the gold check.
    sql = "select * from system.information_schema.columns -- fct_transaction"
    assert _validate_sql_text(sql) is not None


def test_rejects_bypass_c_union_exfiltration() -> None:
    # C: one gold branch passes, the UNION branch exfiltrates non-gold data.
    sql = (
        "select customer_id from gold.fct_transaction where 1=0 "
        "union all select secret from other_catalog.silver.pii"
    )
    assert _validate_sql_text(sql) is not None


def test_rejects_bypass_d_non_gold_with_fake_string() -> None:
    # D: non-gold table read masked by a fake `gold.` string comparison.
    sql = "select * from other_catalog.silver.transactions where 'gold.'='gold.'"
    assert _validate_sql_text(sql) is not None


# ---------------------------------------------------------------------------
# REJECT: additional hardening cases.
# ---------------------------------------------------------------------------
def test_rejects_drop_statement() -> None:
    assert _validate_sql_text("drop table gold.x") is not None


def test_rejects_multiple_statements() -> None:
    assert _validate_sql_text("select 1; select 2") is not None


def test_rejects_statement_hidden_behind_comment() -> None:
    # `-- ; drop` style: a real second statement cannot be smuggled past the
    # single-statement guard by trailing it with a comment.
    sql = "select * from gold.fct_transaction; drop table gold.x -- ; drop"
    assert _validate_sql_text(sql) is not None


def test_rejects_information_schema() -> None:
    assert _validate_sql_text("select * from system.information_schema.tables") is not None


def test_rejects_non_gold_schema_reference() -> None:
    assert _validate_sql_text("select * from other_catalog.silver.pii") is not None


def test_rejects_union_all_exfiltration() -> None:
    sql = (
        "select amount from gold.fct_transaction "
        "union all select balance from other_catalog.silver.accounts"
    )
    assert _validate_sql_text(sql) is not None


def test_rejects_empty_sql() -> None:
    assert _validate_sql_text("") is not None
    assert _validate_sql_text("   ") is not None


def test_rejects_query_without_gold_reference() -> None:
    assert _validate_sql_text("select 1 as one") is not None


def test_rejects_file_based_reader() -> None:
    sql = "select * from read_files('s3://bucket/secret.csv')"
    assert _validate_sql_text(sql) is not None
