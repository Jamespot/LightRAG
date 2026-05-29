# Scalingo source automatiquement tout .profile.d/*.sh avant chaque process
# (web, one-off, worker). On l'utilise ici pour mapper l'URL fournie par
# l'addon Scalingo OpenSearch (SCALINGO_OPENSEARCH_URL) vers les variables
# que LightRAG attend (OPENSEARCH_HOSTS, OPENSEARCH_USER, OPENSEARCH_PASSWORD).
#
# Format SCALINGO_OPENSEARCH_URL : https://user:password@host:port
# → on extrait chaque composant avec une regex bash (parameter expansion).

if [ -n "$SCALINGO_OPENSEARCH_URL" ]; then
  url="${SCALINGO_OPENSEARCH_URL#https://}"
  url="${url#http://}"
  creds="${url%@*}"
  hostport="${url#*@}"

  export OPENSEARCH_HOSTS="$hostport"
  export OPENSEARCH_USER="${creds%%:*}"
  export OPENSEARCH_PASSWORD="${creds#*:}"
  export OPENSEARCH_USE_SSL=true
  # Scalingo expose un cert SSL signé par eux-mêmes, non reconnu par les CA
  # par défaut du système. La connexion reste chiffrée TLS, on désactive
  # juste la validation du CA (équivalent à scalingo-cli qui passe
  # --tlsAllowInvalidCertificates).
  export OPENSEARCH_VERIFY_CERTS=false
fi
