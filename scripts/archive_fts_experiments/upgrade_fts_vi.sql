CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TEXT SEARCH CONFIGURATION IF NOT EXISTS public.vietnamese_simple ( COPY = simple );

ALTER TEXT SEARCH CONFIGURATION public.vietnamese_simple
    ALTER MAPPING FOR asciihword, hword_asciipart, word, hword, hword_part
    WITH unaccent, simple;

ALTER TABLE IF EXISTS document_chunks
ADD COLUMN IF NOT EXISTS search_vector_vi TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('vietnamese_simple', content_raw)) STORED;

CREATE INDEX IF NOT EXISTS idx_document_chunks_search_vector_vi
ON document_chunks USING GIN (search_vector_vi);