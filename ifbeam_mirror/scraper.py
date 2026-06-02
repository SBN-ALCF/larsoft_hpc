import os
import argparse
import sqlite3
import urllib.request
import urllib.parse
import csv
import ssl
import sys
import io
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

DB_FILE = "ifbeam.db"
BASE_URL = "https://ifb-data.fnal.gov:8104/ifbeam/data/data"
db_lock = threading.Lock()
completed_requests = 0
TOTAL_URLS = 0
progress_lock = threading.Lock()

def is_chunk_already_scraped(cursor, bundle, t0, t1):
    """
    Checks the local database using a shared cursor to see if we already have records for this bundle
    within the specified t0 and t1 float seconds range.
    """
    if cursor is None:
        return False
        
    t0_ms = int(t0 * 1000)
    t1_ms = int(t1 * 1000)
    
    try:
        # Query if any record exists in this window
        cursor.execute("""
            SELECT 1 FROM data 
            WHERE bundle = ? AND timestamp >= ? AND timestamp <= ? 
            LIMIT 1
        """, (bundle, t0_ms, t1_ms))
        return cursor.fetchone() is not None
    except sqlite3.Error:
        return False

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS data (
            bundle TEXT,
            timestamp INTEGER,
            name TEXT,
            units TEXT,
            value TEXT
        )
    """)
    # Unique constraint to prevent duplicate inserts of the same data point
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_data_uniq ON data (bundle, timestamp, name)")
    conn.commit()
    conn.close()

def fetch_and_save(url, delay=0.0):
    global completed_requests, TOTAL_URLS
    if delay > 0:
        time.sleep(delay)
        
    print(f"Fetching {url}")
    parsed_url = urllib.parse.urlparse(url)
    qs = urllib.parse.parse_qs(parsed_url.query)
    
    bundle = qs.get("b", [""])[0]
    if not bundle:
        print(f"Warning: No bundle 'b' found in URL {url}")
        with progress_lock:
            completed_requests += 1
        return

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ctx) as response:
            content = response.read().decode('utf-8')
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        with progress_lock:
            completed_requests += 1
            pct = (completed_requests / TOTAL_URLS) * 100 if TOTAL_URLS > 0 else 0
            print(f"[Progress] {completed_requests}/{TOTAL_URLS} requests completed ({pct:.1f}%) | Failed to fetch bundle '{bundle}'")
        return

    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if not header or header != ['timestamp', 'name', 'units', 'value(s)']:
        print(f"Warning: Unexpected or missing header in response: {header}")

    # Use a thread-local connection and lock for safe writing
    conn = sqlite3.connect(DB_FILE, timeout=30)
    count = 0
    with db_lock:
        cursor = conn.cursor()
        for row in reader:
            if len(row) == 4:
                ts_str, name, units, val = row
                try:
                    ts = int(ts_str)
                except ValueError:
                    continue
                
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO data (bundle, timestamp, name, units, value)
                        VALUES (?, ?, ?, ?, ?)
                    """, (bundle, ts, name, units, val))
                    if cursor.rowcount > 0:
                        count += 1
                except sqlite3.Error as e:
                    print(f"Database error: {e}")
                
        conn.commit()
    conn.close()
    
    with progress_lock:
        completed_requests += 1
        pct = (completed_requests / TOTAL_URLS) * 100 if TOTAL_URLS > 0 else 0
        print(f"[Progress] {completed_requests}/{TOTAL_URLS} requests completed ({pct:.1f}%) | Inserted {count} rows for bundle '{bundle}'")

