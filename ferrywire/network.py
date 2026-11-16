"""Actual UDP adapters. IPv4 127.0.0.1 only, one transfer, bounded memory/time."""

import secrets
import socket
import time
from .protocol import (
    Receiver,
    Sender,
    ProtocolError,
    require,
    DEADLINE,
    DALLY,
    MAX_WIRE,
)

MAX_DATAGRAMS = 4096


def port(value, zero=False):
    require(
        type(value) is int and (0 if zero else 1) <= value <= 65535, "invalid UDP port"
    )
    return value


def clock(start):
    return int((time.monotonic() - start) * 1000)


def send(data, destination_port):
    port(destination_port)
    sender = Sender(data, secrets.token_bytes(16))
    count = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        peer = ("127.0.0.1", destination_port)
        start = time.monotonic()
        while not sender.done and not sender.error:
            now = clock(start)
            raw = sender.poll(now)
            if raw is not None:
                sock.sendto(raw, peer)
            if sender.error:
                break
            timeout = max(0.001, min(sender.due - now, DEADLINE - now, 100) / 1000)
            sock.settimeout(timeout)
            try:
                raw, source = sock.recvfrom(MAX_WIRE + 1)
            except socket.timeout:
                continue
            count += 1
            if count > MAX_DATAGRAMS:
                raise ProtocolError(
                    "incoming datagram budget exceeded; delivery uncertain"
                )
            if source != peer:
                sender.ignored += 1
                continue
            sender.receive(raw, clock(start))
    if not sender.done:
        raise ProtocolError(sender.error or "transfer failed; delivery uncertain")
    return {
        "confirmed": True,
        "bytes": len(data),
        "sent": sender.sent,
        "retransmissions": sender.retransmissions,
        "ignored": sender.ignored,
        "meaning": "peer acknowledged verified in-memory delivery, not disk persistence or authentication",
    }


def receive(bind_port=0, ready=None, timeout_ms=35000):
    port(bind_port, True)
    require(
        type(timeout_ms) is int and 100 <= timeout_ms <= 35000,
        "invalid receiver timeout",
    )
    receiver = Receiver()
    peer = None
    count = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", bind_port))
        if ready:
            ready(sock.getsockname()[1])
        start = time.monotonic()
        while clock(start) < timeout_ms:
            now = clock(start)
            if receiver.complete and now - receiver.completed_at >= DALLY:
                break
            remaining = min(100, timeout_ms - now)
            if receiver.complete:
                remaining = min(remaining, DALLY - (now - receiver.completed_at))
            sock.settimeout(max(remaining / 1000, 0.001))
            try:
                raw, source = sock.recvfrom(MAX_WIRE + 1)
            except socket.timeout:
                continue
            if clock(start) >= timeout_ms:
                break
            count += 1
            if count > MAX_DATAGRAMS:
                raise ProtocolError(
                    "receiver datagram budget exceeded; inspect sender separately"
                )
            if source[0] != "127.0.0.1" or (peer is not None and source != peer):
                receiver.ignored += 1
                continue
            response = receiver.receive(raw, clock(start))
            if response is not None:
                if peer is None:
                    peer = source
                sock.sendto(response, source)
        require(receiver.complete, "receiver timed out before verified delivery")
    return receiver.delivered, {
        **receiver.snapshot(),
        "datagrams": count,
        "meaning": "verified in-memory bytes only; sender confirmation can still be lost",
    }
