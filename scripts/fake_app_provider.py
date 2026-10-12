"""Synthetic HTTPS callback receiver used only by the custom-app E2E profile."""

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

EVIDENCE = Path("/test-evidence/callbacks.jsonl")
LOCK = Lock()
RETRIES: set[str] = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_POST(self) -> None:
        if self.path not in {"/app-one/events", "/app-two/retry"}:
            self.send_error(404)
            return
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or int(length) > 16_000:
            self.send_error(413)
            return
        body = self.rfile.read(int(length))
        try:
            event = json.loads(body)
            event_id = str(event["id"])
            event_type = str(event["type"])
            if event["specversion"] != "1.0" or not isinstance(event["data"], dict):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            self.send_error(422)
            return
        with LOCK:
            if self.path == "/app-two/retry" and event_id not in RETRIES:
                RETRIES.add(event_id)
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
            with EVIDENCE.open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {
                            "path": self.path,
                            "event_id": event_id,
                            "event_type": event_type,
                            "data": event["data"],
                            "timestamp": self.headers.get("X-Automiq-Timestamp", ""),
                            "signature": self.headers.get("X-Automiq-Signature", ""),
                            "body": body.decode("utf-8"),
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8443), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain("/test-tls/cert.crt", "/test-tls/key.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
