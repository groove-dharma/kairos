#!/bin/bash
set -euo pipefail

CONTAINER="workstation-postgres"
API="http://localhost:3000"

echo "=== Fortemi Init Fix (workstation) ==="

echo "[0/3] Fixing file storage permissions..."
docker exec -u root workstation-matric-api mkdir -p /var/lib/matric/files
docker exec -u root workstation-matric-api chown -R matric:matric /var/lib/matric

echo -n "[1/3] Esperando Fortemi API..."
until curl -sf "${API}/health" > /dev/null 2>&1; do
  echo -n "."; sleep 2
done
echo " OK"

echo "[2/3] Aplicando fix gen_uuid_v7()..."
docker exec "${CONTAINER}" psql -U matric -d matric -c \
  'CREATE OR REPLACE FUNCTION gen_uuid_v7() RETURNS uuid AS $$ SELECT uuidv7(); $$ LANGUAGE SQL;'

echo "[3/3] Creando HNSW index..."
docker exec "${CONTAINER}" psql -U matric -d matric -c \
  "CREATE INDEX IF NOT EXISTS idx_embedding_vector_hnsw ON embedding USING hnsw (vector vector_cosine_ops) WITH (m = 16, ef_construction = 64);"

echo ""
echo "=== Verificación ==="
echo "gen_uuid_v7():"
docker exec "${CONTAINER}" psql -U matric -d matric -c "SELECT proname FROM pg_proc WHERE proname = 'gen_uuid_v7';"
echo "HNSW index:"
docker exec "${CONTAINER}" psql -U matric -d matric -c "SELECT indexname FROM pg_indexes WHERE tablename = 'embedding' AND indexname = 'idx_embedding_vector_hnsw';"
echo "=== Done ==="
