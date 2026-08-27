import asyncpg
import asyncio
import uuid
import hashlib
import datetime

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        print("=== Final Verification of FTS Upgrade ===")
        # 1. Check extension
        ext = await conn.fetchval("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'unaccent');")
        print(f"1. unaccent extension installed: {ext}")
        # 2. Check config - using correct column names for pg_ts_config and pg_namespace
        cfg = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1
                FROM pg_ts_config t
                JOIN pg_namespace n ON t.tcfgnamespace = n.oid
                WHERE t.cfgname = 'vietnamese_simple' AND n.nspname = 'public'
            );
        """)
        print(f"2. vietnamese_simple config exists in public schema: {cfg}")
        # 3. Check column
        col = await conn.fetchrow("""
            SELECT column_name, data_type, is_generated, generation_expression
            FROM information_schema.columns
            WHERE table_name='document_chunks' AND column_name='search_vector_vi';
        """)
        if col:
            print(f"3. Column search_vector_vi: {col['data_type']}, generated: {col['is_generated']}")
        else:
            print("3. Column search_vector_vi not found.")
        # 4. Check index
        idx = await conn.fetchval("""
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'document_chunks' AND indexname = 'idx_document_chunks_search_vector_vi'
            );
        """)
        print(f"4. Index idx_document_chunks_search_vector_vi exists: {idx}")

        # 5. Insert test data
        print("\n5. Inserting test data...")
        test_user_id = str(uuid.uuid4())
        test_document_id = str(uuid.uuid4())
        now = datetime.datetime.now(datetime.timezone.utc)
        # Insert document
        await conn.execute('''
            INSERT INTO documents (id, logical_document_id, user_id, version_number, source_type, external_id, title, uri, mime_type, source_content_hash, status, is_active, metadata, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
        ''',
            test_document_id, test_document_id, test_user_id, 1, 'test', 'test-id', 'Test Document for FTS VI', 'http://test', 'text/plain', None, 'active', True, '{}', now, now)
        # Insert test chunks
        sentences = [
            "Luật về quản lý và sử dụng đất đai",
            "Nghị định số 43/2014/NĐ-CP vềChi tiết thi hành một số điều của Luật về quản lý và sử dụng đất đai",
            "Quyết định số 1234/QĐ-UBND về việc phê duyệt kế hoạch sử dụng đất",
            "Công văn về việc thanh toán bồi thường",
            "Đại lý pháp luật về đất đai"
        ]
        for s in sentences:
            chunk_id = str(uuid.uuid4())
            content_hash = hashlib.sha256(s.encode('utf-8')).hexdigest()
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
                chunk_id, test_document_id, 0, 0, 'CHILD',
                None, '[]', s, '',
                None, None, None, content_hash, '[]',
                None, None, None, 'unknown', 0,
                None, None, '{}', now)  # provenance_uri, citation_label, metadata
        print(f"   Inserted {len(sentences)} test chunks.")

        # 6. Test FTS with vietnamese_simple
        print("\n6. Testing FTS with vietnamese_simple:")
        # Query without accent: cong
        print('   a) Searching for "cong" (no accent):')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND document_id = $2
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'cong', test_document_id)
        print(f'      Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'      {snippet} (rank={r["rank"]:.4f})')
        # Query with accent: luật
        print('   b) Searching for "luật" (with accent):')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND document_id = $2
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'luật', test_document_id)
        print(f'      Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'      {snippet} (rank={r["rank"]:.4f})')
        # Phrase AND: luật & đất
        print('   c) Searching for "luật & đất" (AND):')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND document_id = $2
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'luật & đất', test_document_id)
        print(f'      Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'      {snippet} (rank={r["rank"]:.4f})')
        # Query without accent: dat (should match because of unaccent)
        print('   d) Searching for "dat" (no accent):')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND document_id = $2
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'dat', test_document_id)
        print(f'      Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'      {snippet} (rank={r["rank"]:.4f})')

        # 7. Clean up test data
        print("\n7. Cleaning up test data...")
        await conn.execute('DELETE FROM document_chunks WHERE document_id = $1', test_document_id)
        await conn.execute('DELETE FROM documents WHERE id = $1', test_document_id)
        print("   Test data removed.")

        print("\n=== Verification Complete ===")
        print("If all steps above show success, the FTS upgrade to vietnamese_simple is working correctly.")
        print("You can now proceed to implement the retrieval services using this configuration.")

    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())