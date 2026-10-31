"""Credential-free local RPC bridge to the host's existing connected MCP tools.

The trusted host services next/respond using its tool connection. Models never
receive this private socket. Unlike the retired mailbox, no response files or
credentials are exchanged. A lost host returns a bounded transport failure.
"""
import json
import os
from pathlib import Path
import queue
import socket
import socketserver
import sys
import threading
import uuid


def exchange(path, value):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(180)
        connection.connect(str(path))
        connection.sendall(json.dumps(value).encode() + b"\n")
        with connection.makefile("r") as response:
            return json.loads(response.readline())


def serve(path):
    pending = queue.Queue()
    waiting = {}
    lock = threading.Lock()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            request = json.loads(self.rfile.readline(2 * 1024 * 1024))
            operation = request["operation"]
            if operation == "call":
                identifier = str(uuid.uuid4())
                result = queue.Queue(maxsize=1)
                with lock:
                    waiting[identifier] = result
                pending.put({"id": identifier, **request["request"]})
                try:
                    response = result.get(timeout=175)
                except queue.Empty:
                    response = {"error": "Connected host timed out; retry the same accepted key"}
                finally:
                    with lock:
                        waiting.pop(identifier, None)
            elif operation == "next":
                try:
                    response = pending.get_nowait()
                except queue.Empty:
                    response = None
            elif operation == "respond":
                with lock:
                    result = waiting.get(request["id"])
                    if result is not None:
                        result.put_nowait(request["response"])
                response = {"delivered": result is not None}
            else:
                response = {"error": "Unsupported host operation"}
            self.wfile.write(json.dumps(response).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True

    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with Server(str(path), Handler) as server:
        os.chmod(path, 0o600)
        try:
            server.serve_forever()
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    operation, path = sys.argv[1:3]
    if operation == "serve":
        serve(path)
    else:
        payload = {} if operation == "next" else json.load(sys.stdin)
        request = {"operation": operation}
        if operation == "call":
            request["request"] = payload
        else:
            request.update(payload)
        print(json.dumps(exchange(path, request)))
