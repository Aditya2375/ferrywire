"""Original framing and state machines. No sockets, files or wall clock."""

from dataclasses import dataclass
import hashlib
import math
import struct
import zlib

MAGIC = b"FRY1"
START, DATA, FIN, ACK = 1, 2, 3, 4
CHUNK = 512
MAX_BYTES = 16384
MAX_CHUNKS = 32
HEADER = struct.Struct("!4sB16sHHH")
MAX_WIRE = HEADER.size + CHUNK + 4
RTO = 250  # milliseconds, deliberately LOOPBACK-only, not an Internet RTO
MAX_RTO = 1000
ATTEMPTS = 6
DEADLINE = 30000
DALLY = 2000


class ProtocolError(ValueError):
    pass


def require(value, message):
    if not value:
        raise ProtocolError(message)


@dataclass(frozen=True)
class Packet:
    kind: int
    session: bytes
    seq: int
    total: int
    payload: bytes = b""


def validate(p):
    require(type(p.kind) is int and p.kind in {START, DATA, FIN, ACK}, "unknown kind")
    require(
        type(p.session) is bytes and len(p.session) == 16, "session must be 16 bytes"
    )
    require(
        type(p.seq) is int and type(p.total) is int and 0 <= p.total <= MAX_CHUNKS,
        "invalid counters",
    )
    require(type(p.payload) is bytes, "payload must be bytes")
    if p.kind == START:
        require(p.seq == 0 and len(p.payload) == 36, "invalid START")
        size = struct.unpack("!I", p.payload[:4])[0]
        require(
            size <= MAX_BYTES and p.total == math.ceil(size / CHUNK),
            "invalid START size/count",
        )
    elif p.kind == DATA:
        require(1 <= p.seq <= p.total and 1 <= len(p.payload) <= CHUNK, "invalid DATA")
    elif p.kind == FIN:
        require(p.seq == p.total + 1 and not p.payload, "invalid FIN")
    else:
        require(0 <= p.seq <= p.total + 1 and not p.payload, "invalid ACK")
    return p


def encode(p):
    validate(p)
    raw = (
        HEADER.pack(MAGIC, p.kind, p.session, p.seq, p.total, len(p.payload))
        + p.payload
    )
    return raw + struct.pack("!I", zlib.crc32(raw))


def decode(raw):
    require(
        type(raw) is bytes and HEADER.size + 4 <= len(raw) <= MAX_WIRE,
        "invalid datagram length",
    )
    magic, kind, session, seq, total, size = HEADER.unpack(raw[: HEADER.size])
    require(magic == MAGIC, "wrong magic/version")
    require(len(raw) == HEADER.size + size + 4, "declared length mismatch")
    require(zlib.crc32(raw[:-4]) == struct.unpack("!I", raw[-4:])[0], "CRC mismatch")
    return validate(Packet(kind, session, seq, total, raw[HEADER.size : -4]))


def frames(data, session):
    require(
        type(data) is bytes and len(data) <= MAX_BYTES, "message exceeds 16384 bytes"
    )
    total = math.ceil(len(data) / CHUNK)
    return [
        Packet(
            START,
            session,
            0,
            total,
            struct.pack("!I", len(data)) + hashlib.sha256(data).digest(),
        ),
        *[
            Packet(DATA, session, i + 1, total, data[i * CHUNK : (i + 1) * CHUNK])
            for i in range(total)
        ],
        Packet(FIN, session, total + 1, total),
    ]


class Sender:
    def __init__(self, data, session):
        self.frames = frames(data, session)
        self.index = 0
        self.attempts = 0
        self.due = 0
        self.sent = 0
        self.retransmissions = 0
        self.ignored = 0
        self.done = False
        self.error = None

    def poll(self, now):
        require(type(now) is int and now >= 0, "invalid monotonic time")
        if self.done or self.error:
            return None
        if now >= DEADLINE:
            self.error = "transfer deadline; receiver may already have delivered"
            return None
        if now < self.due:
            return None
        if self.attempts >= ATTEMPTS:
            self.error = "retry limit; receiver may already have delivered"
            return None
        self.attempts += 1
        self.sent += 1
        if self.attempts > 1:
            self.retransmissions += 1
        self.due = now + min(RTO * 2 ** (self.attempts - 1), MAX_RTO)
        return encode(self.frames[self.index])

    def receive(self, raw, now):
        require(type(now) is int and now >= 0, "invalid monotonic time")
        if self.done or self.error:
            return False
        if now >= DEADLINE:
            self.error = "transfer deadline; receiver may already have delivered"
            return False
        if not self.attempts:
            self.ignored += 1
            return False
        try:
            p = decode(raw)
        except ProtocolError:
            self.ignored += 1
            return False
        current = self.frames[self.index]
        if p.kind != ACK or (p.session, p.seq, p.total) != (
            current.session,
            current.seq,
            current.total,
        ):
            self.ignored += 1
            return False
        self.index += 1
        self.attempts = 0
        self.due = now
        self.done = self.index == len(self.frames)
        return True


class Receiver:
    def __init__(self):
        self.start = None
        self.expected = 1
        self.chunks = []
        self.complete = False
        self.delivered = None
        self.duplicates = 0
        self.ignored = 0
        self.accepted = 0
        self.completed_at = None

    def receive(self, raw, now):
        require(type(now) is int and now >= 0, "invalid monotonic time")
        if self.complete and now - self.completed_at >= DALLY:
            self.ignored += 1
            return None
        try:
            p = decode(raw)
        except ProtocolError:
            self.ignored += 1
            return None
        if p.kind == ACK:
            self.ignored += 1
            return None
        if self.start is None:
            if p.kind != START:
                self.ignored += 1
                return None
            self.start = p
            self.accepted += 1
            return encode(Packet(ACK, p.session, 0, p.total))
        if (p.session, p.total) != (self.start.session, self.start.total):
            self.ignored += 1
            return None
        if p.kind == START:
            if p != self.start:
                self.ignored += 1
                return None
            self.duplicates += 1
            return encode(Packet(ACK, p.session, 0, p.total))
        if p.kind == DATA:
            size = struct.unpack("!I", self.start.payload[:4])[0]
            length = min(CHUNK, size - (p.seq - 1) * CHUNK)
            if len(p.payload) != length:
                self.ignored += 1
                return None
            if p.seq < self.expected:
                if self.chunks[p.seq - 1] != p.payload:
                    self.ignored += 1
                    return None
                self.duplicates += 1
                return encode(Packet(ACK, p.session, p.seq, p.total))
            if p.seq != self.expected or self.complete:
                self.ignored += 1
                return None
            self.chunks.append(p.payload)
            self.expected += 1
            self.accepted += 1
            return encode(Packet(ACK, p.session, p.seq, p.total))
        if p.kind == FIN:
            if self.expected != p.total + 1:
                self.ignored += 1
                return None
            if not self.complete:
                message = b"".join(self.chunks)
                if hashlib.sha256(message).digest() != self.start.payload[4:]:
                    self.ignored += 1
                    return None
                self.delivered = message
                self.complete = True
                self.completed_at = now
                self.accepted += 1
            else:
                self.duplicates += 1
            return encode(Packet(ACK, p.session, p.seq, p.total))
        return None

    def snapshot(self):
        return {
            "expected": self.expected,
            "complete": self.complete,
            "bytes": sum(map(len, self.chunks)),
            "accepted": self.accepted,
            "duplicates": self.duplicates,
            "ignored": self.ignored,
        }
