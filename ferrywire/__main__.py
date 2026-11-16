import argparse
import base64
import json
import sys
from .network import receive, send
from .protocol import ProtocolError, require, MAX_BYTES


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ProtocolError(message)


def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def run():
    p = Parser(
        description="Ferrywire loopback-only UDP protocol. No authentication, encryption, file writes or Internet transport support."
    )
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser(
        "receive",
        help="one in-memory transfer, then JSON/base64; ready JSON emitted first",
    )
    r.add_argument("--port", type=int, default=0)
    r.add_argument("--timeout-ms", type=int, default=35000)
    s = sub.add_parser("send", help="read up to16KiB from stdin; send only to127.0.0.1")
    s.add_argument("--port", type=int, required=True)
    a = p.parse_args()
    if a.command == "send":
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        require(len(data) <= MAX_BYTES, "message exceeds 16384 bytes")
        emit(send(data, a.port))
    else:
        data, result = receive(
            a.port,
            lambda port: emit({"ready": True, "address": "127.0.0.1", "port": port}),
            a.timeout_ms,
        )
        emit({**result, "base64": base64.b64encode(data).decode("ascii")})


if __name__ == "__main__":
    try:
        run()
    except (ProtocolError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=True), file=sys.stderr)
        sys.exit(2)