def main():
    global DB_FILE, TOTAL_URLS
    parser = argparse.ArgumentParser(description="ifbeam mock scraper")
    parser.add_argument("--db", default="ifbeam.db", help="Path to SQLite database file (default: ifbeam.db)")
    parser.add_argument("--url", help="Scrape from a single URL")
    parser.add_argument("--file", help="Scrape from a file containing URLs (one per line)")
    parser.add_argument("--bundle", "-b", help="Bundle name (e.g. BoosterNeutrinoBeam_read)")
    parser.add_argument("--t0", help="Start time in seconds (e.g. 1744785000.000)")
    parser.add_argument("--t1", help="End time in seconds (e.g. 1744786800.000)")
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent workers")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay in seconds between requests per worker")
    parser.add_argument("--chunk-size", type=float, default=3600.0, help="Chunk size in seconds to segment large time window requests (default: 3600.0 / 1 hour). Set to 0 to disable chunking.")
    
    args = parser.parse_args()
    DB_FILE = args.db
    init_db()
    
    urls_to_fetch = []
    
    if args.url:
        urls_to_fetch.append(args.url)
    if args.file:
        with open(args.file, 'r') as f:
            for line in f:
                line = line.strip()
                if line:
                    urls_to_fetch.append(line)
    if args.t0 and args.t1:
        try:
            t0 = float(args.t0)
            t1 = float(args.t1)
        except ValueError:
            print("Error: --t0 and --t1 must be numeric values representing epoch seconds.")
            sys.exit(1)
            
        chunk_size = args.chunk_size
        if chunk_size <= 0:
            chunk_size = t1 - t0
            
        DEFAULT_BUNDLES = [
            "BoosterNeutrinoBeam_read",
            "BNB_monitor",
            "BNB_BPM_settings",
            "BNBMultiWire"
        ]
        bundles_to_scrape = [args.bundle] if args.bundle else DEFAULT_BUNDLES
        
        # Segment the time range into non-overlapping chunks
        skipped_chunks = 0
        
        db_conn = None
        db_cursor = None
        if os.path.exists(DB_FILE):
            print("Checking database for already scraped chunks (resuming)...")
            try:
                db_conn = sqlite3.connect(DB_FILE)
                db_cursor = db_conn.cursor()
            except sqlite3.Error:
                pass
                
        for b in bundles_to_scrape:
            current_t0 = t0
            while current_t0 < t1:
                current_t1 = min(current_t0 + chunk_size, t1)
                
                # Check if this chunk is already in our database
                if is_chunk_already_scraped(db_cursor, b, current_t0, current_t1):
                    skipped_chunks += 1
                else:
                    qs = urllib.parse.urlencode({
                        'b': b,
                        't0': f"{current_t0:.3f}",
                        't1': f"{current_t1:.3f}",
                        'f': 'csv'
                    })
                    urls_to_fetch.append(f"{BASE_URL}?{qs}")
                current_t0 = current_t1
                
        if db_conn:
            db_conn.close()
                
        if skipped_chunks > 0:
            print(f"Resuming scrape: skipped {skipped_chunks} chunks already present in SQLite database.")
        
    if not urls_to_fetch:
        if args.t0 and args.t1:
            print("All requested data chunks are already present in the SQLite database. Scrape complete (Up-to-date).")
            sys.exit(0)
        print("Please provide --url, --file, or time window (--t0, --t1).")
        parser.print_help()
        sys.exit(1)
        
    TOTAL_URLS = len(urls_to_fetch)
        
    # Analyze target URLs to find unique enabled beam folders
    unique_bundles = set()
    for url in urls_to_fetch:
        parsed_url = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed_url.query)
        bundle = qs.get("b", [""])[0]
        if bundle:
            unique_bundles.add(bundle)
            
    print("=" * 60)
    print("ifbeam Scraper Configuration:")
    print(f"  Target SQLite DB:       {DB_FILE}")
    print(f"  Workers (Concurrency):  {args.workers}")
    print(f"  Delay between requests: {args.delay}s")
    print(f"  Enabled Beam Folders (Bundles) detected:")
    for b in sorted(unique_bundles):
        print(f"    - {b}")
    print(f"  Total URLs to fetch:    {len(urls_to_fetch)}")
    print("=" * 60)
    print("Starting scrape...")
        
    if args.workers > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [executor.submit(fetch_and_save, url, args.delay) for url in urls_to_fetch]
            for future in as_completed(futures):
                pass
    else:
        for url in urls_to_fetch:
            fetch_and_save(url, args.delay)

if __name__ == "__main__":
    main()
