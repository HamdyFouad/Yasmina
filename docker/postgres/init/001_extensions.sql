-- Project Yasmina — PostgreSQL bootstrap (runs once on an empty data volume).
-- The pgvector extension is created here and, idempotently, by Alembic.
CREATE EXTENSION IF NOT EXISTS vector;

-- n8n keeps its workflow data in a separate database on the same cluster.
SELECT format('CREATE DATABASE n8n OWNER %I', current_user)
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'n8n')
\gexec
