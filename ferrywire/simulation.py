"""Deterministic virtual network. It never creates a socket."""

from .protocol import *

SCENARIOS = {
    "clean",
    "data-loss",
    "ack-loss",
    "corruption",
    "duplicate",
    "reorder",
    "final-ack",
    "blackhole",
    "final-blackhole",
}


def simulate(data, scenario="clean"):
    require(scenario in SCENARIOS, "unknown scenario")
    sender = Sender(data, bytes(range(16)))
    receiver = Receiver()
    queue = []
    events = []
    used = False
    serial = 0

    def record(t, actor, action, packet):
        events.append(
            {
                "ms": t,
                "actor": actor,
                "action": action,
                "kind": {START: "START", DATA: "DATA", FIN: "FIN", ACK: "ACK"}[
                    packet.kind
                ],
                "seq": packet.seq,
            }
        )

    def transmit(raw, direction, now):
        nonlocal used, serial
        p = decode(raw)
        record(now, direction, "send", p)
        target = p.kind == DATA and p.seq == 1
        drop = scenario == "blackhole" or (
            scenario == "final-blackhole"
            and direction == "receiver"
            and p.seq == p.total + 1
        )
        if not used:
            drop = (
                drop
                or (scenario == "data-loss" and direction == "sender" and target)
                or (scenario == "ack-loss" and direction == "receiver" and p.seq == 1)
                or (
                    scenario == "final-ack"
                    and direction == "receiver"
                    and p.seq == p.total + 1
                )
            )
            if drop:
                used = True
        if drop:
            record(now, direction, "drop", p)
            return
        delay = 50
        if not used and direction == "sender" and target:
            if scenario == "corruption":
                raw = raw[:-1] + bytes([raw[-1] ^ 1])
                used = True
                record(now, direction, "corrupt", p)
            elif scenario == "reorder":
                delay = 450
                used = True
                record(now, direction, "delay", p)
            elif scenario == "duplicate":
                serial += 1
                queue.append((now + 180, serial, direction, raw))
                used = True
                record(now, direction, "duplicate", p)
        serial += 1
        queue.append((now + delay, serial, direction, raw))

    # Fixed virtual ticks and deterministic FIFO ties, independent of wall time.
    for now in range(0, DEADLINE + 1, 10):
        raw = sender.poll(now)
        if raw is not None:
            transmit(raw, "sender", now)
        due = sorted([e for e in queue if e[0] <= now])
        queue[:] = [e for e in queue if e[0] > now]
        for _, _, direction, raw in due:
            # Envelope-only label is from unmodified fields, even if CRC is bad.
            _, kind, sid, seq, total, size = HEADER.unpack(raw[: HEADER.size])
            p = Packet(kind, sid, seq, total, raw[HEADER.size : -4])
            record(now, direction, "arrive", p)
            if direction == "sender":
                ack = receiver.receive(raw, now)
                if ack is not None:
                    transmit(ack, "receiver", now)
                else:
                    record(now, "receiver", "ignore", p)
            else:
                if not sender.receive(raw, now):
                    record(now, "sender", "ignore", p)
        if sender.done or sender.error:
            return {
                "confirmed": sender.done,
                "delivered": receiver.complete,
                "exact": receiver.delivered == data,
                "elapsed": now,
                "sent": sender.sent,
                "retransmissions": sender.retransmissions,
                "ignored_acks": sender.ignored,
                "receiver": receiver.snapshot(),
                "error": sender.error,
                "events": events,
            }
    raise ProtocolError("simulation step budget exceeded")
