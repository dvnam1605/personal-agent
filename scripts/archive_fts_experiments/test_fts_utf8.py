import asyncpg
import asyncio
import os
import sys
from pathlib import Path

# Try to set console to UTF-8 on Windows
if sys.platform.startswith('win'):
    os.system('chcp 65001 >nul')

OCR_ROOT = r'D:\Code\learn\ocr_compare\paddleocr_vl_1_6\QuyetDinh'

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        print("=== Testing FTS Accuracy with OCR Corpus (UTF-8 output) ===")
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

        # Read OCR files - limit to first 10 files for speed and to see a variety
        inserted = 0
        files_to_process = []
        for ext in ('*.txt', '*.md'):
            files_to_process.extend(Path(OCR_ROOT).rglob(ext))
        # Sort to get consistent order
        files_to_process.sort()
        # Limit to 10 files
        files_to_process = files_to_process[:10]
        for file_path in files_to_process:
            try:
                # Read file content
                raw_bytes = file_path.read_bytes()
                try:
                    content = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    content = raw_bytes.decode('utf-8', errors='replace')
                # Insert into temporary table
                await conn.execute('''
                    INSERT INTO ocr_fts_test (file_name, content)
                    VALUES ($1, $2);
                ''', file_path.name, content)
                inserted += 1
                if inserted <= 5:  # Print first 5 files
                    print(f"  Inserted: {file_path.name} ({len(content)} chars)")
            except Exception as e:
                print(f"  Error inserting {file_path}: {e}")
        print(f"[OK] Inserted {inserted} files from OCR corpus (limited to 10 for test).")

        # Test FTS queries
        print("\n=== FTS Accuracy Tests ===")
        # We'll test a set of Vietnamese words, both accented and unaccented
        test_cases = [
            # (query, description, expected_note)
            ('cong', 'unaccented "cong"', 'Should match only if text has "cong" without accent'),
            ('công', 'accented "công"', 'Should match only if text has "công" with accent'),
            ('luat', 'unaccented "luat"', 'Should match only if text has "luat" without accent'),
            ('luật', 'accented "luật"', 'Should match only if text has "luật" with accent'),
            ('dat', 'unaccented "dat"', "Should NOT match 'đất' (accented) with simple dict"),
            ('đất', 'accented "đất"', 'Should match only if text has "đất" with accent'),
            ('vietnam', 'unaccented foreign word', 'Likely no matches in Vietnamese legal texts'),
            ('luật & đất', 'phrase AND: "luật & đất"', 'Should match documents containing both words'),
        ]

        for query, description, note in test_cases:
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
                        # Truncate snippet and replace newlines for cleaner output
                        snippet = r['snippet'].replace('\n', ' ').strip()
                        if len(snippet) > 150:
                            snippet = snippet[:150] + '...'
                        # Try to print as is (should work if console is UTF-8)
                        print(f'    [{r["file_name"]}] {snippet} (rank={r["rank"]:.4f})')
            except Exception as e:
                print(f'  Error executing query: {e}')

        # Show search_vector for a few rows to see what tokens are extracted
        print('\n=== Sample search_vector tokens ===')
        rows = await conn.fetch('''
            SELECT id, file_name, search_vector
            FROM ocr_fts_test
            LIMIT 3;
        ''')
        for r in rows:
            print(f'  ID: {r["id"]}, File: {r["file_name"]}')
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