# Scalingo source automatiquement tout .profile.d/*.sh avant de lancer chaque
# process (web, one-off, worker). On l'utilise ici pour mapper les variables
# fournies par l'addon Scalingo MongoDB (SCALINGO_MONGO_URL) vers les noms que
# LightRAG attend (MONGO_URI), sans dupliquer l'URL en dur dans la config.
#
# Pas de fallback "localhost" ici : si l'addon n'est pas attaché, LightRAG
# remontera une erreur explicite plutôt que de silencieusement viser localhost.

if [ -n "$SCALINGO_MONGO_URL" ]; then
  export MONGO_URI="$SCALINGO_MONGO_URL"
fi
