#!/usr/bin/env python
"""
Test de garde — /documents/reprocess_failed doit être workspace-aware.

Régression historique : la route capturait `rag` (closure du workspace
par défaut) et appelait `rag.apipeline_process_enqueue_documents` quel
que soit le header LIGHTRAG-WORKSPACE. Conséquence multi-tenant :
tous les clients tenants voyaient leur POST exécuté sur le workspace
`default` au lieu du leur.

Le fix est d'utiliser `await _resolve_rag(http_request)` comme le font
les autres endpoints sensibles (upload, delete_document, track_status).

Ce test ne monte pas un FastAPI complet (l'init de `lightrag.api`
charge la config CLI globale, peu adapté à un test unitaire isolé).
Il fait une inspection AST du code source de la fonction route pour
valider 3 invariants :

  1. La fonction prend bien `http_request: Request` en paramètre.
  2. Elle appelle `_resolve_rag(http_request)`.
  3. Elle n'utilise PAS la variable `rag` globale pour
     `apipeline_process_enqueue_documents`.

Si ces 3 invariants tiennent, le bug ne peut pas resurgir tel quel.
"""
import ast
import inspect
from pathlib import Path


DOC_ROUTES = (
    Path(__file__).resolve().parents[1]
    / "lightrag"
    / "api"
    / "routers"
    / "document_routes.py"
)


def _find_function(tree, name):
    """Retourne le node ast.AsyncFunctionDef pour la fonction nommée."""
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Function {name!r} not found in document_routes.py")


def _function_uses(func_node, callee_name, arg_attr=None):
    """True si la fonction appelle `callee_name(...)` (optionnellement avec
    un argument qui matche `arg_attr`)."""
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        # Cas 1 : appel direct foo(...)
        if isinstance(node.func, ast.Name) and node.func.id == callee_name:
            if arg_attr is None:
                return True
            for a in node.args:
                if isinstance(a, ast.Name) and a.id == arg_attr:
                    return True
        # Cas 2 : appel attribut obj.foo(...)
        if isinstance(node.func, ast.Attribute) and node.func.attr == callee_name:
            return True
    return False


def _attribute_call_on(func_node, base_name, attr_name):
    """True si la fonction appelle `base_name.attr_name(...)` (ex. rag.apipeline_...)."""
    for node in ast.walk(func_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == base_name
            and node.func.attr == attr_name
        ):
            return True
    return False


def test_reprocess_failed_takes_http_request_param():
    src = DOC_ROUTES.read_text()
    tree = ast.parse(src)
    fn = _find_function(tree, "reprocess_failed_documents")
    param_names = [a.arg for a in fn.args.args]
    assert (
        "http_request" in param_names
    ), f"Expected `http_request: Request` in {fn.name} signature, got {param_names}"


def test_reprocess_failed_calls_resolve_rag():
    src = DOC_ROUTES.read_text()
    tree = ast.parse(src)
    fn = _find_function(tree, "reprocess_failed_documents")
    assert _function_uses(
        fn, "_resolve_rag"
    ), f"{fn.name} must call `_resolve_rag(http_request)` to honour the LIGHTRAG-WORKSPACE header"


def test_reprocess_failed_does_not_use_global_rag_directly():
    src = DOC_ROUTES.read_text()
    tree = ast.parse(src)
    fn = _find_function(tree, "reprocess_failed_documents")
    # Le bug originel : background_tasks.add_task(rag.apipeline_process_enqueue_documents)
    # — `rag` est la closure globale. Après le fix, on doit utiliser une variable
    # locale `rag_instance` issue de `_resolve_rag(http_request)`.
    assert not _attribute_call_on(
        fn, "rag", "apipeline_process_enqueue_documents"
    ), (
        f"{fn.name} ne doit plus appeler rag.apipeline_process_enqueue_documents "
        "directement (workspace = default ignoré). Utiliser la rag_instance "
        "résolue par _resolve_rag(http_request)."
    )


def test_other_endpoints_already_use_resolve_rag_pattern():
    """Ancre : on vérifie que au moins un autre endpoint référence aussi
    _resolve_rag. Si ce pattern disparaît du module entier, c'est suspect."""
    src = DOC_ROUTES.read_text()
    assert (
        src.count("await _resolve_rag(http_request)") >= 2
    ), "Pattern `_resolve_rag(http_request)` doit exister dans plusieurs endpoints"
