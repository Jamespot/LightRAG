#!/usr/bin/env python
"""
Test : les appels LLM retry sur 503 InternalServerError (saturation Cloud Temple).

Régression : le décorateur tenacity de openai_complete_if_cache ne retraitait
QUE RateLimitError / APIConnectionError / APITimeoutError / InvalidResponseError.
Cloud Temple renvoie sa saturation en `503 InternalServerError` :

    openai.InternalServerError: Error code: 503 -
      {'error': {'message': "Saturation: Aucune instance disponible pour le
       modèle 'Qwen/Qwen3.6-27B-FP8' après 3 tentatives.", ...}}

Comme InternalServerError n'était pas dans la liste tenacity, l'exception
remontait IMMÉDIATEMENT après les 2 micro-retries du SDK openai (~6s), le
chunk échouait, puis le document entier. Un pic de saturation transitoire
(quelques secondes à ~2 min) suffisait à faire échouer une indexation.

Ces tests valident le comportement attendu après fix :
  1. Sur 503 transitoire (2 échecs puis succès) → la fonction retry et finit OK.
  2. Sur 503 persistant → la fonction retry plusieurs fois (pas 1 seule).

Le sleep de tenacity (4..64s entre tentatives) est court-circuité via le
contrôleur AsyncRetrying exposé sur `.retry` pour garder le test instantané.
"""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import InternalServerError

import lightrag.llm.openai as oa
from lightrag.llm.openai import openai_complete_if_cache


def _saturation_error():
    """Construit un vrai openai.InternalServerError comme Cloud Temple en
    saturation (HTTP 503)."""
    req = httpx.Request(
        "POST", "https://api.ai.cloud-temple.com/v1/chat/completions"
    )
    resp = httpx.Response(503, request=req)
    return InternalServerError(
        "Saturation: Aucune instance disponible pour le modèle "
        "'Qwen/Qwen3.6-27B-FP8' après 3 tentatives.",
        response=resp,
        body=None,
    )


class _Msg:
    def __init__(self, content):
        self.content = content
        self.reasoning_content = ""
        self.parsed = None


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _Resp:
    """Réponse non-streaming minimale (pas de __aiter__ → branche non-stream)."""

    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None


def _mock_client(side_effect):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=side_effect)
    client.close = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def _instant_retries(monkeypatch):
    # tenacity attend 4..64s entre tentatives ; on rend le sleep instantané
    # pour ne pas faire durer le test ~2 min. On patche le contrôleur exposé
    # par tenacity sur l'attribut `.retry` de la fonction décorée.
    monkeypatch.setattr(openai_complete_if_cache.retry, "sleep", AsyncMock())


@pytest.mark.asyncio
async def test_retries_on_503_then_succeeds(monkeypatch):
    """2 saturations puis succès → la fonction doit retry et renvoyer le contenu."""
    client = _mock_client(
        [_saturation_error(), _saturation_error(), _Resp("OK")]
    )
    monkeypatch.setattr(oa, "create_openai_async_client", lambda **kw: client)

    result = await openai_complete_if_cache(
        "qwen3.6:27b", "hi", api_key="k", base_url="http://x"
    )

    assert result == "OK"
    # 2 échecs retriés + 1 succès = 3 appels réseau
    assert client.chat.completions.create.call_count == 3


@pytest.mark.asyncio
async def test_retries_multiple_times_on_persistent_503(monkeypatch):
    """Saturation persistante → on retry plusieurs fois (régression : avant le
    fix, call_count valait 1 car InternalServerError n'était pas retrié)."""
    from tenacity import RetryError

    client = _mock_client(_saturation_error())  # lève toujours
    monkeypatch.setattr(oa, "create_openai_async_client", lambda **kw: client)

    with pytest.raises((InternalServerError, RetryError)):
        await openai_complete_if_cache(
            "qwen3.6:27b", "hi", api_key="k", base_url="http://x"
        )

    # L'essentiel : on retry nettement plus d'une fois sur le 503.
    assert client.chat.completions.create.call_count >= 4


# --- Garde-fou statique : verrouille la config des 2 décorateurs -------------
# Le test comportemental ci-dessus ne couvre que openai_complete_if_cache.
# openai_embed partage le même besoin (granite peut saturer) mais n'est pas
# exercé ici → on vérifie par inspection du source que les 2 décorateurs
# retraitent bien InternalServerError, et que le backoff est resté renforcé.
import inspect  # noqa: E402


def _decorator_blocks():
    src = inspect.getsource(oa)
    # Découpe grossière : chaque bloc @retry( ... ) jusqu'à la parenthèse de
    # fermeture suivie de la définition de fonction.
    blocks = []
    for marker in ("async def openai_complete_if_cache", "async def openai_embed"):
        idx = src.index(marker)
        start = src.rindex("@retry(", 0, idx)
        blocks.append(src[start:idx])
    return blocks


def test_internal_server_error_is_imported():
    assert "InternalServerError" in inspect.getsource(oa).split("async def")[0]


def test_both_retry_decorators_handle_internal_server_error():
    for block in _decorator_blocks():
        assert "InternalServerError" in block, (
            "Un décorateur @retry ne retraite pas InternalServerError (503) — "
            "régression : la saturation provider ferait échouer le chunk."
        )


def test_retry_budget_stays_resilient():
    for block in _decorator_blocks():
        assert "stop_after_attempt(6)" in block, (
            "Le nombre de tentatives a été réduit — le retry pourrait ne plus "
            "couvrir une saturation transitoire de ~2 min."
        )
