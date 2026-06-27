"""Optional natural-language -> SQL agent for the gold lakehouse layer.

This is a small `LangGraph <https://langchain-ai.github.io/langgraph/>`_ state
machine that turns an analyst's plain-English question into a single, read-only
``SELECT`` against the *gold* schema, validates it against strict guardrails,
executes it on a Databricks SQL warehouse and (optionally) summarises the result
in natural language.

Design notes
------------
* **Import-guarded.** All heavy/optional third-party dependencies (langgraph,
  langchain, the LLM client, databricks-sql-connector) are imported *inside*
  functions or behind ``try/except ImportError`` with actionable messages. The
  module therefore imports cleanly -- and ``python -m py_compile`` passes -- even
  when none of those packages are installed. The agent only truly *runs* when
  the deps and credentials are present.
* **Read-only & gold-only.** Generated SQL is rejected unless it is a single
  ``SELECT``/``WITH`` statement that references only known gold tables. Any DML/
  DDL keyword aborts execution.
* **No hardcoded secrets.** Model name and warehouse connection are read from
  environment variables.

This component is an *optional showcase*. It is intentionally isolated from the
core dbt pipeline and is not part of CI.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Dict, List, Optional, TypedDict

try:
    # When imported as part of the ``ai`` package (e.g. ``import ai.nl_to_sql_agent``).
    from .schema_context import (
        DEFAULT_CATALOG,
        GOLD_SCHEMA,
        build_schema_prompt,
        table_names,
    )
except ImportError:  # pragma: no cover - fallback for running as a script from ai/.
    from schema_context import (
        DEFAULT_CATALOG,
        GOLD_SCHEMA,
        build_schema_prompt,
        table_names,
    )

# ---------------------------------------------------------------------------
# Configuration (env-driven, never hardcode secrets).
# ---------------------------------------------------------------------------
#: Chat model name used for SQL generation / summarisation.
LLM_MODEL_ENV = "NL2SQL_MODEL"
DEFAULT_LLM_MODEL = "gpt-4o-mini"

#: Databricks SQL warehouse connection (used by databricks-sql-connector).
DB_HOST_ENV = "DATABRICKS_HOST"
DB_HTTP_PATH_ENV = "DATABRICKS_HTTP_PATH"
DB_TOKEN_ENV = "DATABRICKS_TOKEN"

#: Hard cap on rows fetched back from the warehouse to keep responses small.
MAX_RESULT_ROWS = 200

# ---------------------------------------------------------------------------
# Guardrail configuration.
# ---------------------------------------------------------------------------
#: Statements must begin with one of these (case-insensitive) tokens.
_ALLOWED_LEADING = ("select", "with")

#: Any of these tokens anywhere in the statement aborts execution. Matched on
#: word boundaries against the *scrubbed* SQL (comments/string-literals removed)
#: so a column named e.g. ``delete_flag`` does not false-trigger ``delete``.
_FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "truncate",
    "merge",
    "alter",
    "create",
    "replace",
    "grant",
    "revoke",
    "call",
    "use",
    "set",
    "copy",
    "refresh",
    "optimize",
    "vacuum",
    "analyze",
    "msck",
    "restore",
    "undrop",
    "export",
    "load",
    "install",
    "comment",
    "describe",
)

#: Set operators are disallowed in v1: they enable UNION-style exfiltration where
#: one gold branch passes validation while a second branch reads non-gold data.
_FORBIDDEN_SET_OPERATORS = ("union", "intersect", "except")

#: Explicit deny-list (regexes, matched against the scrubbed SQL). These target
#: catalog/metadata browsing (``information_schema``, ``system.``) and file-based
#: reads (``read_files``/``cloud_files`` and ``delta.``/``csv.``/``json.``/
#: ``parquet.``/``text.`` path notations) that bypass the gold tables entirely.
_DENY_PATTERNS = (
    r"\binformation_schema\b",
    r"\bsystem\.",
    r"\bread_files\b",
    r"\bcloud_files\b",
    r"\bdelta\.",
    r"\bcsv\.",
    r"\bjson\.",
    r"\bparquet\.",
    r"\btext\.",
)

#: Matches a dotted identifier reference such as ``gold.fct_transaction`` or
#: ``catalog.gold.table``. Each part must start with a letter/underscore so that
#: numeric literals like ``1.5`` are never mistaken for schema-qualified names.
_DOTTED_REF_RE = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+")

#: Placeholder substituted for every scrubbed comment / string literal / quoted
#: identifier. It is an inert identifier token: it carries no dots and matches no
#: keyword, gold table name or deny pattern.
_SCRUB_PLACEHOLDER = "x"


class AgentState(TypedDict, total=False):
    """Mutable state threaded through the LangGraph nodes."""

    question: str
    schema_prompt: str
    sql: str
    valid: bool
    error: Optional[str]
    rows: List[Dict[str, Any]]
    columns: List[str]
    answer: Optional[str]


# ---------------------------------------------------------------------------
# Optional-dependency helpers. Each loader raises a friendly ImportError so a
# user who runs the agent without installing ai/requirements.txt gets a clear
# instruction rather than a bare traceback.
# ---------------------------------------------------------------------------
def _require(package_label: str, exc: ImportError) -> "ImportError":
    return ImportError(
        f"The optional dependency '{package_label}' is required to run the NL->SQL "
        f"agent but is not installed. Install the optional AI extras with:\n"
        f"    pip install -r ai/requirements.txt\n"
        f"Original import error: {exc}"
    )


def _load_chat_model(model: str) -> Any:
    """Instantiate a LangChain chat model. Imported lazily.

    Uses ``langchain-openai`` by default; swap in any LangChain-compatible chat
    model if you prefer a different provider.
    """
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - exercised only without deps
        raise _require("langchain-openai", exc)
    return ChatOpenAI(model=model, temperature=0)


# ---------------------------------------------------------------------------
# Prompt building.
# ---------------------------------------------------------------------------
_SQL_SYSTEM_PROMPT = (
    "You are a careful analytics engineer. Translate the user's question into a "
    "SINGLE read-only SQL query for a Databricks SQL warehouse.\n"
    "Rules:\n"
    "  - Output ONLY the SQL, with no markdown fences and no commentary.\n"
    "  - The statement MUST be a single SELECT (it may start with a WITH clause).\n"
    "  - Reference ONLY the gold tables described below, using their fully-"
    "qualified names.\n"
    "  - Never use INSERT/UPDATE/DELETE/DROP/MERGE/ALTER or any other DML/DDL.\n"
    "  - Prefer explicit column lists and add a sensible LIMIT for exploratory "
    "questions.\n\n"
    "Schema:\n"
    "{schema_prompt}\n"
)

_SUMMARY_SYSTEM_PROMPT = (
    "You are a data analyst. Given a user's question, the SQL that was run and "
    "the resulting rows (as JSON), write a concise, factual natural-language "
    "answer. Do not invent numbers that are not in the rows."
)


def _strip_sql_fences(text: str) -> str:
    """Remove markdown code fences and surrounding whitespace from model output."""
    cleaned = text.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", cleaned, flags=re.IGNORECASE | re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    return cleaned


# ---------------------------------------------------------------------------
# Node implementations.
# ---------------------------------------------------------------------------
def generate_sql(state: AgentState) -> AgentState:
    """LLM node: produce a single SELECT from the question + schema prompt."""
    model_name = os.environ.get(LLM_MODEL_ENV, DEFAULT_LLM_MODEL)
    llm = _load_chat_model(model_name)

    schema_prompt = state.get("schema_prompt") or build_schema_prompt()
    messages = [
        {"role": "system", "content": _SQL_SYSTEM_PROMPT.format(schema_prompt=schema_prompt)},
        {"role": "user", "content": state["question"]},
    ]
    response = llm.invoke(messages)
    raw = getattr(response, "content", str(response))
    state["schema_prompt"] = schema_prompt
    state["sql"] = _strip_sql_fences(raw)
    return state


def validate_sql(state: AgentState) -> AgentState:
    """Guardrail node: enforce single, read-only, gold-only SELECT.

    Sets ``state['valid']`` and, on failure, a human-readable ``state['error']``.
    """
    sql = (state.get("sql") or "").strip()
    error = _validate_sql_text(sql)
    state["valid"] = error is None
    state["error"] = error
    return state


def _scrub_sql(sql: str) -> str:
    """Return an analysis-only copy of ``sql`` with injection vectors neutralised.

    A single left-to-right scan removes the content that an attacker can use to
    smuggle table/keyword text past structural checks, replacing each with an
    inert placeholder so token boundaries are preserved:

    * ``/* ... */`` block comments,
    * ``-- ...`` line comments,
    * single-quoted string literals (``''`` escapes handled),
    * double-quoted and backtick-quoted identifier contents.

    The ORIGINAL ``sql`` is never mutated -- only this scrubbed string is used for
    matching; the unscrubbed statement still flows to execution unchanged.
    """
    out: List[str] = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        pair = sql[i : i + 2]

        if pair == "/*":  # block comment -> drop to closing */ (or end).
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            out.append(" ")
            continue

        if pair == "--":  # line comment -> drop to end of line.
            end = sql.find("\n", i + 2)
            i = n if end == -1 else end
            out.append(" ")
            continue

        if ch == "'":  # single-quoted string literal (with '' escapes).
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(_SCRUB_PLACEHOLDER)
            continue

        if ch == '"':  # double-quoted identifier (with "" escapes).
            i += 1
            while i < n:
                if sql[i] == '"':
                    if i + 1 < n and sql[i + 1] == '"':
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(_SCRUB_PLACEHOLDER)
            continue

        if ch == "`":  # backtick-quoted identifier.
            i += 1
            while i < n:
                if sql[i] == "`":
                    i += 1
                    break
                i += 1
            out.append(_SCRUB_PLACEHOLDER)
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _validate_sql_text(sql: str) -> Optional[str]:
    """Return ``None`` if ``sql`` passes all guardrails, else an error string.

    Pure function (no LLM, no network) so the guardrails are independently
    testable. All structural checks run against a *scrubbed*, lowercased copy of
    the SQL (see :func:`_scrub_sql`) so that comments and string/identifier
    literals can never satisfy -- or smuggle past -- a check. The original,
    unscrubbed ``sql`` is what actually gets executed.

    Enforces, in order:

    #. non-empty input,
    #. a single statement (no semicolon survives in the scrubbed body),
    #. leading token is ``select`` or ``with``,
    #. no forbidden DML/DDL keywords (word-boundary match on the scrubbed SQL),
    #. no set operators (``union``/``intersect``/``except``) -- a v1 limitation
       that closes the UNION-exfiltration class,
    #. no deny-listed references (``information_schema``, ``system.`` catalog,
       file-based readers such as ``read_files``/``delta.``/``csv.`` ...),
    #. every dotted reference is gold-only: ``a.b.c`` requires ``b == 'gold'`` and
       ``a.b`` requires ``a == 'gold'`` (blocks ``other_catalog.silver.x`` etc.),
    #. at least one real reference to the gold schema or a known gold table.
    """
    if not sql or not sql.strip():
        return "Empty SQL was produced."

    # 1. Scrub injection vectors, then lowercase for all matching below.
    scrubbed = _scrub_sql(sql).lower()

    # 2. Single statement: strip trailing whitespace/semicolons; any remaining
    #    semicolon means a second statement is present.
    body = scrubbed.rstrip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        return "Multiple statements are not allowed (only a single SELECT)."

    # 3. Leading token must be SELECT or WITH.
    leading = body.lstrip()
    if not leading.startswith(_ALLOWED_LEADING):
        return "Only single SELECT/WITH statements are allowed."

    # 4. Forbidden DML/DDL keywords (word-boundary match on the scrubbed SQL).
    for keyword in _FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", scrubbed):
            return f"Forbidden keyword '{keyword}' detected; only read-only SELECT " "is permitted."

    # 5. Set operators enable UNION-style exfiltration -- disallowed in v1.
    for operator in _FORBIDDEN_SET_OPERATORS:
        if re.search(rf"\b{re.escape(operator)}\b", scrubbed):
            return (
                f"Set operator '{operator}' is not allowed (v1 limitation); it can "
                "be used to exfiltrate non-gold data via a second branch."
            )

    # 6. Explicit deny-list: catalog/metadata browsing and file-based readers.
    for pattern in _DENY_PATTERNS:
        if re.search(pattern, scrubbed):
            return (
                "Reference to a deny-listed source detected "
                f"(matched '{pattern}'); only gold tables may be queried."
            )

    # 7. Gold-only qualified references: inspect every dotted identifier and
    #    require its schema component to be the gold schema.
    for ref in _DOTTED_REF_RE.findall(scrubbed):
        parts = ref.split(".")
        schema_part = parts[0] if len(parts) == 2 else parts[1]
        if schema_part != GOLD_SCHEMA:
            return (
                f"Reference '{ref}' is outside the '{GOLD_SCHEMA}' schema; only "
                f"'{GOLD_SCHEMA}.<table>' or '<catalog>.{GOLD_SCHEMA}.<table>' is "
                "allowed."
            )

    # 8. Must touch gold: either the gold schema prefix or a known gold table.
    references_gold_schema = f"{GOLD_SCHEMA}." in scrubbed
    references_known_table = any(
        re.search(rf"\b{re.escape(name)}\b", scrubbed) for name in table_names()
    )
    if not (references_gold_schema or references_known_table):
        return (
            "Query does not reference the 'gold' schema or any known gold table; "
            "refusing to execute."
        )

    return None


def execute_sql(state: AgentState) -> AgentState:
    """Execution node: run the validated SQL on a Databricks SQL warehouse.

    Connection parameters come from ``DATABRICKS_HOST``/``DATABRICKS_HTTP_PATH``/
    ``DATABRICKS_TOKEN``. The connector dependency is imported lazily.
    """
    try:
        from databricks import sql as databricks_sql
    except ImportError as exc:  # pragma: no cover - exercised only without deps
        raise _require("databricks-sql-connector", exc)

    host = os.environ.get(DB_HOST_ENV)
    http_path = os.environ.get(DB_HTTP_PATH_ENV)
    token = os.environ.get(DB_TOKEN_ENV)
    missing = [
        name
        for name, value in (
            (DB_HOST_ENV, host),
            (DB_HTTP_PATH_ENV, http_path),
            (DB_TOKEN_ENV, token),
        )
        if not value
    ]
    if missing:
        raise EnvironmentError("Missing Databricks SQL warehouse env var(s): " + ", ".join(missing))

    server_hostname = host.replace("https://", "").replace("http://", "").rstrip("/")

    with databricks_sql.connect(
        server_hostname=server_hostname,
        http_path=http_path,
        access_token=token,
        catalog=DEFAULT_CATALOG,
        schema=GOLD_SCHEMA,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(state["sql"])
            description = cursor.description or []
            columns = [col[0] for col in description]
            fetched = cursor.fetchmany(MAX_RESULT_ROWS)

    rows = [dict(zip(columns, row)) for row in fetched]
    state["columns"] = columns
    state["rows"] = rows
    return state


def summarize(state: AgentState) -> AgentState:
    """Optional LLM node: turn result rows into a short natural-language answer."""
    rows = state.get("rows", [])
    if not rows:
        state["answer"] = "The query returned no rows."
        return state

    model_name = os.environ.get(LLM_MODEL_ENV, DEFAULT_LLM_MODEL)
    llm = _load_chat_model(model_name)

    import json

    preview = rows[:50]
    messages = [
        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {state['question']}\n\n"
                f"SQL:\n{state['sql']}\n\n"
                f"Rows (JSON, up to 50 shown):\n{json.dumps(preview, default=str)}"
            ),
        },
    ]
    response = llm.invoke(messages)
    state["answer"] = getattr(response, "content", str(response))
    return state


# ---------------------------------------------------------------------------
# Graph wiring.
# ---------------------------------------------------------------------------
def _route_after_validation(state: AgentState) -> str:
    """Conditional edge: continue to execution when valid, else go to the error end."""
    return "execute_sql" if state.get("valid") else "error"


def build_agent() -> Any:
    """Build and compile the LangGraph NL->SQL agent.

    The graph is::

        generate_sql -> validate_sql -> (valid?) -> execute_sql -> summarize -> END
                                      \\-> (invalid) -------------------------> END

    Returns:
        A compiled LangGraph application exposing ``.invoke(state)``.

    Raises:
        ImportError: if ``langgraph`` is not installed (with install guidance).
    """
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:  # pragma: no cover - exercised only without deps
        raise _require("langgraph", exc)

    graph = StateGraph(AgentState)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate_sql", validate_sql)
    graph.add_node("execute_sql", execute_sql)
    graph.add_node("summarize", summarize)

    graph.set_entry_point("generate_sql")
    graph.add_edge("generate_sql", "validate_sql")
    graph.add_conditional_edges(
        "validate_sql",
        _route_after_validation,
        {"execute_sql": "execute_sql", "error": END},
    )
    graph.add_edge("execute_sql", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile()


def run(question: str) -> Dict[str, Any]:
    """Convenience entry point: build the agent and answer a single question.

    Args:
        question: The analyst's natural-language question.

    Returns:
        The final agent state as a plain dict, including ``sql``, ``valid``,
        ``error`` (if any), ``rows``/``columns`` and a natural-language
        ``answer``.
    """
    agent = build_agent()
    initial: AgentState = {
        "question": question,
        "schema_prompt": build_schema_prompt(),
    }
    result = agent.invoke(initial)
    return dict(result)


def _main(argv: List[str]) -> int:
    """CLI entry point: read a question from argv, print the SQL and result."""
    if len(argv) < 2:
        print('Usage: python ai/nl_to_sql_agent.py "your question here"', file=sys.stderr)
        return 2

    question = " ".join(argv[1:])
    result = run(question)

    print("Question:", question)
    print("\nGenerated SQL:\n" + (result.get("sql") or "<none>"))

    if not result.get("valid"):
        print("\nValidation failed:", result.get("error"))
        return 1

    print("\nColumns:", result.get("columns"))
    rows = result.get("rows", [])
    print(f"\nRows ({len(rows)} shown):")
    for row in rows:
        print(row)

    if result.get("answer"):
        print("\nAnswer:\n" + result["answer"])

    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
