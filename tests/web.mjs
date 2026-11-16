import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  encode,
  decode,
  frames,
  simulate,
  SCENARIOS,
  crc32,
  text,
  toText,
  Receiver,
  Sender,
} from "../docs/model.js";
let checks = 0;
const eq = (a, b) => {
  assert.deepEqual(a, b);
  checks++;
};
eq(crc32(text("123456789")), 0xcbf43926);
eq(toText(text("\ufeffé😀")), "\ufeffé😀");
assert.throws(() => text("\ud800"));
checks++;
const session = Uint8Array.from({ length: 16 }, (_, i) => i);
let seed = 284;
const rng = () => {
  seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0;
  return seed >>> 8;
};
const cases = [];
for (const size of [0, 1, 511, 512, 513, 1024, 16383, 16384])
  for (const scenario of SCENARIOS) {
    const data = Uint8Array.from({ length: size }, () => rng() % 256);
    cases.push({ data: Buffer.from(data).toString("base64"), scenario });
  }
for (let i = 0; i < 100; i++) {
  const data = Uint8Array.from({ length: rng() % 2048 }, () => rng() % 256);
  cases.push({
    data: Buffer.from(data).toString("base64"),
    scenario: SCENARIOS[rng() % SCENARIOS.length],
  });
}
const py = spawnSync("python3", ["-m", "tests.parity"], {
  input: JSON.stringify(cases),
  encoding: "utf8",
  maxBuffer: 50 * 1024 * 1024,
});
assert.equal(py.status, 0, py.stderr);
const expected = JSON.parse(py.stdout);
for (let i = 0; i < cases.length; i++) {
  const data = Uint8Array.from(Buffer.from(cases[i].data, "base64"));
  eq(
    {
      frames: (await frames(data, session)).map((p) =>
        Buffer.from(encode(p)).toString("hex"),
      ),
      result: await simulate(data, cases[i].scenario),
    },
    expected[i],
  );
}
for (let i = 0; i < 1000; i++) {
  const n = 1 + (rng() % 512),
    p = {
      kind: 2,
      session: Uint8Array.from({ length: 16 }, () => rng() % 256),
      seq: 1,
      total: 1,
      payload: Uint8Array.from({ length: n }, () => rng() % 256),
    };
  eq(decode(encode(p)), p);
  const raw = encode(p);
  raw[rng() % raw.length] ^= 1;
  assert.throws(() => decode(raw));
  checks++;
}
const f = await frames(text("hello"), session),
  r = new Receiver();
await r.receive(encode(f[0]), 0);
eq(await r.receive(encode(f.at(-1)), 10), null);
await r.receive(encode(f[1]), 20);
await r.receive(encode(f[1]), 30);
eq(r.expected, 2);
await r.receive(encode(f.at(-1)), 40);
eq(toText(r.delivered), "hello");
eq(await r.receive(encode(f.at(-1)), 2040), null);
const s = await Sender.create(text(""), session);
eq(
  s.receive(
    encode({ kind: 4, session, seq: 0, total: 0, payload: new Uint8Array() }),
    0,
  ),
  false,
);
eq(s.index, 0);
console.log(
  `${checks} assertions PASS; ${cases.length} exact wire+full trace Python/JS comparisons; 1000 random framing+corruption cases`,
);
