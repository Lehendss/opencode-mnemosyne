ALTER TABLE memory ADD COLUMN IF NOT EXISTS importance real NOT NULL DEFAULT 0.5;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS confidence real NOT NULL DEFAULT 0.8;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS valid_from timestamptz;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS valid_until timestamptz;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS supersedes uuid REFERENCES memory(memory_id);
ALTER TABLE memory ADD COLUMN IF NOT EXISTS access_count integer NOT NULL DEFAULT 0;
ALTER TABLE memory ADD COLUMN IF NOT EXISTS last_accessed_at timestamptz;

UPDATE memory
SET importance = CASE kind
    WHEN 'bug_resolution' THEN 0.95
    WHEN 'preference' THEN 0.90
    WHEN 'decision' THEN 0.85
    WHEN 'procedure' THEN 0.85
    WHEN 'incident' THEN 0.80
    WHEN 'session_summary' THEN 0.70
    WHEN 'file_change' THEN 0.70
    WHEN 'tool_result' THEN 0.45
    WHEN 'assistant_response' THEN 0.40
    WHEN 'user_prompt' THEN 0.35
    ELSE 0.50
END,
valid_from = COALESCE(valid_from, occurred_at);

CREATE INDEX IF NOT EXISTS memory_kind_importance_idx
ON memory (kind, importance DESC) WHERE deleted_at IS NULL;
