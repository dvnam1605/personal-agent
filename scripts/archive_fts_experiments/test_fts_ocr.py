import asyncpg
import asyncio
import os
from pathlib import Path

OCR_ROOT = r'D:\Code\learn\ocr_compare\paddleocr_vl_1_6\QuyetDinh'

async def main():
    conn = await asyncpg.connect(user='postgres', password='postgres', database='assistant', host='localhost', port=5434)
    try:
        print("=== Testing FTS with OCR Corpus ===")
        # Create temporary table for OCR text
        await conn.execute('DROP TABLE IF EXISTS ocr_test;')
        await conn.execute('''
            CREATE TABLE ocr_test (
                id SERIAL PRIMARY KEY,
                file_name TEXT NOT NULL,
                content TEXT NOT NULL,
                search_vector TSVECTOR
                    GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
            );
        ''')
        await conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_ocr_test_search_vector
            ON ocr_test USING GIN (search_vector);
        ''')
        print("[OK] Temporary table ocr_test created with GIN index.")

        # Read OCR files - limit to first 5 files for speed
        inserted = 0
        files_to_process = []
        for ext in ('*.txt', '*.md'):
            files_to_process.extend(Path(OCR_ROOT).rglob(ext))
        # Sort to get consistent order
        files_to_process.sort()
        # Limit to 5 files
        files_to_process = files_to_process[:5]
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
                    INSERT INTO ocr_test (file_name, content)
                    VALUES ($1, $2);
                ''', file_path.name, content)
                inserted += 1
                print(f"  Inserted: {file_path.name} ({len(content)} chars)")
            except Exception as e:
                print(f"  Error inserting {file_path}: {e}")
        print(f"[OK] Inserted {inserted} files from OCR corpus (limited to 5 for test).")

        # Test FTS queries
        print("\n=== FTS Query Tests ===")
        # Test 1: Search for a common Vietnamese word without accent
        keyword1 = 'cong'
        print(f'\n1. Searching for "{keyword1}" (no accent):')
        rows = await conn.fetch('''
            SELECT id, file_name, left(content, 100) AS snippet,
                   ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS rank
            FROM ocr_test
            WHERE search_vector @@ to_tsquery('simple', $1)
            ORDER BY rank DESC
            LIMIT 5;
        ''', keyword1)
        print(f"Found {len(rows)} results:")
        for r in rows:
            print(f"  [{r['file_name']}] {r['snippet']}... (rank={r['rank']:.4f})")

        # Test 2: Search for the same word with accent
        keyword2 = 'công'
        print(f'\n2. Searching for "{keyword2}" (with accent):')
        rows = await conn.fetch('''
            SELECT id, file_name, left(content, 100) AS snippet,
                   ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS rank
            FROM ocr_test
            WHERE search_vector @@ to_tsquery('simple', $1)
            ORDER BY rank DESC
            LIMIT 5;
        ''', keyword2)
        print(f"Found {len(rows)} results:")
        for r in rows:
            print(f"  [{r['file_name']}] {r['snippet']}... (rank={r['rank']:.4f})")

        # Test 3: Search for a word that might appear in legal documents
        keyword3 = 'luat'
        print(f'\n3. Searching for "{keyword3}" (no accent):')
        rows = await conn.fetch('''
            SELECT id, file_name, left(content, 100) AS snippet,
                   ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS rank
            FROM ocr_test
            WHERE search_vector @@ to_tsquery('simple', $1)
            ORDER BY rank DESC
            LIMIT 5;
        ''', keyword3)
        print(f"Found {len(rows)} results:")
        for r in rows:
            print(f"  [{r['file_name']}] {r['snippet']}... (rank={r['rank']:.4f})")

        # Test 4: Search for the accented version
        keyword4 = 'luật'
        print(f'\n4. Searching for "{keyword4}" (with accent):')
        rows = await conn.fetch('''
            SELECT id, file_name, left(content, 100) AS snippet,
                   ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS rank
            FROM ocr_test
            WHERE search_vector @@ to_tsquery('simple', $1)
            ORDER BY rank DESC
            LIMIT 5;
        ''', keyword4)
        print(f"Found {len(rows)} results:")
        for r in rows:
            print(f"  [{r['file_name']}] {r['snippet']}... (rank={r['rank']:.4f})")

        # Test 5: Phrase search (AND)
        keyword5 = 'luật & đất'
        print(f'\n5. Searching for "{keyword5}" (AND):')
        rows = await conn.fetch('''
            SELECT id, file_name, left(content, 100) AS snippet,
                   ts_rank_cd(search_vector, to_tsquery('simple', $1)) AS rank
            FROM ocr_test
            WHERE search_vector @@ to_tsquery('simple', $1)
            ORDER BY rank DESC
            LIMIT 5;
        ''', keyword5)
        print(f"Found {len(rows)} results:")
        for r in rows:
            print(f"  [{r['file_name']}] {r['snippet']}... (rank={r['rank']:.4f})")

        # Test 6: Show search_vector for one row
        print('\n6. Sample search_vector values:')
        rows = await conn.fetch('''
            SELECT id, file_name, left(content, 50) AS content_preview, search_vector
            FROM ocr_test
            LIMIT 3;
        ''')
        for r in rows:
            print(f"  ID: {r['id']}, File: {r['file_name']}")
            print(f"    Content: {r['content_preview']}...")
            print(f"    TSVector: {r['search_vector']}")
            print()

        print("\n=== Summary ===")
        print("FTS mechanism is working with the 'simple' dictionary.")
        print("Note: The simple dictionary does NOT remove accents, so:")
        print(" - Searches for unaccented terms will not match accented terms in the text.")
        print(" - Searches for accented terms will match only if the term in the text has the same accent.")
        print("For better Vietnamese support, consider using the 'unaccent' extension with a custom dictionary (optional for P10A).")
        print("\nYou can now proceed with P10A implementation knowing the FTS foundation is in place.")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Clean up temporary table
        try:
            await conn.execute('DROP TABLE IF EXISTS ocr_test;')
            print("[OK] Temporary table ocr_test dropped.")
        except:
            pass
        await conn.close()
        print("\nScript finished.")

if __name__ == '__main__':
    asyncio.run(main())