# Scalingo source automatiquement tout .profile.d/*.sh avant chaque process
# (web, one-off, worker). On l'utilise ici pour mapper l'URL fournie par
# l'addon Scalingo OpenSearch (SCALINGO_OPENSEARCH_URL) vers les variables
# que LightRAG attend (OPENSEARCH_HOSTS, OPENSEARCH_USER, OPENSEARCH_PASSWORD).
#
# Format SCALINGO_OPENSEARCH_URL : https://user:password@host:port
# → on extrait chaque composant avec une regex bash (parameter expansion).

if [ -n "$SCALINGO_OPENSEARCH_URL" ]; then
  # Détection du schéma (http vs https) — Scalingo expose certaines instances
  # en http via le Private Network interne, d'autres en https. On lit ce qui
  # est dans l'URL plutôt que de hardcoder.
  case "$SCALINGO_OPENSEARCH_URL" in
    https://*) export OPENSEARCH_USE_SSL=true  ;;
    http://*)  export OPENSEARCH_USE_SSL=false ;;
  esac

  url="${SCALINGO_OPENSEARCH_URL#https://}"
  url="${url#http://}"
  creds="${url%@*}"
  hostport="${url#*@}"

  export OPENSEARCH_HOSTS="$hostport"
  export OPENSEARCH_USER="${creds%%:*}"
  export OPENSEARCH_PASSWORD="${creds#*:}"
  # En https : Scalingo expose un cert SSL signé par eux-mêmes, non reconnu par
  # les CA système. La connexion reste chiffrée TLS, on désactive juste la
  # validation du CA. En http : flag ignoré par opensearch-py.
  export OPENSEARCH_VERIFY_CERTS=false
fi
