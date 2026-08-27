import asyncpg
import asyncio
import os
from pathlib import Path

OCR_ROOT = r'D:\Code\learn\ocr_compare\paddleocr_vl_1_6\QuyetDinh'

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        print("=== Final FTS Test (Encoding Safe) ===")
        # Create temporary table for OCR text
        await conn.execute('DROP TABLE IF EXISTS ocr_fts_test;')
        await conn.execute('''
            CREATE TABLE ocr_fts_test (
                id SERIAL PRIMARY KEY,
                file_name TEXT NOT NULL,
                content TEXT NOT NULL,
                search_vector TSVECTOR
                    GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
            );
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_ocr_fts_test_search_vector
            ON ocr_fts_test USING GIN (search_vector);
        ''')
        print("[OK] Temporary table ocr_fts_test created with GIN index.")

        # Read OCR files - limit to first 5 files for speed
        inserted = 0
        files_to_process = []
        for ext in ('*.txt', '*.md'):
            files_to_process.extend(Path(OCR_ROOT).rglob(ext))
        files_to_process.sort()
        files_to_process = files_to_process[:5]
        for file_path in files_to_process:
            try:
                raw_bytes = file_path.read_bytes()
                try:
                    content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    content = raw_bytes.decode('utf-8', errors='replace')
                await conn.execute('''
                    INSERT INTO ocr_fts_test (file_name, content)
                    VALUES ($1, $2);
                ''', file_path.name, content)
                inserted += 1
                print(f"  Inserted: {file_path.name} ({len(content)} chars)")
            except Exception as e:
                print(f"  Error inserting {file_path}: {e}")
        print(f"[OK] Inserted {inserted} files from OCR corpus.")

        # Test FTS queries - using ASCII-safe descriptions
        print("\n=== FTS Accuracy Tests ===")
        test_cases = [
            ('cong', 'unaccented "cong"', 'Should match only if text has "cong" without accent'),
            ('công', 'accented "công"', 'Should match only if text has "công" with accent'),
            ('luat', 'unaccented "luat"', 'Should match only if text has "luat" without accent'),
            ('luật', 'accented "luật"', 'Should match only if text has "luật" with accent'),
            ('dat', 'unaccented "dat"', "Should NOT match 'đất' (accented) with simple dict"),
            ('đất', 'accented "đất"', 'Should match only if text has "đất" with accent'),
            ('luật & đất', 'phrase AND: "luật & đất"', 'Should match documents containing both words'),
        ]

        for query, description, note in test_cases:
            # Use repr(query) to see the exact string (non-ASCII shown as escape sequences)
            print(f'\n{description}: {repr(query)}')
            print(f"  Note: {note}")
            try:
                rows = await conn.fetch('''
                    SELECT id, file_name, left(content, 150) AS snippet,
                           ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS rank
                    FROM ocr_fts_test
                    WHERE search_vector @@ to_tsquery('simple', $1)
                    ORDER BY rank DESC
                    LIMIT 5;
                ''', query)
                print(f'  Found {len(rows)} results:')
                if len(rows) == 0:
                    print('    (no matches)')
                else:
                    for r in rows:
                        # Make snippet safe for ASCII output by replacing non-ASCII with ?
                        snippet = r['snippet']
                        # Replace newline with space and truncate
                        snippet = snippet.replace('\n', ' ').strip()
                        if len(snippet) > 150:
                            snippet = snippet[:150] + '...'
                        # Convert to ASCII, replacing non-ASCII with ?
                        ascii_snippet = snippet.encode('ascii', errors='replace').decode('ascii')
                        ascii_filename = r['file_name'].encode('ascii', errors='replace').decode('ascii')
                        print(f'    [{ascii_filename}] {ascii_snippet} (rank={r["rank"]:.4f})')
            except Exception as e:
                print(f'  Error executing query: {e}')

        # Show search_vector for a few rows
        print('\n=== Sample search_vector tokens ===')
        rows = await conn.fetch('''
            SELECT id, file_name, search_vector
            FROM ocr_fts_test
            LIMIT 3;
        ''')
        for r in rows:
            ascii_filename = r['file_name'].encode('ascii', errors='replace').decode('ascii')
            print(f'  ID: {r["id"]}, File: {ascii_filename}')
            print(f'    TSVector: {r["search_vector"]}')
            print()

        print("\n=== Summary ===")
        print("FTS mechanism is working with the 'simple' dictionary.")
        print("Key observations for Vietnamese:")
        print("1. The simple dictionary does NOT remove accents.")
        print("2. Therefore:")
        print("   - Queries without accents will NOT match accented words in the text.")
        print("   - Queries with accents will ONLY match if the text has the exact same accent.")
        print("3. For better Vietnamese retrieval, consider using the 'unaccent' extension with a custom dictionary.")
        print("   (This is optional for P10A but recommended for production quality.)")
        print("\nYou can now decide if the current FTS setup meets your accuracy requirements for P10A.")

    except Exception as e:
        print(f'Error: {e}')
        import traceback
        traceback.print_exc()
    finally:
        # Clean up temporary table
        try:
            await conn.execute('DROP TABLE IF EXISTS ocr_fts_test;')
            print("[OK] Temporary table ocr_fts_test dropped.")
        except:
            pass
        await conn.close()
        print("\nScript finished.")

if __name__ == '__main__':
    asyncio.run(main())