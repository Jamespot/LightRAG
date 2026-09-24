"""KNN SQL must restrict to one workspace before distance sort."""

from lightrag.kg.postgres_impl import SQL_TEMPLATES


def test_knn_templates_materialize_workspace_before_hnsw():
    for key in ("entities", "chunks", "relationships"):
        sql = SQL_TEMPLATES[key]
        assert "WITH ws AS MATERIALIZED" in sql, key
        assert sql.index("WHERE workspace = $1") < sql.index("ORDER BY content_vector")
        assert "FROM ws" in sql
