"""Dependency-free MCP stdio adapter, pinned to protocol 2025-06-18.

Only initialization, ping and tools/list/call are supported. No HTTP listener,
sampling, wallet access, subscriptions or remote authentication is advertised.
Checks execute serially with the controller's command timeout.
"""
import argparse
import json
import sys

from service import Service, compact

PROTOCOL = "2025-06-18"
MAX_MESSAGE = 256 * 1024


def error(identifier, code, message):
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


class StdioServer:
    def __init__(self, service):
        self.service = service
        self.initialized = False
        self.negotiated = False

    def handle(self, request):
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            return error(None, -32600, "Invalid JSON-RPC request")
        method, identifier = request["method"], request.get("id")
        if "id" not in request:
            if method == "notifications/initialized" and self.negotiated:
                self.initialized = True
            return None
        if not isinstance(identifier, (str, int)) or isinstance(identifier, bool):
            return error(None, -32600, "Invalid request id")
        params = request.get("params", {})
        if not isinstance(params, dict):
            return error(identifier, -32602, "Parameters must be an object")
        if method == "initialize":
            if self.negotiated:
                return error(identifier, -32600, "Already initialized")
            if not isinstance(params.get("protocolVersion"), str) or not isinstance(params.get("clientInfo"), dict) or not isinstance(params.get("capabilities"), dict):
                return error(identifier, -32602, "Invalid initialization parameters")
            self.negotiated = True
            result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                      "serverInfo": {"name": "checkgate", "version": "0.2.0"}}
        elif method == "ping":
            result = {}
        elif not self.initialized:
            return error(identifier, -32002, "Initialize the connection first")
        elif method == "tools/list":
            result = {"tools": self.service.tools()}
        elif method == "tools/call":
            if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments", {}), dict):
                return error(identifier, -32602, "Invalid tool parameters")
            outcome = self.service.call(params["name"], params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": compact(outcome)}],
                      "structuredContent": outcome,
                      "isError": outcome.get("decision") == "blocked" or outcome.get("result") in ("failed", "inputs_changed")}
        else:
            return error(identifier, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    def serve(self, source, destination):
        while True:
            line = source.readline(MAX_MESSAGE + 1)
            if not line:
                break
            if len(line) > MAX_MESSAGE:
                while line and not line.endswith(b"\n"):
                    line = source.readline(MAX_MESSAGE + 1)
                response = error(None, -32600, "Message exceeds the transport limit")
            else:
                try:
                    request = json.loads(line.decode("utf-8"))
                except (ValueError, UnicodeError):
                    response = error(None, -32700, "Invalid JSON")
                else:
                    try:
                        response = self.handle(request)
                    except Exception:
                        # A local JSON/configuration error is not a wire parse error.
                        # Keep stdout clean and preserve the client's request identity.
                        response = error(request.get("id") if isinstance(request, dict) else None, -32603, "Local server error")
            if response is not None:
                destination.write((json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8"))
                destination.flush()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--evidence-dir")
    args = parser.parse_args()
    StdioServer(Service(args.root, args.evidence_dir)).serve(sys.stdin.buffer, sys.stdout.buffer)
