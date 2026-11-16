import hashlib
import random
import struct
import unittest
import zlib
from ferrywire.protocol import *
from ferrywire.simulation import simulate, SCENARIOS


class ProtocolTests(unittest.TestCase):
    def test_exact_wire_vector_independent_crc(self):
        packet = Packet(DATA, bytes(range(16)), 1, 1, b"abc")
        prefix = b"FRY1\x02" + bytes(range(16)) + b"\x00\x01\x00\x01\x00\x03abc"
        raw = encode(packet)
        self.assertEqual(raw, prefix + struct.pack("!I", zlib.crc32(prefix)))
        self.assertEqual(decode(raw), packet)
        self.assertEqual(len(raw), 34)

    def test_empty_and_exact_chunk_boundaries(self):
        for size in [0, 1, 511, 512, 513, 1024, 16383, 16384]:
            data = bytes(i % 256 for i in range(size))
            result = simulate(data)
            self.assertTrue(
                result["confirmed"] and result["delivered"] and result["exact"]
            )
            self.assertEqual(result["receiver"]["bytes"], size)
            self.assertEqual(result["receiver"]["accepted"], (size + 511) // 512 + 2)
        with self.assertRaises(ProtocolError):
            frames(b"x" * 16385, b"0" * 16)

    def test_all_scenarios_and_final_ack_uncertainty(self):
        for scenario in sorted(SCENARIOS):
            r = simulate(b"a" * 1500, scenario)
            self.assertEqual(
                r["confirmed"],
                scenario not in {"blackhole", "final-blackhole"},
                scenario,
            )
            self.assertEqual(r["delivered"], scenario != "blackhole", scenario)
            self.assertEqual(r["exact"], scenario != "blackhole", scenario)
            if scenario not in {"clean", "duplicate"}:
                self.assertGreater(r["retransmissions"], 0, scenario)
            self.assertLess(r["elapsed"], 30000)
        r = simulate(b"", "final-blackhole")
        self.assertFalse(r["confirmed"])
        self.assertTrue(r["delivered"])
        self.assertIn("may already", r["error"])

    def test_sender_duplicate_stale_future_and_wrong_session_ack_do_not_send(self):
        s = Sender(b"a" * 1024, b"1" * 16)
        s.poll(0)
        for p in [
            Packet(ACK, b"2" * 16, 0, 2),
            Packet(ACK, b"1" * 16, 1, 2),
            Packet(ACK, b"1" * 16, 0, 1),
            Packet(FIN, b"1" * 16, 3, 2),
        ]:
            self.assertFalse(s.receive(encode(p), 10))
            self.assertIsNone(s.poll(10))
            self.assertEqual(s.index, 0)
        self.assertTrue(s.receive(encode(Packet(ACK, b"1" * 16, 0, 2)), 20))
        s.poll(20)
        self.assertFalse(s.receive(encode(Packet(ACK, b"1" * 16, 0, 2)), 30))
        self.assertIsNone(s.poll(30))
        self.assertEqual(s.retransmissions, 0)

    def test_receiver_ignores_out_of_order_conflicting_duplicate_wrong_session(self):
        f = frames(b"a" * 512 + b"b", b"1" * 16)
        r = Receiver()
        self.assertIsNone(r.receive(encode(f[1]), 0))
        r.receive(encode(f[0]), 10)
        self.assertIsNone(r.receive(encode(f[2]), 20))
        self.assertEqual(r.expected, 1)
        r.receive(encode(f[1]), 30)
        self.assertIsNone(
            r.receive(encode(Packet(DATA, b"1" * 16, 1, 2, b"z" * 512)), 40)
        )
        self.assertIsNone(r.receive(encode(Packet(DATA, b"2" * 16, 2, 2, b"b")), 40))
        self.assertIsNone(r.receive(encode(Packet(DATA, b"1" * 16, 2, 2, b"bb")), 40))
        r.receive(encode(f[2]), 50)
        r.receive(encode(f[3]), 60)
        self.assertEqual(r.delivered, b"a" * 512 + b"b")
        r.receive(encode(f[3]), 70)
        r.receive(encode(f[1]), 80)
        self.assertEqual(r.accepted, 4)
        self.assertEqual(r.duplicates, 2)
        self.assertEqual(r.delivered, b"a" * 512 + b"b")

    def test_fin_needs_all_bytes_and_digest(self):
        f = frames(b"abc", b"1" * 16)
        r = Receiver()
        r.receive(encode(f[0]), 0)
        self.assertIsNone(r.receive(encode(f[-1]), 10))
        r.receive(encode(Packet(DATA, b"1" * 16, 1, 1, b"bad")), 20)
        self.assertIsNone(r.receive(encode(f[-1]), 30))
        self.assertFalse(r.complete)
        self.assertIsNone(r.delivered)

    def test_malformed_packets_and_crc_fail_closed(self):
        valid = encode(frames(b"hi", b"1" * 16)[0])
        for raw in [
            b"",
            valid[:-1],
            valid + b"x",
            valid[:4] + b"\xff" + valid[5:],
            b"x" * 550,
        ]:
            with self.assertRaises(ProtocolError):
                decode(raw)
            self.assertIsNone(Receiver().receive(raw, 0))
        for i in range(len(valid)):
            bad = bytearray(valid)
            bad[i] ^= 1
            with self.assertRaises(ProtocolError):
                decode(bytes(bad))

    def test_ack_cannot_carry_content_and_counters_no_bool(self):
        for p in [
            Packet(ACK, b"1" * 16, 0, 0, b"x"),
            Packet(FIN, b"1" * 16, 0, 0),
            Packet(DATA, b"1" * 16, 1, 0, b"a"),
            Packet(START, b"1" * 16, 0, 0, b"x" * 36),
            Packet(True, b"1" * 16, 0, 0),
            Packet(ACK, b"1" * 16, True, 0),
        ]:
            with self.assertRaises(ProtocolError):
                encode(p)

    def test_timeout_backoff_and_retry_bound(self):
        s = Sender(b"", b"1" * 16)
        emitted = []
        for t in range(0, 6000, 10):
            if s.poll(t):
                emitted.append(t)
        self.assertEqual(emitted, [0, 250, 750, 1750, 2750, 3750])
        self.assertEqual(s.sent, 6)
        self.assertEqual(s.retransmissions, 5)
        self.assertIsNotNone(s.error)
        self.assertIsNone(s.poll(6000))

    def test_absolute_deadline_not_extended_by_packets(self):
        s = Sender(b"a", b"1" * 16)
        s.poll(0)
        self.assertIsNone(s.poll(30000))
        self.assertIsNotNone(s.error)
        self.assertFalse(s.receive(encode(Packet(ACK, b"1" * 16, 0, 1)), 30001))

    def test_random_binary_framing_roundtrips_and_parser_fuzz(self):
        rng = random.Random(81)
        for _ in range(2000):
            n = rng.randrange(513)
            kind = DATA if n else ACK
            p = Packet(kind, rng.randbytes(16), 1, 1, rng.randbytes(n))
            self.assertEqual(decode(encode(p)), p)
            raw = rng.randbytes(rng.randrange(600))
            try:
                decode(raw)
            except ProtocolError:
                pass
            else:
                self.fail("random input accepted")

    def test_seeded_duplicate_reorder_corrupt_oracle(self):
        rng = random.Random(29)
        for i in range(250):
            data = rng.randbytes(rng.randrange(1600))
            f = frames(data, rng.randbytes(16))
            r = Receiver()
            for p in f:
                # The receiver must reconstruct exactly the original byte stream
                # even when valid old/future frames and CRC damage arrive first.
                for _ in range(rng.randrange(5)):
                    other = rng.choice(f)
                    if other.kind == DATA and other.seq > r.expected:
                        r.receive(encode(other), 0)
                    if other.kind == START and r.start:
                        r.receive(encode(other), 0)
                bad = bytearray(encode(p))
                bad[-1] ^= 1
                r.receive(bytes(bad), 0)
                ack = r.receive(encode(p), 0)
                self.assertIsNotNone(ack)
                if rng.randrange(2):
                    r.receive(encode(p), 0)
            self.assertEqual(r.delivered, data)
            self.assertEqual(r.accepted, len(f))

    def test_unsent_and_late_ack_and_fixed_receiver_dally(self):
        s = Sender(b"", b"1" * 16)
        ack = encode(Packet(ACK, b"1" * 16, 0, 0))
        self.assertFalse(s.receive(ack, 0))
        self.assertEqual(s.index, 0)
        s.poll(0)
        self.assertFalse(s.receive(ack, 30000))
        self.assertIsNotNone(s.error)
        r = Receiver()
        f = frames(b"", b"1" * 16)
        r.receive(encode(f[0]), 0)
        r.receive(encode(f[-1]), 100)
        self.assertIsNotNone(r.receive(encode(f[-1]), 2099))
        self.assertIsNone(r.receive(encode(f[-1]), 2100))
        self.assertEqual(r.completed_at, 100)
        self.assertEqual(r.delivered, b"")
