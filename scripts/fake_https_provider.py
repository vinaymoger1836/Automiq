"""Synthetic HTTPS provider for the Phase 2 connector E2E overlay."""

import json
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock


class Handler(BaseHTTPRequestHandler):
    retry_count = 0
    lock = Lock()

    def log_message(self, format: str, *args: object) -> None:
        # Do not log request paths or headers from workflow data.
        return

    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/retry":
            with self.lock:
                type(self).retry_count += 1
                attempt = type(self).retry_count
            if attempt <= 2:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = {"ok": True, "attempt": attempt}
        elif self.path == "/large":
            body = {"ok": True, "padding": "x" * 9000}
        elif self.path == "/badtype":
            body = {"ok": "text is not an allowed output type"}
        elif self.path == "/slow":
            time.sleep(3)
            body = {"ok": True}
        elif self.path == "/status":
            body = {"ok": True, "unselected": "must-not-persist"}
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        try:
            self.wfile.write(encoded)
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/test-tls/cert.crt", "/test-tls/key.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
