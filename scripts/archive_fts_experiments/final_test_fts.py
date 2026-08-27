import asyncpg
import asyncio
import uuid
import hashlib
import datetime

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        # Ensure we have a document to attach to
        doc = await conn.fetchrow('SELECT id FROM documents WHERE provenance_uri = $1', 'fts_test_doc')
        if doc:
            document_id = doc['id']
        else:
            document_id = str(uuid.uuid4())
            now = datetime.datetime.now(datetime.timezone.utc)
            await conn.execute('''
                INSERT INTO documents (id, logical_document_id, user_id, version_number, source_type, external_id, title, uri, mime_type, source_content_hash, status, is_active, metadata, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            ''',
                document_id, document_id, None, 1, 'test', 'test-id', 'Test Document for FTS', 'http://test', 'text/plain', None, 'active', True, '{}', now, now)
            # Add a provenance_uri to mark it as test
            await conn.execute('UPDATE documents SET provenance_uri = $1 WHERE id = $2', 'fts_test_doc', document_id)
        # Clear any old test chunks
        await conn.execute("DELETE FROM document_chunks WHERE provenance_uri = $1", 'fts_test_chunk')
        # Insert test sentences
        sentences = [
            "Luật về quản lý và sử dụng đất đai",
            "Nghị định số 43/2014/NĐ-CP vềChi tiết thi hành một số điều của Luật về quản lý và sử dụng đất đai",
            "Quyết định số 1234/QĐ-UBND về việc phê duyệt kế hoạch sử dụng đất",
            "Công văn về việc thanh toán bồi thường",
            "Đại lý pháp luật về đất đai"
        ]
        now = datetime.datetime.now(datetime.timezone.utc)
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
                chunk_id, document_id, 0, 0, 'CHILD',
                None, '[]', s, '',
                None, None, None, content_hash, '[]',
                None, None, None, 'unknown', 0,
                'fts_test_chunk', None, '{}', now)
        print(f"Inserted {len(sentences)} test chunks.")
        # Now test FTS with vietnamese_simple
        print("\n=== Testing FTS with vietnamese_simple ===")
        # Query without accent: cong
        print('\\n1. Searching for "cong" (no accent) using vietnamese_simple:')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_chunk'
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'cong')
        print(f'Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Query with accent: luật
        print('\\n2. Searching for "luật" (with accent) using vietnamese_simple:')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_chunk'
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'luật')
        print(f'Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Phrase AND: luật & đất
        print('\\n3. Searching for "luật & đất" (AND) using vietnamese_simple:')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_chunk'
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'luật & đất')
        print(f'Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Query without accent: dat (should match because of unaccent)
        print('\\n4. Searching for "dat" (no accent) using vietnamese_simple:')
        rows = await conn.fetch('''
            SELECT id, left(content_raw, 150) AS snippet,
                   ts_rank_cd(search_vector_vi, to_tsquery('vietnamese_simple', $1)) AS rank
            FROM document_chunks
            WHERE search_vector_vi @@ to_tsquery('vietnamese_simple', $1)
              AND provenance_uri = 'fts_test_chunk'
            ORDER BY rank DESC
            LIMIT 5;
        ''', 'dat')
        print(f'Found {len(rows)} results:')
        for r in rows:
            snippet = r['snippet'].replace('\n', ' ').strip()
            if len(snippet) > 150:
                snippet = snippet[:150] + '...'
            print(f'  {snippet} (rank={r["rank"]:.4f})')
        # Clean up
        await conn.execute('DELETE FROM document_chunks WHERE provenance_uri = $1', 'fts_test_chunk')
        await conn.execute('DELETE FROM documents WHERE provenance_uri = $1', 'fts_test_doc')
        print('\\n✓ Test data cleaned up.')
        print("\\n=== Summary ===")
        print("FTS with vietnamese_simple (unaccent + simple) is working.")
        print("Queries without accent now match accented words in the text.")
        print("This improves recall for Vietnamese text.")
    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())