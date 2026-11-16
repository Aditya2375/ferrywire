// Original independent browser implementation. Never opens a network socket.
export const START = 1,
  DATA = 2,
  FIN = 3,
  ACK = 4,
  CHUNK = 512,
  MAX_BYTES = 16384,
  RTO = 250,
  MAX_RTO = 1000,
  ATTEMPTS = 6,
  DEADLINE = 30000,
  DALLY = 2000;
const enc = new TextEncoder();
export const toText = (b) =>
  new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(b);
export function check(ok, m) {
  if (!ok) throw new Error(m);
}
export function text(s) {
  check(
    typeof s === "string" &&
      !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(
        s,
      ),
    "invalid Unicode text",
  );
  const b = enc.encode(s);
  check(b.length <= 16384, "message exceeds 16384 bytes");
  return b;
}
const equal = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);
export async function sha256(b) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", b));
}
export function crc32(b) {
  let c = 0xffffffff;
  for (const x of b) {
    c ^= x;
    for (let i = 0; i < 8; i++) c = (c >>> 1) ^ (c & 1 ? 0xedb88320 : 0);
  }
  return (c ^ 0xffffffff) >>> 0;
}
export function validate(p) {
  check(
    [START, DATA, FIN, ACK].includes(p.kind) && Number.isInteger(p.kind),
    "unknown kind",
  );
  check(
    p.session instanceof Uint8Array && p.session.length === 16,
    "session must be16bytes",
  );
  check(
    Number.isInteger(p.seq) &&
      Number.isInteger(p.total) &&
      p.total >= 0 &&
      p.total <= 32,
    "invalid counters",
  );
  check(p.payload instanceof Uint8Array, "payload must be bytes");
  if (p.kind === START) {
    check(p.seq === 0 && p.payload.length === 36, "invalid START");
    const size = new DataView(
      p.payload.buffer,
      p.payload.byteOffset,
      36,
    ).getUint32(0);
    check(
      size <= 16384 && p.total === Math.ceil(size / 512),
      "invalid START size/count",
    );
  } else if (p.kind === DATA)
    check(
      p.seq >= 1 &&
        p.seq <= p.total &&
        p.payload.length >= 1 &&
        p.payload.length <= 512,
      "invalid DATA",
    );
  else if (p.kind === FIN)
    check(p.seq === p.total + 1 && p.payload.length === 0, "invalid FIN");
  else
    check(
      p.seq >= 0 && p.seq <= p.total + 1 && p.payload.length === 0,
      "invalid ACK",
    );
  return p;
}
const packet = (kind, session, seq, total, payload = new Uint8Array()) => ({
  kind,
  session,
  seq,
  total,
  payload,
});
export function encode(p) {
  validate(p);
  const raw = new Uint8Array(31 + p.payload.length),
    v = new DataView(raw.buffer);
  raw.set([70, 82, 89, 49]);
  v.setUint8(4, p.kind);
  raw.set(p.session, 5);
  v.setUint16(21, p.seq);
  v.setUint16(23, p.total);
  v.setUint16(25, p.payload.length);
  raw.set(p.payload, 27);
  v.setUint32(raw.length - 4, crc32(raw.slice(0, -4)));
  return raw;
}
export function decode(raw) {
  check(
    raw instanceof Uint8Array && raw.length >= 31 && raw.length <= 543,
    "invalid datagram length",
  );
  const v = new DataView(raw.buffer, raw.byteOffset, raw.byteLength);
  check(
    raw[0] === 70 && raw[1] === 82 && raw[2] === 89 && raw[3] === 49,
    "wrong magic/version",
  );
  check(raw.length === 31 + v.getUint16(25), "declared length mismatch");
  check(
    crc32(raw.slice(0, -4)) === v.getUint32(raw.length - 4),
    "CRC mismatch",
  );
  return validate(
    packet(
      v.getUint8(4),
      raw.slice(5, 21),
      v.getUint16(21),
      v.getUint16(23),
      raw.slice(27, -4),
    ),
  );
}
export async function frames(data, session) {
  check(
    data instanceof Uint8Array && data.length <= 16384,
    "message exceeds 16384 bytes",
  );
  const total = Math.ceil(data.length / 512),
    meta = new Uint8Array(36);
  new DataView(meta.buffer).setUint32(0, data.length);
  meta.set(await sha256(data), 4);
  const f = [packet(START, session, 0, total, meta)];
  for (let i = 0; i < total; i++)
    f.push(
      packet(DATA, session, i + 1, total, data.slice(i * 512, (i + 1) * 512)),
    );
  f.push(packet(FIN, session, total + 1, total));
  return f;
}
export class Sender {
  constructor(f) {
    this.frames = f;
    this.index = 0;
    this.attempts = 0;
    this.due = 0;
    this.sent = 0;
    this.retransmissions = 0;
    this.ignored = 0;
    this.done = false;
    this.error = null;
  }
  static async create(data, session) {
    return new Sender(await frames(data, session));
  }
  poll(now) {
    check(Number.isInteger(now) && now >= 0, "invalid monotonic time");
    if (this.done || this.error) return null;
    if (now >= 30000) {
      this.error = "transfer deadline; receiver may already have delivered";
      return null;
    }
    if (now < this.due) return null;
    if (this.attempts >= 6) {
      this.error = "retry limit; receiver may already have delivered";
      return null;
    }
    this.attempts++;
    this.sent++;
    if (this.attempts > 1) this.retransmissions++;
    this.due = now + Math.min(250 * 2 ** (this.attempts - 1), 1000);
    return encode(this.frames[this.index]);
  }
  receive(raw, now) {
    check(Number.isInteger(now) && now >= 0, "invalid monotonic time");
    if (this.done || this.error) return false;
    if (now >= 30000) {
      this.error = "transfer deadline; receiver may already have delivered";
      return false;
    }
    if (!this.attempts) {
      this.ignored++;
      return false;
    }
    let p;
    try {
      p = decode(raw);
    } catch {
      this.ignored++;
      return false;
    }
    const c = this.frames[this.index];
    if (
      p.kind !== ACK ||
      !equal(p.session, c.session) ||
      p.seq !== c.seq ||
      p.total !== c.total
    ) {
      this.ignored++;
      return false;
    }
    this.index++;
    this.attempts = 0;
    this.due = now;
    this.done = this.index === this.frames.length;
    return true;
  }
}
export class Receiver {
  constructor() {
    this.start = null;
    this.expected = 1;
    this.chunks = [];
    this.complete = false;
    this.delivered = null;
    this.duplicates = 0;
    this.ignored = 0;
    this.accepted = 0;
    this.completedAt = null;
  }
  async receive(raw, now) {
    check(Number.isInteger(now) && now >= 0, "invalid monotonic time");
    if (this.complete && now - this.completedAt >= 2000) {
      this.ignored++;
      return null;
    }
    let p;
    try {
      p = decode(raw);
    } catch {
      this.ignored++;
      return null;
    }
    if (p.kind === ACK) {
      this.ignored++;
      return null;
    }
    if (this.start === null) {
      if (p.kind !== START) {
        this.ignored++;
        return null;
      }
      this.start = p;
      this.accepted++;
      return encode(packet(ACK, p.session, 0, p.total));
    }
    if (!equal(p.session, this.start.session) || p.total !== this.start.total) {
      this.ignored++;
      return null;
    }
    if (p.kind === START) {
      if (!equal(p.payload, this.start.payload)) {
        this.ignored++;
        return null;
      }
      this.duplicates++;
      return encode(packet(ACK, p.session, 0, p.total));
    }
    if (p.kind === DATA) {
      const size = new DataView(
          this.start.payload.buffer,
          this.start.payload.byteOffset,
        ).getUint32(0),
        length = Math.min(512, size - (p.seq - 1) * 512);
      if (p.payload.length !== length) {
        this.ignored++;
        return null;
      }
      if (p.seq < this.expected) {
        if (!equal(this.chunks[p.seq - 1], p.payload)) {
          this.ignored++;
          return null;
        }
        this.duplicates++;
        return encode(packet(ACK, p.session, p.seq, p.total));
      }
      if (p.seq !== this.expected || this.complete) {
        this.ignored++;
        return null;
      }
      this.chunks.push(p.payload);
      this.expected++;
      this.accepted++;
      return encode(packet(ACK, p.session, p.seq, p.total));
    }
    if (p.kind === FIN) {
      if (this.expected !== p.total + 1) {
        this.ignored++;
        return null;
      }
      if (!this.complete) {
        const b = new Uint8Array(this.chunks.reduce((n, x) => n + x.length, 0));
        let offset = 0;
        for (const x of this.chunks) {
          b.set(x, offset);
          offset += x.length;
        }
        if (!equal(await sha256(b), this.start.payload.slice(4))) {
          this.ignored++;
          return null;
        }
        this.delivered = b;
        this.complete = true;
        this.completedAt = now;
        this.accepted++;
      } else this.duplicates++;
      return encode(packet(ACK, p.session, p.seq, p.total));
    }
    return null;
  }
  snapshot() {
    return {
      expected: this.expected,
      complete: this.complete,
      bytes: this.chunks.reduce((n, b) => n + b.length, 0),
      accepted: this.accepted,
      duplicates: this.duplicates,
      ignored: this.ignored,
    };
  }
}
export const SCENARIOS = [
  "clean",
  "data-loss",
  "ack-loss",
  "corruption",
  "duplicate",
  "reorder",
  "final-ack",
  "blackhole",
  "final-blackhole",
];
export async function simulate(data, scenario = "clean") {
  check(SCENARIOS.includes(scenario), "unknown scenario");
  const sender = await Sender.create(
      data,
      Uint8Array.from({ length: 16 }, (_, i) => i),
    ),
    receiver = new Receiver();
  let queue = [],
    used = false,
    serial = 0;
  const events = [];
  function record(ms, actor, action, p) {
    events.push({
      ms,
      actor,
      action,
      kind: { 1: "START", 2: "DATA", 3: "FIN", 4: "ACK" }[p.kind],
      seq: p.seq,
    });
  }
  function transmit(raw, direction, now) {
    const p = decode(raw);
    record(now, direction, "send", p);
    const target = p.kind === DATA && p.seq === 1;
    let drop =
      scenario === "blackhole" ||
      (scenario === "final-blackhole" &&
        direction === "receiver" &&
        p.seq === p.total + 1);
    if (!used) {
      drop =
        drop ||
        (scenario === "data-loss" && direction === "sender" && target) ||
        (scenario === "ack-loss" && direction === "receiver" && p.seq === 1) ||
        (scenario === "final-ack" &&
          direction === "receiver" &&
          p.seq === p.total + 1);
      if (drop) used = true;
    }
    if (drop) {
      record(now, direction, "drop", p);
      return;
    }
    let delay = 50;
    if (!used && direction === "sender" && target) {
      if (scenario === "corruption") {
        raw = raw.slice();
        raw[raw.length - 1] ^= 1;
        used = true;
        record(now, direction, "corrupt", p);
      } else if (scenario === "reorder") {
        delay = 450;
        used = true;
        record(now, direction, "delay", p);
      } else if (scenario === "duplicate") {
        queue.push([now + 180, ++serial, direction, raw]);
        used = true;
        record(now, direction, "duplicate", p);
      }
    }
    queue.push([now + delay, ++serial, direction, raw]);
  }
  for (let now = 0; now <= 30000; now += 10) {
    const raw = sender.poll(now);
    if (raw) transmit(raw, "sender", now);
    const due = queue
      .filter((e) => e[0] <= now)
      .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    queue = queue.filter((e) => e[0] > now);
    for (const [, , direction, raw] of due) {
      const v = new DataView(raw.buffer, raw.byteOffset, raw.byteLength),
        p = { kind: raw[4], seq: v.getUint16(21) };
      record(now, direction, "arrive", p);
      if (direction === "sender") {
        const ack = await receiver.receive(raw, now);
        if (ack) transmit(ack, "receiver", now);
        else record(now, "receiver", "ignore", p);
      } else if (!sender.receive(raw, now)) record(now, "sender", "ignore", p);
    }
    if (sender.done || sender.error)
      return {
        confirmed: sender.done,
        delivered: receiver.complete,
        exact: receiver.delivered !== null && equal(receiver.delivered, data),
        elapsed: now,
        sent: sender.sent,
        retransmissions: sender.retransmissions,
        ignored_acks: sender.ignored,
        receiver: receiver.snapshot(),
        error: sender.error,
        events,
      };
  }
  throw new Error("simulation step budget exceeded");
}
