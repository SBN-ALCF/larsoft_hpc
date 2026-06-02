import sqlite3
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import csv
import io

DB_FILE = "ifbeam.db"
PORT = 8000

class IFBeamHandler(BaseHTTPRequestHandler):
    def send_500_error(self, is_head=False):
        self.send_response(500)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        html = (
            "<!doctype html>\n"
            "<html lang=en>\n"
            "<title>500 Internal Server Error</title>\n"
            "<h1>Internal Server Error</h1>\n"
            "<p>The server encountered an internal error and was unable to complete your request. "
            "Either the server is overloaded or there is an error in the application.</p>\n"
        ).encode('utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        if not is_head:
            self.wfile.write(html)

    def do_HEAD(self):
        self.do_GET(is_head=True)

    def do_GET(self, is_head=False):
        parsed_path = urllib.parse.urlparse(self.path)
        
        if parsed_path.path != '/ifbeam/data/data':
            self.send_response(404)
            self.end_headers()
            if not is_head:
                self.wfile.write(b"Not Found")
            return
            
        qs = urllib.parse.parse_qs(parsed_path.query)
        bundle = qs.get('b', [''])[0]
        t0_str = qs.get('t0', [''])[0]
        t1_str = qs.get('t1', [''])[0]
        
        # Real server defaults to csv and ignores unsupported f formats
        # Missing or invalid parameters result in a standard 500 error page
        if not bundle or not t0_str or not t1_str:
            self.send_500_error(is_head)
            return
            
        try:
            t0 = float(t0_str)
            t1 = float(t1_str)
        except ValueError:
            self.send_500_error(is_head)
            return
            
        # Convert t0 and t1 to milliseconds
        # Include a 2 second (2000 ms) safety buffer to match the live API's boundary-crossing inclusion
        t0_ms = int(t0 * 1000) - 2000
        t1_ms = int(t1 * 1000) + 2000
        
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT timestamp, name, units, value 
                FROM data 
                WHERE bundle = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp ASC, name ASC
            """, (bundle, t0_ms, t1_ms))
            
            rows = cursor.fetchall()
            conn.close()
            
            # Write to CSV string
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(['timestamp', 'name', 'units', 'value(s)'])
            for row in rows:
                # Format to exactly match the API's CSV output
                writer.writerow(row)
                
            # Use carriage returns as well to match curl output exactly if needed, 
            # though python's csv module uses CRLF by default on windows, we can leave as is.
            csv_data = output.getvalue().replace('\r\n', '\n').encode('utf-8')
            
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain; charset=UTF-8')
            self.send_header('Content-Length', str(len(csv_data)))
            self.end_headers()
            if not is_head:
                self.wfile.write(csv_data)
            
        except Exception as e:
            self.send_500_error(is_head)

def run(server_class=HTTPServer, handler_class=IFBeamHandler, port=PORT):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting mock ifbeam server on port {port}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print(f"Stopped mock ifbeam server")

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="ifbeam mock server")
    parser.add_argument("--db", default="ifbeam.db", help="Path to SQLite database file (default: ifbeam.db)")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the server on (default: 8000)")
    args = parser.parse_args()
    
    DB_FILE = args.db
    PORT = args.port
    
    run(port=PORT)
