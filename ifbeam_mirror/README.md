# ifbeam Local Mock Server & Web Scraper

This package provides a local mock server and web scraper for the `ifbeam` database. It is designed to run inside and outside containers with just Python 3.10+ standard libraries.

## Components

- **Scraper**: Parallel data fetching with rate-limiting.
- **Server**: Emulates the production `ifbeam` API including exact CSV columns, headers, `GET`/`HEAD` requests, Nginx/Flask-style 500 HTML error responses on malformed/missing params, and a custom database reference CLI.
- **Statistical Verification Tool**: Connects to both mock and real servers to check 100% data and header matches for database queries.

---

## 1. Scraping ifbeam Data

The scraper CLI parses URLs to populate the local SQLite database.

### Usage

```bash
python3 scraper.py [options]
```

### CLI Arguments
- `--db <path>`: Path to target SQLite database file (default: `ifbeam.db`).
- `--url <url>`: Scrape a single URL.
- `--file <path>`: Scrape a file containing a list of URLs (one per line).
- `--bundle <name>`: Optional bundle name. If omitted when specifying `--t0` and `--t1`, the scraper automatically scrapes all four standard bundles.
- `--t0 <seconds>`, `--t1 <seconds>`: Explicit time range in float seconds since epoch.
- `--workers <integer>`: Number of concurrent workers for parallel scraping (default: `1`).
- `--delay <seconds>`: Rate-limiting delay in seconds between sequential requests per worker (default: `0.0`).
- `--chunk-size <seconds>`: Chunk size in seconds to segment large time window requests (default: `3600.0` / 1 hour). Set to 0 to disable chunking. Highly useful for parallelizing long intervals (e.g. months) into parallel hourly downloads.

### Examples

1. **Scraping with Multiprocessing (Parallel Workers)**:
   Scrape an entire file of URLs using 8 parallel threads:
   ```bash
   python3 scraper.py --file references/urls.txt --workers 8
   ```

2. **Scraping an Explicit Beam Folder Range**:
   Scrape the `BoosterNeutrinoBeam_read` beam folder for a 30-minute window:
   ```bash
   python3 scraper.py --bundle BoosterNeutrinoBeam_read --t0 1744785000.000 --t1 1744786800.000 --db custom.db
   ```

3. **Scraping in Parallel Hourly Chunks**:
   Scrape a full 24-hour range of `BNB_monitor` by segmenting it into parallel 1-hour chunks fetched by 6 threads:
   ```bash
   python3 scraper.py --bundle BNB_monitor --t0 1744785000.000 --t1 1744871400.000 --workers 6 --chunk-size 3600
   ```

4. **Scraping All Four Standard Bundles Simultaneously**:
   Scrape a time range for all four standard bundles in parallel:
   ```bash
   python3 scraper.py --t0 1744785000.000 --t1 1744786800.000 --workers 4
   ```

---

## 2. Mock Server

`server.py` sets up a lightweight HTTP server on the configured port that mimics the live `ifbeam` API. It references the populated SQLite database file.

### Usage

```bash
python3 server.py [options]
```

### CLI Arguments
- `--db <path>`: Path to the SQLite database file to read from (default: `ifbeam.db`).
- `--port <number>`: Port number to host the server on (default: `8000`).

### Running the Server
To run the server pointing to a custom database and port:
```bash
python3 server.py --db my_ifbeam.db --port 8080
```

### Supported behaviors
- **API**: Listens on `/ifbeam/data/data` matching query parameters `b`, `t0`, `t1`, and `f`.
- **Content-Type**: Always returns `Content-Type: text/plain; charset=UTF-8` on successful CSV queries, duplicating live Nginx/Werkzeug header mapping.
- **HEAD Requests**: Handles HTTP `HEAD` requests correctly, returning matching headers and content lengths without sending bodies.
- **Boundary Inclusion Buffer**: Leverages a `2000` ms (2-second) query buffer internally to cover spill-boundary crossing data points seamlessly.
- **Error Emulation**: Responds with standard Flask/Werkzeug `500 Internal Server Error` pages and `text/html; charset=utf-8` on missing/malformed parameter formats.

---

## 3. Verifying Database and Server Parity

The `test_parity.py` utility checks the real `ifbeam` and mock server for identical responses. It queries random data intervals stored in the local SQLite database, fetches both the real production CSV and local mock CSV, and performs a line-by-line comparison of data content and headers.

### Usage

```bash
python3 test_parity.py [options]
```

### CLI Arguments
- `--db <path>`: Path to SQLite database file to get samples from (default: `ifbeam.db`).
- `--samples <integer>`: Number of sample time windows to select and test per bundle (default: `2`).
- `--port <number>`: Port where the local mock server is currently running (default: `8000`).

### Running a Parity Test
Ensure your local server is running in the background, then execute:
```bash
python3 test_parity.py --samples 3
```

---

## 4. Creating a SquashFS Image

For read-only overlays in containerized environments (e.g. Singularity/Apptainer) on HPC systems, a convenience utility `make_squashfs.py` is included. It copies the SQLite database file into a custom directory structure matching your target mount path, and invokes `mksquashfs` with configured compression.

### Usage
```bash
python3 make_squashfs.py [options]
```

### CLI Arguments
- `--db <path>`: Path to the source SQLite database file (default: `ifbeam.db`).
- `--out <path>`: Path to output SquashFS image file (default: `ifbeam.squashfs`).
- `--comp <algorithm>`: Compression algorithm, e.g. `gzip`, `xz`, `zstd`, `lz4` (default: `gzip`).
- `--mount-path <path>`: The absolute mount path inside the container structure (e.g. `/var/lib/ifbeam`). If empty, defaults to the root `/` of the filesystem.

### Execution Example
```bash
python3 make_squashfs.py --mount-path /var/lib/ifbeam --comp xz
```
