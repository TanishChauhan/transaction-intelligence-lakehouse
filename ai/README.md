# Optional AI: Natural-Language → SQL Agent

> **Status: optional portfolio showcase.** This component is **not** part of the
> core Transaction Intelligence Lakehouse pipeline and is **not** run in CI. It
> lives entirely under `ai/` with its own isolated dependencies
> (`ai/requirements.txt`) so the core project (`pyproject.toml`, dbt models,
> tests) is completely unaffected.

## What it is

A small [LangGraph](https://langchain-ai.github.io/langgraph/) agent that turns a
plain-English question into a **single, read-only `SELECT`** against the curated
**gold** layer, validates it, runs it on a Databricks SQL warehouse, and
optionally summarises the result in natural language.

The agent grounds the LLM in the real warehouse shape using
[`schema_context.py`](./schema_context.py), which is the single source of truth
for the gold tables and their columns (kept in sync with `dbt/models/gold/*.sql`).

### Graph

```
generate_sql ──▶ validate_sql ──(valid)──▶ execute_sql ──▶ summarize ──▶ END
                      │
                      └────────────(invalid)──────────────────────────▶ END
```

| Node           | Responsibility                                                        |
| -------------- | --------------------------------------------------------------------- |
| `generate_sql` | LLM produces a single `SELECT` from the question + gold schema prompt |
| `validate_sql` | **Guardrails** (see below) — pure, no network                         |
| `execute_sql`  | Runs the validated SQL via `databricks-sql-connector`                 |
| `summarize`    | LLM turns the returned rows into a short NL answer                    |

## Guardrails

The `validate_sql` node (`_validate_sql_text`, a pure, network-free function)
enforces the rules below before any query touches the warehouse. Critically, all
structural checks run against a **scrubbed copy** of the SQL while the original,
unscrubbed statement is what actually executes — so comments and literals can
never satisfy *or* smuggle past a check.

- **Comment & literal scrubbing (first).** Before any check, the validator
  removes `/* … */` block comments, `-- …` line comments, single-quoted string
  literals (handling `''` escapes), and double-quoted / backtick-quoted
  identifier contents, replacing each with an inert placeholder. This neutralises
  bypasses such as a fake `'gold.x'` string literal or a table name hidden in a
  trailing comment. Matching is done on the lowercased scrubbed text.
- **Single statement.** After stripping trailing whitespace and semicolons, any
  remaining `;` in the scrubbed body is rejected as a second statement.
- **`SELECT`/`WITH` only.** The scrubbed statement must begin with `SELECT` or
  `WITH`.
- **Read-only.** Any DML/DDL keyword is rejected via word-boundary match on the
  scrubbed text: `insert`, `update`, `delete`, `drop`, `truncate`, `merge`,
  `alter`, `create`, `replace`, `grant`, `revoke`, `call`, `use`, `set`, `copy`,
  `refresh`, `optimize`, `vacuum`, `analyze`, `msck`, `restore`, `undrop`,
  `export`, `load`, `install`, `comment`, `describe`. Because matching is on the
  scrubbed text and on word boundaries, a column such as `delete_flag` does **not**
  false-trigger.
- **No set operators.** `UNION`, `INTERSECT` and `EXCEPT` are disallowed in v1.
  This closes the UNION-exfiltration class, where one gold branch passes
  validation while a second branch reads non-gold data.
- **Deny-listed sources.** References to catalog/metadata browsing
  (`information_schema`, the `system.` catalog) and file-based readers
  (`read_files`, `cloud_files`, and the `delta.`/`csv.`/`json.`/`parquet.`/`text.`
  path notations) are rejected outright.
- **Gold-only qualified references.** Every dotted identifier in the scrubbed SQL
  is inspected: a three-part `a.b.c` must have schema part `b == gold`, and a
  two-part `a.b` must have `a == gold`. Anything else (e.g.
  `other_catalog.silver.pii`, `system.x.y`) is rejected. Allowed shapes are
  `gold.<table>` and `<catalog>.gold.<table>`.
- **Must touch gold.** Finally, the query must reference the `gold` schema or a
  known gold table (`dim_customer`, `dim_merchant`, `fct_transaction`,
  `fraud_signals`, `customer_spend_daily`, `merchant_risk_daily`). Silver/bronze
  and the validation-only `is_fraud_label` are out of scope.

If validation fails, the graph routes straight to the end with an error message
and **never executes** anything.

## Setup

```bash
# 1. Install the OPTIONAL extras (isolated from the core project).
pip install -r ai/requirements.txt

# 2. Point the agent at your Databricks SQL warehouse.
export DATABRICKS_HOST="https://<workspace>.cloud.databricks.com"
export DATABRICKS_HTTP_PATH="/sql/1.0/warehouses/<warehouse-id>"
export DATABRICKS_TOKEN="<your-personal-access-token>"

# 3. Configure the LLM (langchain-openai by default).
export OPENAI_API_KEY="<your-llm-key>"
export NL2SQL_MODEL="gpt-4o-mini"   # optional; this is the default

# 4. (Optional) override the catalog (defaults to txn_intelligence).
export TIL_CATALOG="txn_intelligence"
```

All configuration is read from environment variables — **no secrets are
hardcoded** anywhere in the module.

## Usage

### CLI

```bash
python ai/nl_to_sql_agent.py "What is the overall flagged-fraud rate?"
```

This prints the generated SQL, the validation result, the returned rows, and a
short natural-language answer.

### From Python

```python
from ai.nl_to_sql_agent import run

result = run("Which merchant categories have the highest flagged rate?")
print(result["sql"])      # the generated SELECT
print(result["valid"])    # whether guardrails passed
print(result["rows"])     # result rows (list of dicts)
print(result["answer"])   # natural-language summary
```

### Inspect the schema prompt only (no deps required)

```bash
python ai/schema_context.py
```

`schema_context.py` is pure standard library and always importable.

## Import-guarding (why it compiles without the AI deps)

`nl_to_sql_agent.py` imports `langgraph`, `langchain`, the LLM client and
`databricks-sql-connector` **lazily** — inside functions, behind
`try/except ImportError` with actionable install guidance. As a result:

- `python -m py_compile ai/schema_context.py ai/nl_to_sql_agent.py` passes with
  no third-party packages installed.
- `import ai.schema_context` works with the standard library alone.
- The agent only *runs* when the optional deps **and** credentials are present;
  otherwise you get a clear `ImportError` pointing you at `ai/requirements.txt`.

## Disclaimer

This agent is an **optional showcase** demonstrating governed, guarded NL→SQL
over a tested gold layer. It is decoupled from the production pipeline and CI.
Fraud signals it queries are **rule-based**; `is_fraud_label` is validation-only
and is never served from gold.
