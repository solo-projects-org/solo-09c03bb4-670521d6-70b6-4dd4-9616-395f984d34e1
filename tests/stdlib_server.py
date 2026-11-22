"""本地离线自测用 HTTP 垫片（仅标准库）。

与 app/main.py 暴露完全相同的路由与 JSON 契约，
用于在没有 FastAPI/uvicorn 的环境中做网页级自测；
Docker 镜像内仍由真正的 FastAPI (app.main:app) 提供服务。
"""
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db as dbmod  # noqa: E402
from app import services  # noqa: E402

DB_PATH = os.environ.get("CHRONO_DB_PATH",
                         os.path.join(os.path.dirname(os.path.dirname(
                             os.path.abspath(__file__))), "data", "chrono.db"))
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "app", "static")

conn = dbmod.connect(DB_PATH)
dbmod.init_db(conn)
DB_LOCK = threading.Lock()  # sqlite 连接串行化，保证事务隔离

CT = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = payload if isinstance(payload, bytes) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(STATIC_DIR, "index.html"), "rb") as f:
                self._send(200, f.read(), CT[".html"])
        elif path.startswith("/static/"):
            name = os.path.basename(path)
            fp = os.path.join(STATIC_DIR, name)
            if not os.path.exists(fp):
                self._send(404, {"error": "not found"})
                return
            ext = os.path.splitext(name)[1]
            with open(fp, "rb") as f:
                self._send(200, f.read(), CT.get(ext, "application/octet-stream"))
        elif path == "/api/state":
            with DB_LOCK:
                self._send(200, services.full_state(conn))
        elif path == "/api/chronology":
            with DB_LOCK:
                self._send(200, services.current_chronology(conn))
        elif path == "/api/submissions":
            with DB_LOCK:
                rows = dbmod.list_submissions(conn)
                self._send(200, [
                    {"id": r["id"], "accepted": bool(r["accepted"]), "reason": r["reason"],
                     "created_at": r["created_at"], "detail": services._safe_json(r["detail"])}
                    for r in rows])
        elif path == "/api/proofs":
            with DB_LOCK:
                self._send(200, services.proof_rows(conn))
        elif path.startswith("/api/proofs/") and path.endswith("/csv"):
            try:
                proof_id = int(path[len("/api/proofs/"):-len("/csv")])
            except ValueError:
                self._send(404, {"error": "not found"})
                return
            try:
                with DB_LOCK:
                    filename, data = services.build_proof_csv(conn, proof_id)
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except services.ProofError as exc:
                self._send(exc.status, {"error": exc.to_dict()})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path not in ("/api/submissions", "/api/proofs"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/proofs":
                try:
                    outbound = int(payload.get("outbound_layer_id"))
                    evids = payload.get("evidence_ids")
                    if not isinstance(evids, list) or not evids:
                        raise ValueError
                except (TypeError, ValueError):
                    self._send(422, {"error": {
                        "code": "bad_proof_request",
                        "message": "需要 outbound_layer_id 与非空 evidence_ids 数组"}})
                    return
                with DB_LOCK:
                    result = services.create_proof(conn, outbound, evids)
                self._send(200, result)
                return
            actions = payload.get("actions")
            if not isinstance(actions, list) or not actions:
                self._send(422, {"error": {"code": "empty_submission",
                                           "message": "提交必须包含至少一个动作"}})
                return
            with DB_LOCK:
                result = services.submit(conn, actions)
            self._send(200 if result["accepted"] else 409, result)
        except services.ProofError as exc:
            self._send(exc.status, {"error": exc.to_dict()})
        except services.ValidationError as exc:
            self._send(422, {"error": exc.to_dict()})
        except json.JSONDecodeError as exc:
            self._send(422, {"error": {"code": "bad_json", "message": str(exc)}})

    def log_message(self, fmt, *args):  # 安静一点
        sys.stderr.write("[shim] " + fmt % args + "\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"stdlib shim listening on http://127.0.0.1:{port} (db={DB_PATH})")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
