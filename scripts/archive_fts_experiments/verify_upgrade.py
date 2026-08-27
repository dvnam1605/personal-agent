import asyncpg
import asyncio

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        # Check extension
        ext = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'unaccent');")
        print(f"unaccent extension installed: {ext}")
        # Check config - note: pg_ts_config does not have nspname column in some versions; use nsname or check pg_namespace
        cfg = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1
                FROM pg_ts_config t
                JOIN pg_namespace n ON t.tcfgnamespace = n.oid
                WHERE t.cfgname = 'vietnamese_simple' AND n.nspname = 'public'
            );
        """)
        print(f"vietnamese_simple config exists in public schema: {cfg}")
        # Check column
        col = await conn.fetchrow("""
            SELECT column_name, data_type, is_generated, generation_expression
            FROM information_schema.columns
            WHERE table_name='document_chunks' AND column_name='search_vector_vi';
        """)
        if col:
            print(f"Column search_vector_vi: {col['data_type']}, generated: {col['is_generated']}")
            print(f"Generation expression: {col['generation_expression']}")
        else:
            print("Column search_vector_vi not found.")
        # Check index
        idx = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'document_chunks' AND indexname = 'idx_document_chunks_search_vector_vi'
            );
        """)
        print(f"Index idx_document_chunks_search_vector_vi exists: {idx}")
        # Test FTS with vietnamese_simple
        # Insert a test row if needed
        await conn.execute("DELETE FROM document_chunks WHERE provenance_uri = 'fts_test_final'")
        # Get a document
        doc = await conn.fetchrow("SELECT id FROM documents LIMIT 1")
        if doc:
            document_id = doc['id']
        else:
            # Create a minimal document
            import uuid, datetime
            document_id = str(uuid.uuid4())
            now = datetime.datetime.now(datetime.timezone.utc)
            await conn.execute('''
                INSERT INTO documents (id, logical_document_id, user_id, version_number, source_type, external_id, title, uri, mime_type, source_content_hash, status, is_active, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ''',
                document_id, document_id, None, 1, 'test', 'test-id', 'Test Document', 'http://test', 'text/plain', None, 'active', True, '{}', now, now)
        # Insert a test chunk
        chunk_id = str(uuid.uuid4())
        content = "Luật về quản lý và sử dụng đất đai"
        import hashlib
        content_hash = hashlib.sha256(content.encode('utf-8')).hexdigest()
        now = datetime.datetime.now(datetime.timezone.utc)
        await conn.execute('''
            INSERT INTO document_chunks (
                id, document_id, chunk_index, hierarchy_level, node_type,
                parent_id, heading_path, content_raw, content_embedding_text,
                page_start, page_end, token_count, content_hash, source_block_ids,
                parent_chunker_version, child_chunker_version, embedding,
                embedding_model, embedding_dimensions, provenance_uri,
                citation_label, metadata, created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14,
                $15, $16, $17, $18, $19, $20, $21, $22, $23
            )
            ''',
                chunk_id, document_id, 0, 0, 'CHILD',
                None, '[]', content, '',
                None, None, None, content_hash, '[]',
                None, None, None, 'unknown', 0,
                'fts_test_final', None, '{}', now)
        # Test search for "cong" (no accent) - should match because of unaccent
        print('\n--- FTS Test with vietnamese_simple ---')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 100) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_final'
            ORDER BY rank DESC
        ''', 'cong')
        print(f'Search for "cong" (no accent) found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 100:
                snippet = snippet[:100] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Test search for "luật" (with accent)
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 100) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_final'
            ORDER BY rank DESC
        ''', 'luật')
        print(f'Search for "luật" (with accent) found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 100:
                snippet = snippet[:100] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Test search for "đất" (with accent)
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 100) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_final'
            ORDER BY rank DESC
        ''', 'đất')
        print(f'Search for "đất" (with accent) found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 100:
                snippet = snippet[:100] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Test search for "dat" (no accent) - should also match because of unaccent
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 100) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_final'
            ORDER BY rank DESC
        ''', 'dat')
        print(f'Search for "dat" (no accent) found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 100:
                snippet = snippet[:100] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Clean up
        await conn.execute("DELETE FROM document_chunks WHERE provenance_uri = $1", 'fts_test_final')
        # If we created a test document, delete it
        if 'document_id' in locals() and doc is None:
            await conn.execute('DELETE FROM documents WHERE id = $1', document_id)
        print('\n✓ Verification completed and test data cleaned up.')
    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())