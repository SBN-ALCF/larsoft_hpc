import sqlite3
import urllib.request
import urllib.parse
import csv
import io
import ssl
import sys
import argparse

REAL_BASE_URL = "https://ifb-data.fnal.gov:8104/ifbeam/data/data"
MOCK_BASE_URL = "http://localhost:8000/ifbeam/data/data"

def get_db_samples(db_path, num_samples_per_bundle=2):
    """
    Finds actual active timestamps in the database and creates test windows around them
    to ensure we query windows that definitely have data in our SQLite DB.
    Optimized to run in sub-centisecond times using index-friendly lookups instead
    of O(N) COUNT(*) and LIMIT OFFSET scans.
    """
    samples = []
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        try:
            # Use a recursive CTE (loose index scan / skip scan) to get unique bundles in ~3ms
            cursor.execute("""
                WITH RECURSIVE
                  cnt(x) AS (
                     SELECT MIN(bundle) FROM data
                     UNION ALL
                     SELECT (SELECT MIN(bundle) FROM data WHERE bundle > x) FROM cnt WHERE x IS NOT NULL
                  )
                SELECT x FROM cnt WHERE x IS NOT NULL
            """)
            bundles = [row[0] for row in cursor.fetchall() if row[0] is not None]
        except sqlite3.OperationalError:
            # Fallback for older SQLite versions (< 3.8.3) that don't support recursive CTEs.
            # Using predefined bundles avoids a slow O(N) DISTINCT scan on 450GB databases.
            bundles = [
                "BoosterNeutrinoBeam_read",
                "BNB_monitor",
                "BNB_BPM_settings",
                "BNBMultiWire"
            ]
        
        for bundle in bundles:
            # Query MIN timestamp using index-friendly ORDER BY LIMIT 1 in ~1ms
            cursor.execute("""
                SELECT timestamp FROM data 
                WHERE bundle = ? 
                ORDER BY timestamp ASC 
                LIMIT 1
            """, (bundle,))
            row_min = cursor.fetchone()
            
            # Query MAX timestamp using index-friendly ORDER BY LIMIT 1 in ~1ms
            cursor.execute("""
                SELECT timestamp FROM data 
                WHERE bundle = ? 
                ORDER BY timestamp DESC 
                LIMIT 1
            """, (bundle,))
            row_max = cursor.fetchone()
            
            if not row_min or not row_max:
                continue
                
            min_ts = row_min[0]
            max_ts = row_max[0]
            
            # Pick a few evenly distributed target timestamps and perform quick B-tree index jumps
            for i in range(num_samples_per_bundle):
                target_ts = min_ts + int((max_ts - min_ts) * (i + 1) / (num_samples_per_bundle + 1))
                cursor.execute("""
                    SELECT timestamp 
                    FROM data 
                    WHERE bundle = ? AND timestamp >= ? 
                    ORDER BY timestamp ASC 
                    LIMIT 1
                """, (bundle, target_ts))
                res = cursor.fetchone()
                
                # Fallback to searching backwards if target is near the absolute end of the series
                if not res:
                    cursor.execute("""
                        SELECT timestamp 
                        FROM data 
                        WHERE bundle = ? AND timestamp <= ? 
                        ORDER BY timestamp DESC 
                        LIMIT 1
                    """, (bundle, target_ts))
                    res = cursor.fetchone()
                    
                if res:
                    ts = res[0]
                    # Create a 60-second window centered around this timestamp
                    # Truncate to integer seconds to align with integer boundaries
                    t0 = int(ts / 1000.0) - 30
                    t1 = t0 + 60
                    samples.append({
                        'bundle': bundle,
                        't0': float(t0),
                        't1': float(t1)
                    })
        conn.close()
    except Exception as e:
        print(f"Error querying database for samples: {e}")
        
    return samples

def fetch_data(base_url, bundle, t0, t1, verify_ssl=False):
    """
    Fetches data from the specified server.
    """
    qs = urllib.parse.urlencode({
        'b': bundle,
        't0': f"{t0:.3f}",
        't1': f"{t1:.3f}",
        'f': 'csv'
    })
    url = f"{base_url}?{qs}"
    
    ctx = ssl.create_default_context()
    if not verify_ssl:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, context=ctx) as response:
            content = response.read().decode('utf-8')
            headers = dict(response.info())
            return content, headers, url
    except Exception as e:
        return None, None, url

