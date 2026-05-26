# Déploiement Scalingo — `lightrag-safebrain-recette`

App cible : https://dashboard.scalingo.com/apps/osc-fr1/lightrag-safebrain-recette

Stack : buildpack Python (Scalingo détecte automatiquement via `requirements.txt`).
Branche source : `feat/multi-workspace-http` du repo `Jamespot/LightRAG`.

---

## 1. Pré-requis

- CLI Scalingo installé + logué (`scalingo login`)
- App `lightrag-safebrain-recette` créée dans la région `osc-fr1`
- Addon PostgreSQL (avec extension `pgvector`) attaché à l'app — voir §3
- Clé Cloud Temple valide (la même que SafeBrain utilise pour les LLM)

## 2. Première mise en place (à faire une seule fois)

### 2.1 Pointer Scalingo sur le fork

Depuis le clone local du fork :

```bash
cd ~/Projets/jamespot/lightrag   # ou ton chemin
git remote add scalingo git@scalingo.com:lightrag-safebrain-recette.git
```

### 2.2 Vérifier les fichiers buildpack présents

Ces fichiers sont déjà dans la branche `feat/multi-workspace-http` :

- `Procfile` → `web: lightrag-server --host 0.0.0.0 --port $PORT --workspace ${WORKSPACE:-default}`
- `runtime.txt` → `python-3.12.11`
- `requirements.txt` → `.[api]` (délègue à `pyproject.toml` extras `api`)

### 2.3 Provisionner PostgreSQL avec pgvector

```bash
scalingo --app lightrag-safebrain-recette addons-add postgresql postgresql-business-256
```

Puis activer l'extension pgvector via la console SQL :

```bash
scalingo --app lightrag-safebrain-recette pgsql-console
# Dans le prompt PostgreSQL :
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

## 3. Variables d'environnement à configurer

```bash
# === LLM Cloud Temple (SecNumCloud, OpenAI-compatible) ===
scalingo --app lightrag-safebrain-recette env-set \
  LLM_BINDING=openai \
  LLM_MODEL=qwen3-2507-gptq:235b \
  LLM_BINDING_HOST=https://api.ai.cloud-temple.com/v1 \
  LLM_BINDING_API_KEY=<CLE_CLOUD_TEMPLE>

# === Embeddings (Granite 768 dim) ===
scalingo --app lightrag-safebrain-recette env-set \
  EMBEDDING_BINDING=openai \
  EMBEDDING_MODEL=granite-embedding:278m \
  EMBEDDING_DIM=768 \
  EMBEDDING_BINDING_HOST=https://api.ai.cloud-temple.com/v1 \
  EMBEDDING_BINDING_API_KEY=<CLE_CLOUD_TEMPLE>

# === Storage backends (Postgres dispo via SCALINGO_POSTGRESQL_URL) ===
scalingo --app lightrag-safebrain-recette env-set \
  LIGHTRAG_KV_STORAGE=PGKVStorage \
  LIGHTRAG_VECTOR_STORAGE=PGVectorStorage \
  LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage \
  LIGHTRAG_GRAPH_STORAGE=NetworkXStorage

# Postgres : Scalingo expose SCALINGO_POSTGRESQL_URL. LightRAG attend des vars
# discrètes — on les dérive via une petite couche. Si LightRAG ne sait pas
# parser une URL, on doit éclater manuellement :
scalingo --app lightrag-safebrain-recette env-set \
  POSTGRES_HOST=<host depuis SCALINGO_POSTGRESQL_URL> \
  POSTGRES_PORT=<port> \
  POSTGRES_USER=<user> \
  POSTGRES_PASSWORD=<password> \
  POSTGRES_DATABASE=<dbname>

# === Server ===
scalingo --app lightrag-safebrain-recette env-set \
  WORKSPACE=default \
  LIGHTRAG_API_KEY=<GENERER_UNE_CLE_FORTE_64_CHAR>

# === Perf (peut s'ajuster sans redéploiement) ===
scalingo --app lightrag-safebrain-recette env-set \
  MAX_ASYNC=24 \
  MAX_PARALLEL_INSERT=8
