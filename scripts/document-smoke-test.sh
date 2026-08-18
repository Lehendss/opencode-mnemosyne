#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/.env"
BASE_URL="http://127.0.0.1:${DOCUMENT_PORT:-8789}"
PROJECT_ID="$(printf '%s' "$ROOT" | shasum -a 256 | cut -d' ' -f1)"
TEMP_DIR="$(mktemp -d)"
AUTHORIZATION="Authorization: Bearer $DOCUMENT_API_TOKEN"
UNIQUE="mnemosyne-$RANDOM-$(date +%s)"

cleanup() {
  set +e
  while IFS= read -r object_key; do
    [[ -n "$object_key" ]] || continue
    docker compose --project-directory "$ROOT" run --rm --entrypoint /bin/sh \
      -e OBJECT_KEY="$object_key" minio-init -c \
      'mc alias set local http://minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null && mc rm --force "local/$MINIO_BUCKET/$OBJECT_KEY" >/dev/null'
  done < <(docker compose --project-directory "$ROOT" exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT DISTINCT d.object_key FROM document d JOIN document_project dp USING (document_id) WHERE dp.project_label = 'document-smoke';")
  docker compose --project-directory "$ROOT" exec -T postgres \
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "DELETE FROM document d USING document_project dp WHERE d.document_id = dp.document_id AND dp.project_label = 'document-smoke';" \
    >/dev/null
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

printf 'Document RAG textual acceptance test. Unique marker: %s.\n' "$UNIQUE" > "$TEMP_DIR/text.txt"
cupsfilter -m application/pdf "$TEMP_DIR/text.txt" > "$TEMP_DIR/text.pdf" 2>/dev/null

magick -size 1400x400 xc:white -fill black -pointsize 42 -gravity center \
  -annotate +0+0 "OCR acceptance marker $UNIQUE" "$TEMP_DIR/scan.png"
magick "$TEMP_DIR/scan.png" "$TEMP_DIR/scan.pdf"
[[ -z "$(pdftotext "$TEMP_DIR/scan.pdf" - 2>/dev/null | tr -d '[:space:]')" ]]

for _ in {1..30}; do
  if curl -fsS "$BASE_URL/health" >/dev/null; then
    break
  fi
  sleep 1
done
curl -fsS "$BASE_URL/health" >/dev/null

if [[ "$(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/v1/documents/unknown?project_id=$PROJECT_ID")" != "401" ]]; then
  echo "Document API accepted an unauthenticated request." >&2
  exit 1
fi

upload() {
  curl -fsS -X POST "$BASE_URL/v1/documents" \
    -H "$AUTHORIZATION" \
    -F "project_id=$PROJECT_ID" \
    -F "project_label=document-smoke" \
    -F "file=@$1;type=application/pdf"
}

json_field() {
  python3 -c "import json,sys; print(json.load(sys.stdin)[$1])"
}

wait_ready() {
  local document_id="$1"
  local status=""
  for _ in {1..120}; do
    status="$(curl -fsS "$BASE_URL/v1/documents/$document_id?project_id=$PROJECT_ID" \
      -H "$AUTHORIZATION" | json_field "'status'")"
    if [[ "$status" == "ready" ]]; then
      return 0
    fi
    if [[ "$status" == "failed" ]]; then
      echo "Document processing failed for $document_id." >&2
      return 1
    fi
    sleep 2
  done
  echo "Document processing timed out for $document_id (last status: $status)." >&2
  return 1
}

TEXT_RESPONSE="$(upload "$TEMP_DIR/text.pdf")"
TEXT_ID="$(printf '%s' "$TEXT_RESPONSE" | json_field "'document_id'")"
wait_ready "$TEXT_ID"

DUPLICATE_ID="$(upload "$TEMP_DIR/text.pdf" | json_field "'document_id'")"
[[ "$TEXT_ID" == "$DUPLICATE_ID" ]]

cp "$TEMP_DIR/text.pdf" "$TEMP_DIR/project-alias.pdf"
ALIAS_ID="$(upload "$TEMP_DIR/project-alias.pdf" | json_field "'document_id'")"
[[ "$TEXT_ID" == "$ALIAS_ID" ]]
curl -fsS "$BASE_URL/v1/documents/$TEXT_ID?project_id=$PROJECT_ID" -H "$AUTHORIZATION" \
  | python3 -c "import json,sys; assert json.load(sys.stdin)['original_filename']=='project-alias.pdf'"

curl -fsS -X POST "$BASE_URL/v1/search" \
  -H "$AUTHORIZATION" -H 'Content-Type: application/json' \
  --data "{\"query\":\"$UNIQUE\",\"project_id\":\"$PROJECT_ID\",\"limit\":5}" \
  | python3 -c "import json,sys; data=json.load(sys.stdin); assert any('$UNIQUE' in row['snippet'] for row in data['results'])"

docker compose --project-directory "$ROOT" exec -T -e TEST_PROJECT_ID="$PROJECT_ID" documents \
  python -c "import os; import document_service.api as api; api.embed=lambda _: (_ for _ in ()).throw(RuntimeError('offline')); result=api.search_documents(api.SearchRequest(query='project-alias.pdf', project_id=os.environ['TEST_PROJECT_ID'], limit=5)); assert any(row['original_filename']=='project-alias.pdf' for row in result['results'])"

curl -fsS "$BASE_URL/v1/documents/$TEXT_ID/pages?project_id=$PROJECT_ID&pages=1" \
  -H "$AUTHORIZATION" \
  | python3 -c "import json,sys; data=json.load(sys.stdin); assert '$UNIQUE' in data['pages'][0]['content']"

curl -fsS "$BASE_URL/v1/documents/$TEXT_ID/page/1.jpg?project_id=$PROJECT_ID" \
  -H "$AUTHORIZATION" -o "$TEMP_DIR/rendered.jpg"
[[ "$(file -b --mime-type "$TEMP_DIR/rendered.jpg")" == "image/jpeg" ]]

curl -fsS "$BASE_URL/v1/documents/$TEXT_ID/download?project_id=$PROJECT_ID" \
  -H "$AUTHORIZATION" -o "$TEMP_DIR/recovered.pdf"
[[ "$(shasum -a 256 "$TEMP_DIR/text.pdf" | cut -d' ' -f1)" == "$(shasum -a 256 "$TEMP_DIR/recovered.pdf" | cut -d' ' -f1)" ]]

SCAN_ID="$(upload "$TEMP_DIR/scan.pdf" | json_field "'document_id'")"
wait_ready "$SCAN_ID"
curl -fsS "$BASE_URL/v1/documents/$SCAN_ID/pages?project_id=$PROJECT_ID&pages=1" \
  -H "$AUTHORIZATION" \
  | python3 -c "import json,sys; data=json.load(sys.stdin); page=data['pages'][0]; assert page['text_source']=='ocr'; assert 'OCR acceptance marker' in page['content']"

echo "Document RAG end-to-end smoke test passed."