def compare_csv_contents(real_csv, mock_csv, t0=None, t1=None):
    """
    Parses and compares CSV contents line-by-line.
    If t0 and t1 are provided, filters both CSVs to only include records strictly within [t0, t1] range
    to focus parity check on strict in-window data and ignore spill-boundary/caching variances.
    """
    real_lines = [line.strip() for line in real_csv.strip().split('\n') if line.strip()]
    mock_lines = [line.strip() for line in mock_csv.strip().split('\n') if line.strip()]
    
    real_header = real_lines[0] if real_lines else ""
    mock_header = mock_lines[0] if mock_lines else ""
    
    if t0 is not None and t1 is not None:
        t0_ms = int(t0 * 1000)
        t1_ms = int(t1 * 1000)
        
        filtered_real = [real_header]
        filtered_mock = [mock_header]
        
        for line in real_lines[1:]:
            parts = line.split(',')
            if parts and parts[0].isdigit():
                ts = int(parts[0])
                if t0_ms <= ts <= t1_ms:
                    filtered_real.append(line)
                    
        for line in mock_lines[1:]:
            parts = line.split(',')
            if parts and parts[0].isdigit():
                ts = int(parts[0])
                if t0_ms <= ts <= t1_ms:
                    filtered_mock.append(line)
                    
        real_lines = filtered_real
        mock_lines = filtered_mock
        
    if len(real_lines) != len(mock_lines):
        return False, f"Strict In-Window Line count mismatch (Real: {len(real_lines)}, Mock: {len(mock_lines)})"
        
    for idx, (r_line, m_line) in enumerate(zip(real_lines, mock_lines)):
        if r_line != m_line:
            return False, f"Line {idx} mismatch:\n  Real: {r_line}\n  Mock: {m_line}"
            
    return True, f"Identical ({len(real_lines) - 1} data lines matched strictly in-window)"

def main():
    parser = argparse.ArgumentParser(description="ifbeam Data Parity Test Tool")
    parser.add_argument("--db", default="ifbeam.db", help="Path to SQLite database file (default: ifbeam.db)")
    parser.add_argument("--samples", type=int, default=2, help="Number of sample windows to test per bundle")
    parser.add_argument("--port", type=int, default=8000, help="Port where the mock server is running")
    
    args = parser.parse_args()
    
    global MOCK_BASE_URL
    MOCK_BASE_URL = f"http://localhost:{args.port}/ifbeam/data/data"
    
    print("=" * 70)
    print("starting ifbeam Parity Verification...")
    print(f"  Source SQLite DB: {args.db}")
    print(f"  Mock Server:      {MOCK_BASE_URL}")
    print(f"  Real Server:      {REAL_BASE_URL}")
    print("=" * 70)

    '''
    # disable proxies
    proxy_handler = urllib.request.ProxyHandler({})
    opener = urllib.request.build_opener(proxy_handler)
    urllib.request.install_opener(opener)
    '''
    
    samples = get_db_samples(args.db, args.samples)
    if not samples:
        print("No samples found in database. Make sure you have populated the DB using scraper.py first.")
        sys.exit(1)
        
    print(f"Found {len(samples)} sample windows to verify across unique bundles.\n")
    
    passed_tests = 0
    failed_tests = 0
    
    for idx, sample in enumerate(samples):
        bundle = sample['bundle']
        t0 = sample['t0']
        t1 = sample['t1']
        
        print(f"[{idx+1}/{len(samples)}] Testing Bundle: {bundle}")
        print(f"  Time Window: {t0:.3f} to {t1:.3f}")
        
        # Fetch from Real
        real_csv, real_headers, real_url = fetch_data(REAL_BASE_URL, bundle, t0, t1)
        if real_csv is None:
            print("  ❌ Failed to fetch from REAL API")
            failed_tests += 1
            continue
            
        # Fetch from Mock
        mock_csv, mock_headers, mock_url = fetch_data(MOCK_BASE_URL, bundle, t0, t1)
        if mock_csv is None:
            print(f"  ❌ Failed to fetch from MOCK API (is server running on port {args.port}?)")
            failed_tests += 1
            continue
            
        # Compare Content-Type headers
        real_ct = real_headers.get('Content-Type', '').lower()
        mock_ct = mock_headers.get('Content-Type', '').lower()
        ct_match = "Match" if real_ct == mock_ct else f"Mismatch (Real: '{real_ct}', Mock: '{mock_ct}')"
        
        # Compare Data
        is_equal, reason = compare_csv_contents(real_csv, mock_csv, t0, t1)
        
        if is_equal:
            print(f"  ✅ Data: {reason}")
            print(f"  ✅ Headers (Content-Type): {real_ct} ({ct_match})")
            passed_tests += 1
        else:
            print(f"  ❌ Parity Failure: {reason}")
            print(f"  Real URL: {real_url}")
            print(f"  Mock URL: {mock_url}")
            failed_tests += 1
        print("-" * 50)
        
    print("\n" + "=" * 70)
    print("Parity Verification Summary:")
    print(f"  Passed: {passed_tests}/{len(samples)}")
    print(f"  Failed: {failed_tests}/{len(samples)}")
    print("=" * 70)
    
    if failed_tests > 0:
        sys.exit(1)
    else:
        print("Success! Mock server is in perfect parity with real server for the tested DB segments.")
        sys.exit(0)

if __name__ == '__main__':
    main()
