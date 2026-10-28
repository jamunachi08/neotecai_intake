"""Minimal LocalAI stand-in: validates request shape, returns the canned answer in /srv/mock/next.json."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        if self.path == "/v1/models":
            return self._send(200, {"data": [{"id": "qwen3.6-35b-a3b"}, {"id": "bge-m3"}]})
        self._send(404, {})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        json.dump({"auth": self.headers.get("Authorization"), "req": req}, open("/srv/mock/last_request.json", "w"))
        errs = []
        if req.get("model") != "qwen3.6-35b-a3b": errs.append("model")
        if req.get("reasoning_effort") != "none": errs.append("reasoning_effort")
        rf = req.get("response_format") or {}
        if rf.get("type") != "json_schema" or "schema" not in rf.get("json_schema", {}): errs.append("response_format")
        content = req["messages"][1]["content"]
        imgs = [c for c in content if c["type"] == "image_url" and c["image_url"]["url"].startswith("data:image/jpeg;base64,")]
        if not imgs: errs.append("image")
        if errs:
            return self._send(400, {"error": "bad request: " + ",".join(errs)})
        answer = open("/srv/mock/next.json").read()
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": answer}}]})

    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 8089), H).serve_forever()
