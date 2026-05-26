# Fork Jamespot/LightRAG — notes

Fork de [`hkuds/lightrag`](https://github.com/hkuds/lightrag) maintenu par Jamespot pour ajouter le routage multi-workspace **par requête HTTP** au serveur officiel.

Le besoin métier : permettre à un service Rails (SafeBrain) d'isoler ses Knowledge Bases dans des workspaces LightRAG distincts via un simple header HTTP, sans devoir démarrer une instance LightRAG par KB.

Branche de référence : [`feat/multi-workspace-http`](https://github.com/Jamespot/LightRAG/tree/feat/multi-workspace-http) (forkée de `main` @ `3bf2297d`).

---

## Patches appliqués

### 1. Multi-workspace HTTP routing (`2d3012ed`)

Cible le bug upstream [#2904](https://github.com/HKUDS/LightRAG/issues/2904) : *« LIGHTRAG-WORKSPACE header ignored in /query »*.

LightRAG officiel 1.4.x lit déjà le header `LIGHTRAG-WORKSPACE` via `get_workspace_from_request` (`lightrag_server.py:489`), mais ce helper n'est câblé que sur `/health`. Toutes les routes data (`/query`, `/documents/upload`, etc.) utilisent l'instance globale `rag` créée au démarrage du serveur avec le workspace de l'env `WORKSPACE`.

**Changements** (`lightrag/api/lightrag_server.py` + 2 routers) :

- Pool global `_rag_pool: dict[str, LightRAG]` + `_rag_pool_lock` asyncio
- Helper `get_rag_for_workspace(ws)` qui crée à la volée une instance `LightRAG` par workspace et la mémorise
- Dependency FastAPI `require_workspace_rag(request)` qui :
  - lit le header `LIGHTRAG-WORKSPACE`
  - **raise 400** si manquant (enforcement strict, pas de fallback silencieux sur `default`)
- Factories `create_document_routes` et `create_query_routes` acceptent un kwarg optionnel `workspace_resolver` (backward-compatible : sans `workspace_resolver`, comportement upstream préservé)
- Routes câblées : `/query`, `/query/stream`, `/query/data`, `/documents/upload`, `/documents/track_status/{id}`, `/documents/delete_document`

Le storage Postgres (`PGKVStorage`, `PGVectorStorage`, `PGDocStatusStorage`) est déjà nativement scopé par workspace (colonne `workspace` + index composites), donc l'isolation des données est garantie automatiquement dès qu'on instancie une `LightRAG` par workspace.

### 2. Isolation filesystem upload (`0188a443`)

Sans ce patch, deux KBs qui uploadent un fichier portant le même nom (`rapport.pdf`, `cv.pdf`…) se bloquent mutuellement via le check `file_path.exists()` dans `upload_to_input_dir`. Le `doc_status` est pourtant déjà scopé par workspace via `rag_instance.doc_status`.

**Changement** (`lightrag/api/routers/document_routes.py:2244`) :

```python
workspace_dir = doc_manager.base_input_dir / rag_instance.workspace
workspace_dir.mkdir(parents=True, exist_ok=True)
file_path = workspace_dir / safe_filename
```

Le `safe_filename` (basename) reste inchangé côté KG / references — aucune pollution du `file_path` présenté au LLM dans les réponses de query.

---

## Limitations connues (non bloquantes pour SafeBrain)

### Routes encore scopées sur le workspace par défaut

Le patch HTTP route les endpoints `data` (`/upload`, `/query*`, `/track_status`, `/delete_document`). Les routes suivantes utilisent encore le `rag` ou le `doc_manager` global :

- `/documents/scan`
- `/documents/clear_documents`
- `/documents/clear_cache`
- `/documents/clear_entity_extract_cache`
- `/documents/text`, `/documents/texts` (insertion texte brut sans fichier)
- `/documents/file`, `/documents/file_batch` (variantes upload moins utilisées)
- `/documents/reprocess_failed`
- `/documents/cancel_pipeline`

SafeBrain n'appelle aucune de ces routes — pas d'impact.

**Pour la WebUI admin** : si tu cliques sur "Scan" ou "Clear" dans la WebUI LightRAG officielle, ces actions opèrent sur le workspace par défaut (`WORKSPACE` env), **pas** sur le workspace que tu visualises. Bug d'UX admin connu, non corrigé pour limiter le diff upstream.

### Glob non récursif sur `input_dir`

Deux call-sites scannent `input_dir` sans récursivité :

- `DocumentManager.scan_directory_for_new_files` (`document_routes.py:866-874`) : `input_dir.glob(f"*{ext}")`
- `clear_documents` background task (`document_routes.py:2621`) : `input_dir.glob("*")` + `is_file()` (intentionnel, commentaire ligne 2617 *« preserve files in subdirectories »*)

Conséquence : les fichiers uploadés via notre patch (placés dans `base_input_dir/<workspace>/`) sont invisibles à ces deux flux. Pas d'impact sur SafeBrain (jamais utilisés), mais à savoir pour qui ferait du scan filesystem.

### Pool RAG non borné (memory creep)

`_rag_pool` n'a ni cap ni LRU. Chaque workspace utilisé reste en mémoire jusqu'au shutdown du serveur. Une `LightRAG` instance consomme ~100 Mo (pools de connexions PG + caches LLM + worker threads).

**Estimation** : ~50 workspaces actifs simultanés ≈ 5 Go RAM. Acceptable sur un dyno Scalingo standard. Au-delà, OOM possible.

**Mitigations** :
- **V1 (zero-code)** : cron de restart du dyno chaque nuit → vide le pool, recharge lazy au premier query d'une KB.
- **V2 (futur)** : ajouter un `OrderedDict` + cap `LIGHTRAG_POOL_MAX` + éviction LRU avec `await evicted.finalize_storages()`. ~30 lignes.

### Sanitization workspace permissive

`get_workspace_from_request` accepte tout header, sanitize via `re.sub(r"[^a-zA-Z0-9_]", "_", workspace)`. Un client API hostile pourrait créer N workspaces arbitraires et hold autant d'instances dans le pool.

**Mitigation côté SafeBrain** : le serveur LightRAG est joignable uniquement via Scalingo Private Network (le service n'écoute pas sur l'internet public). Seul l'app Rails peut taper dessus, et elle envoie systématiquement `kb_<id>` strict.

**Fix propre** (futur) : valider strictement `^kb_[0-9]+$` au lieu de la sanitization permissive.

---

## Build & déploiement

### Dev local (SafeBrain)

Le `docker-compose.yml` de SafeBrain monte directement le code Python du fork dans le container officiel pour itérer sans rebuild :

```yaml
lightrag:
  image: ghcr.io/hkuds/lightrag:latest
  volumes:
    - ../lightrag/lightrag:/app/lightrag:ro
    - lightrag_data:/app/data
```

Tout changement dans le fork → `docker compose restart lightrag` suffit.

### Recette / prod (Scalingo)

À configurer (TODO) : app Scalingo `lightrag-safebrain-recette` avec buildpack Python pointé sur le fork :

```
$ scalingo --app lightrag-safebrain-recette env-set \
    BUILDPACK_URL=https://github.com/Scalingo/python-buildpack \
    LIGHTRAG_REPO=https://github.com/Jamespot/LightRAG \
    LIGHTRAG_BRANCH=feat/multi-workspace-http
```

Procfile dédié à fournir dans le fork (à faire).

---

## Roadmap upstream

Le patch est conçu pour être upstream-friendly :
- Backward compatible (le kwarg `workspace_resolver` est optionnel)
- Touche peu de fichiers (~150 lignes sur 3 fichiers Python)
- Adresse un bug upstream existant et documenté (#2904)

PR upstream envisageable une fois la branche stabilisée en prod chez Jamespot.

---

## Tests E2E (depuis le repo SafeBrain)

```bash
# Validation end-to-end basique
bin/rails runner scripts/validate_lightrag_e2e.rb

# Isolation multi-workspace (vraie preuve que le routing HTTP marche)
bin/rails runner scripts/validate_lightrag_isolation.rb

# Collision filename cross-KB (valide le patch 0188a443)
bin/rails runner scripts/validate_lightrag_filename_collision.rb

# Bout en bout user-facing : KB → source → bot → chat → LLM
bin/rails runner scripts/validate_lightrag_chat_with_bot.rb
```

Tous doivent passer avec ce fork déployé en local (port 9621) + `CLOUD_TEMPLE_API_KEY` exporté.
