"""Per-document LLM token attribution during ingestion.

The configured ``llm_model_func`` is the single chokepoint through which every
LLM call passes (entity extraction, gleaning, merge summaries, queries). It does
not, however, know *which* document it is currently serving — the call chain
(``use_llm_func_with_cache`` → ``use_llm_func``) only carries a prompt and
returns a string, dropping both the OpenAI ``usage`` payload and the doc_id.

We bridge that gap without threading new arguments through the whole pipeline:

  1. ``process_document`` (one asyncio task per document) sets a ``(workspace,
     doc_id)`` scope in a :class:`contextvars.ContextVar` at its very start.
     ContextVars are copied into child tasks at creation time, so every LLM call
     spawned while processing that document inherits the scope — and concurrent
     documents stay isolated, each in its own task context.
  2. The LLM wrapper reads the scope after each call and accumulates the usage in
     a process-local dict. Cache hits never reach the wrapper (the cache layer
     short-circuits earlier), so only real, billable tokens are counted.
  3. When the document reaches ``PROCESSED`` the pipeline flushes the accumulated
     usage to PostgreSQL (table ``LIGHTRAG_DOC_TOKEN_USAGE``) and clears it.

Accumulation runs entirely between awaits (no ``await`` inside the critical
section), so under asyncio's single-threaded model the read-modify-write is
atomic and needs no lock.
"""

from __future__ import annotations

from contextvars import Token
from dataclasses import dataclass
from typing import Any

from .utils import doc_token_scope as _current_scope
from .utils import logger

# The (workspace, doc_id) scope ContextVar lives in utils.py (imported here as
# _current_scope) so the priority limiter there can propagate it across the
# queue → worker boundary — workers are long-lived tasks whose frozen context
# would otherwise drop the scope set by process_document.


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_call_count: int = 0
    llm_model: str = ""


# Process-local accumulator, keyed by (workspace, doc_id).
_accumulator: dict[tuple[str, str], _Usage] = {}


def set_doc_scope(workspace: str, doc_id: str) -> Token:
    """Bind the current async context to a (workspace, doc_id). Returns a token
    to pass to :func:`reset_doc_scope` in a ``finally`` block."""
    return _current_scope.set((workspace or "", doc_id))


def reset_doc_scope(token: Token) -> None:
    _current_scope.reset(token)


def record_usage(usage: dict[str, Any], model: str | None = None) -> None:
    """Accumulate one LLM call's token usage against the active doc scope.

    No-op when called outside ingestion (e.g. a /query call), so the same LLM
    wrapper can serve both paths while only documents get attribution.

    ``model`` is the LLM model name serving the call; it is constant per server
    so we just keep the latest non-empty value for the document.
    """
    scope = _current_scope.get()
    if scope is None:
        return

    agg = _accumulator.get(scope)
    if agg is None:
        agg = _Usage()
        _accumulator[scope] = agg

    if model:
        agg.llm_model = model

    agg.prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
    agg.completion_tokens += int(usage.get("completion_tokens", 0) or 0)
    total = usage.get("total_tokens")
    if total:
        agg.total_tokens += int(total)
    else:
        agg.total_tokens += int(usage.get("prompt_tokens", 0) or 0) + int(
            usage.get("completion_tokens", 0) or 0
        )
    agg.llm_call_count += 1


def pop_usage(workspace: str, doc_id: str) -> _Usage | None:
    """Remove and return the accumulated usage for a document, if any."""
    return _accumulator.pop((workspace or "", doc_id), None)


async def persist_doc_usage(workspace: str, doc_id: str, db: Any) -> None:
    """Flush a finished document's accumulated usage to PostgreSQL.

    ``db`` is a ``PostgreSQLDB`` (``self.doc_status.db``). Non-PostgreSQL
    backends pass ``None`` and are silently skipped — the feature targets the
    PG deployment. Failures are logged but never abort ingestion: token
    accounting must not be able to fail a document.
    """
    usage = pop_usage(workspace, doc_id)
    if usage is None or db is None:
        return

    sql = """
        INSERT INTO LIGHTRAG_DOC_TOKEN_USAGE
            (workspace, id, prompt_tokens, completion_tokens,
             total_tokens, llm_call_count, llm_model, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT (workspace, id) DO UPDATE SET
            prompt_tokens     = EXCLUDED.prompt_tokens,
            completion_tokens = EXCLUDED.completion_tokens,
            total_tokens      = EXCLUDED.total_tokens,
            llm_call_count    = EXCLUDED.llm_call_count,
            llm_model         = EXCLUDED.llm_model,
            updated_at        = CURRENT_TIMESTAMP
    """
    data = {
        "workspace": workspace or "",
        "id": doc_id,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "llm_call_count": usage.llm_call_count,
        "llm_model": usage.llm_model,
    }
    try:
        await db.execute(sql, data, upsert=True)
        logger.info(
            f"[TokenUsage] {doc_id}: {usage.total_tokens} tokens "
            f"({usage.llm_call_count} LLM calls) persisted"
        )
    except Exception as e:  # noqa: BLE001 — accounting must never fail ingestion
        logger.warning(f"[TokenUsage] failed to persist usage for {doc_id}: {e}")