```

**Notes** :
- `<CLE_CLOUD_TEMPLE>` : récupérer dans le coffre 1Password ou réutiliser celle de SafeBrain.
- `LIGHTRAG_API_KEY` : générer un secret fort (ex: `openssl rand -hex 32`). À reporter ensuite dans la config SafeBrain (`LIGHTRAG_API_KEY` côté app `safebrain-recette`).
- `HOST` et `PORT` : ne PAS les setter, Scalingo les fournit (et le Procfile utilise `$PORT`).

## 4. Premier deploy

```bash
git push scalingo feat/multi-workspace-http:master
```

Scalingo va :
1. Détecter le buildpack Python
2. Lire `runtime.txt` → installer Python 3.12.11
3. Lire `requirements.txt` → `pip install .[api]` (toutes les deps + extras)
4. Démarrer le process `web` selon le `Procfile`

Suivre le build :

```bash
scalingo --app lightrag-safebrain-recette logs --follow
```

## 5. Smoke test

```bash
# 1. Health check (devrait répondre 200)
curl https://lightrag-safebrain-recette.osc-fr1.scalingo.io/health \
  -H "X-API-Key: <LIGHTRAG_API_KEY>"

# 2. Endpoint workspace strict (devrait répondre 400 SANS header workspace)
curl -X POST https://lightrag-safebrain-recette.osc-fr1.scalingo.io/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <LIGHTRAG_API_KEY>" \
  -d '{"query":"test","mode":"hybrid"}'
# Attendu : {"detail": "LIGHTRAG-WORKSPACE header is required..."}

# 3. Idem AVEC header → 200 (réponse vide si workspace neuf)
curl -X POST https://lightrag-safebrain-recette.osc-fr1.scalingo.io/query \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <LIGHTRAG_API_KEY>" \
  -H "LIGHTRAG-WORKSPACE: kb_test_curl" \
  -d '{"query":"test","mode":"hybrid"}'
```

## 6. Côté SafeBrain (app `safebrain-recette`)

Une fois LightRAG up, configurer la connexion :

```bash
scalingo --app safebrain-recette env-set \
  LIGHTRAG_URL=https://lightrag-safebrain-recette.osc-fr1.scalingo.io \
  LIGHTRAG_API_KEY=<MEME_VALEUR_QUE_LIGHTRAG>
```

Ou, pour passer par le Scalingo Private Network (recommandé — chiffré +
zéro trafic public) :

```bash
scalingo --app safebrain-recette env-set \
  LIGHTRAG_URL=http://lightrag-web.<APP_ID>.<PN_ID>.private-network.internal:<PORT>
```

Identifiants à récupérer via `scalingo --app lightrag-safebrain-recette dashboard`.

## 7. Mises à jour ultérieures

À chaque nouveau commit sur `feat/multi-workspace-http` :

```bash
cd ~/Projets/jamespot/lightrag
git push scalingo feat/multi-workspace-http:master
```

Scalingo rebuild + redémarre automatiquement.

## 8. Cron de restart (mitigation memory pool)

Comme documenté dans `FORK_NOTES.md` (point #8), le pool de RAG instances
n'a pas de LRU. Pour éviter un memory creep à long terme, ajouter un cron
quotidien qui restart le dyno :

```bash
scalingo --app lightrag-safebrain-recette scheduler-add \
  "0 4 * * *" "scalingo --app lightrag-safebrain-recette restart"
```

(à exécuter via Scalingo Scheduler — vérifier la syntaxe exacte dans le
dashboard si l'addon scheduler n'est pas dispo via CLI).

## 9. Troubleshooting

- **Build qui timeout** : les deps `torch`, `transformers` peuvent être lourds.
  Si le build dépasse 15 min, considérer un build Docker custom (voir
  `Dockerfile` du fork, à pousser sur GHCR puis pull sur Scalingo).

- **pgvector extension absente** : `psycopg2.errors.UndefinedFile: could not
  open extension control file`. Reprendre §2.3.

- **Workspace pas isolé** : si une query inter-KB ramène du contenu d'une
  autre KB, vérifier que les patches du fork sont bien actifs :
  `scalingo --app lightrag-safebrain-recette run python -c "from lightrag.api import lightrag_server; print(hasattr(lightrag_server, 'get_workspace_from_request'))"`
  doit retourner `True`.
