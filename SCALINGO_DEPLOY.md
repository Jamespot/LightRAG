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

> ### ⭐ Choix du modèle LLM (extraction KG) — IMPORTANT
> Utiliser un modèle **MoE** (Mixture of Experts), PAS un modèle dense.
> **Recommandé recette ET prod : `qwen3.6:35b`** (`Qwen/Qwen3.6-35B-A3B-FP8`).
>
> Pourquoi : l'extraction d'entités/relations envoie jusqu'à `MAX_ASYNC` appels
> LLM **concurrents** à Cloud Temple. Un modèle **dense** (ex. `qwen3.6:27b`)
> active tous ses params à chaque appel → peu de requêtes par GPU → **sature dès
> 4-8 concurrents** (queues de 60-94s, timeouts worker à 600s, indexations
> échouées). Un modèle **MoE A3B** n'active que 3B params par requête → un GPU en
> sert beaucoup plus en parallèle → **encaisse 8-16 concurrents sans broncher**
> (mesuré : p50 ~10-15s, 0 échec).
>
> Mesure empirique (01/06/2026, prompts d'extraction réalistes) :
> | Concurrence | qwen3.6:35b (MoE) | qwen3.6:27b (dense) |
> |---|---|---|
> | N=8  | p50 10.2s / max 11.6s | p50 17.7s / **max 94s** |
> | N=16 | p50 14.8s / max 16.8s | p50 34.8s / **max 94s** |
>
> `qwen3.6:35b` est de la **même famille** que le 27b → conventions d'extraction
> identiques → cohérence du graphe préservée, et il est même **plus rapide**
> (144 vs 122 tok/s). Alternatives MoE valables : `nemotron-cascade:30b`,
> `mistral-small4:119b`. Éviter tout modèle **dense** pour l'indexation.

```bash
# === LLM Cloud Temple (SecNumCloud, OpenAI-compatible) ===
# qwen3.6:35b = MoE A3B (voir l'encadré ci-dessus) — encaisse la concurrence.
scalingo --app lightrag-safebrain-recette env-set \
  LLM_BINDING=openai \
  LLM_MODEL=qwen3.6:35b \
  LLM_BINDING_HOST=https://api.ai.cloud-temple.com/v1 \
  LLM_BINDING_API_KEY=<CLE_CLOUD_TEMPLE>

# === Embeddings (Granite 768 dim) ===
scalingo --app lightrag-safebrain-recette env-set \
  EMBEDDING_BINDING=openai \
  EMBEDDING_MODEL=granite-embedding:278m \
  EMBEDDING_DIM=768 \
  EMBEDDING_BINDING_HOST=https://api.ai.cloud-temple.com/v1 \
  EMBEDDING_BINDING_API_KEY=<CLE_CLOUD_TEMPLE>

# === Storage backends ===
# KV / vector / doc_status en Postgres. GRAPHE en OpenSearch — PAS NetworkX :
# le filesystem Scalingo n'est pas persistant, NetworkXStorage perd donc tout
# le graphe à chaque restart/déploiement. OpenSearch (addon Scalingo) est
# persistant. Requiert : addon OpenSearch attaché + `.profile.d/opensearch.sh`
# (mappe SCALINGO_OPENSEARCH_URL → OPENSEARCH_HOSTS/USER/PASSWORD) + opensearch-py.
scalingo --app lightrag-safebrain-recette env-set \
  LIGHTRAG_KV_STORAGE=PGKVStorage \
  LIGHTRAG_VECTOR_STORAGE=PGVectorStorage \
  LIGHTRAG_DOC_STATUS_STORAGE=PGDocStatusStorage \
  LIGHTRAG_GRAPH_STORAGE=OpenSearchGraphStorage

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
# MAX_ASYNC = nb d'appels LLM concurrents (sémaphore global). Avec un modèle MoE
# (qwen3.6:35b) on tient confortablement 8-16. NE PAS monter trop haut sur un
# modèle dense (sature). 8 = sûr ; 12-16 = plus rapide si le MoE encaisse.
# LLM_TIMEOUT=300 → timeout worker = 600s (LLM_TIMEOUT*2).
scalingo --app lightrag-safebrain-recette env-set \
  MAX_ASYNC=8 \
  MAX_PARALLEL_INSERT=8 \
  LLM_TIMEOUT=300
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
