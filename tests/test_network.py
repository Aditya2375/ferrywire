import base64
import json
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
import unittest
from ferrywire.network import send, receive, port
from ferrywire.protocol import *

ROOT = str(Path(__file__).resolve().parents[1])


class NetworkTests(unittest.TestCase):
    def test_real_loopback_binary_empty_and_max(self):
        for data in [b"", bytes(range(256)) * 64]:
            ready = queue.Queue()
            result = queue.Queue()

            def receiver():
                try:
                    result.put(receive(0, ready.put, 5000))
                except BaseException as e:
                    result.put(e)

            thread = threading.Thread(target=receiver)
            thread.start()
            p = ready.get(timeout=2)
            sent = send(data, p)
            thread.join(7)
            self.assertFalse(thread.is_alive())
            received = result.get_nowait()
            if isinstance(received, BaseException):
                raise received
            self.assertEqual(received[0], data)
            self.assertTrue(sent["confirmed"])
            self.assertEqual(sent["retransmissions"], 0)

    def test_real_udp_loss_proxy_data_ack_corrupt_and_final_ack(self):
        ready = queue.Queue()
        result = queue.Queue()
        stop = threading.Event()

        def receiver():
            try:
                result.put(receive(0, ready.put, 10000))
            except BaseException as e:
                result.put(e)

        thread = threading.Thread(target=receiver)
        thread.start()
        target = ready.get(timeout=2)
        proxy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        proxy.bind(("127.0.0.1", 0))
        proxy.settimeout(0.05)
        seen = set()
        sender_peer = None

        def relay():
            nonlocal sender_peer
            while not stop.is_set():
                try:
                    raw, source = proxy.recvfrom(1024)
                except socket.timeout:
                    continue
                p = decode(raw)
                if source[1] == target:
                    direction = "ack"
                    destination = sender_peer
                    key = (direction, p.seq)
                    if p.seq in {1, p.total + 1} and key not in seen:
                        seen.add(key)
                        continue
                else:
                    sender_peer = source
                    destination = ("127.0.0.1", target)
                    direction = "data"
                    key = (direction, p.seq)
                    if p.kind == DATA and p.seq == 1 and key not in seen:
                        seen.add(key)
                        continue
                    if p.kind == DATA and p.seq == 2 and key not in seen:
                        seen.add(key)
                        raw = raw[:-1] + bytes([raw[-1] ^ 1])
                if destination:
                    proxy.sendto(raw, destination)

        relay_thread = threading.Thread(target=relay)
        relay_thread.start()
        try:
            data = bytes(range(256)) * 5
            sent = send(data, proxy.getsockname()[1])
            thread.join(12)
            self.assertFalse(thread.is_alive())
            received = result.get_nowait()
            if isinstance(received, BaseException):
                raise received
            self.assertEqual(received[0], data)
            self.assertGreaterEqual(sent["retransmissions"], 4)
            self.assertEqual(received[1]["accepted"], 5)
        finally:
            stop.set()
            relay_thread.join(2)
            proxy.close()
            thread.join(12)

    def test_receiver_wrong_peer_and_malformed_no_delivery(self):
        ready = queue.Queue()
        result = queue.Queue()

        def receiver():
            try:
                result.put(receive(0, ready.put, 4000))
            except BaseException as e:
                result.put(e)

        thread = threading.Thread(target=receiver)
        thread.start()
        p = ready.get(timeout=2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as good, socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM
        ) as bad:
            good.bind(("127.0.0.1", 0))
            bad.bind(("127.0.0.1", 0))
            good.settimeout(1)
            data = b"valid"
            f = frames(data, b"1" * 16)
            good.sendto(encode(f[0]), ("127.0.0.1", p))
            good.recvfrom(1024)
            for packet in f[1:]:
                bad.sendto(encode(packet), ("127.0.0.1", p))
            bad.sendto(b"x" * 600, ("127.0.0.1", p))
            for packet in f[1:]:
                good.sendto(encode(packet), ("127.0.0.1", p))
                good.recvfrom(1024)
        thread.join(6)
        received = result.get_nowait()
        if isinstance(received, BaseException):
            raise received
        self.assertEqual(received[0], data)
        self.assertGreaterEqual(received[1]["ignored"], 2)

    def test_receiver_timeout_and_ports(self):
        start = time.monotonic()
        with self.assertRaisesRegex(ProtocolError, "timed out"):
            receive(0, timeout_ms=100)
        self.assertLess(time.monotonic() - start, 1)
        for p in [-1, 65536, True, "42"]:
            with self.assertRaises(ProtocolError):
                port(p)
        with self.assertRaises(ProtocolError):
            port(0)
        self.assertEqual(port(0, True), 0)

    def test_real_cli_ready_and_binary_base64(self):
        receiver = subprocess.Popen(
            [sys.executable, "-m", "ferrywire", "receive", "--timeout-ms", "5000"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = json.loads(receiver.stdout.readline())
            self.assertTrue(ready["ready"])
            data = b"\x00\xff\x1b[31mhello"
            sent = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ferrywire",
                    "send",
                    "--port",
                    str(ready["port"]),
                ],
                cwd=ROOT,
                input=data,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(sent.returncode, 0, sent.stderr)
            self.assertTrue(json.loads(sent.stdout)["confirmed"])
            stdout, stderr = receiver.communicate(timeout=7)
            self.assertEqual(receiver.returncode, 0, stderr)
            self.assertEqual(base64.b64decode(json.loads(stdout)["base64"]), data)
            self.assertNotIn("\x1b", stdout)
        finally:
            if receiver.poll() is None:
                receiver.kill()
                receiver.wait()

    def test_cli_oversize_and_parser_controls(self):
        p = subprocess.run(
            [sys.executable, "-m", "ferrywire", "send", "--port", "1234"],
            cwd=ROOT,
            input=b"x" * 16385,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(p.returncode, 2)
        self.assertIn("16384", json.loads(p.stderr)["error"])
        p = subprocess.run(
            [sys.executable, "-m", "ferrywire", "\x1b[31m"],
            cwd=ROOT,
            capture_output=True,
            timeout=5,
        )
        self.assertEqual(p.returncode, 2)
        self.assertNotIn(b"\x1b", p.stderr)

    def test_real_sender_blackhole_has_bounded_six_attempts(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sink:
            sink.bind(("127.0.0.1", 0))
            started = time.monotonic()
            with self.assertRaisesRegex(ProtocolError, "receiver may already"):
                send(b"hello", sink.getsockname()[1])
            self.assertLess(time.monotonic() - started, 6)
            sink.settimeout(0.05)
            packets = []
            while True:
                try:
                    packets.append(sink.recvfrom(1024)[0])
                except socket.timeout:
                    break
            self.assertEqual(len(packets), 6)
            self.assertEqual(len(set(packets)), 1)
            self.assertEqual(decode(packets[0]).kind, START)

    def test_receiver_datagram_budget_malformed_flood_is_bounded(self):
        from unittest.mock import patch

        ready, result = queue.Queue(), queue.Queue()

        def receiver():
            try:
                result.put(receive(0, ready.put, 2000))
            except BaseException as exc:
                result.put(exc)

        with patch("ferrywire.network.MAX_DATAGRAMS", 8):
            thread = threading.Thread(target=receiver)
            thread.start()
            target = ready.get(timeout=2)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for _ in range(12):
                    sock.sendto(b"bad", ("127.0.0.1", target))
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertRegex(str(result.get_nowait()), "datagram budget")

    def test_sender_wrong_source_ack_cannot_advance(self):
        result = queue.Queue()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as target, socket.socket(
            socket.AF_INET, socket.SOCK_DGRAM
        ) as impostor:
            target.bind(("127.0.0.1", 0))
            target.settimeout(2)
            impostor.bind(("127.0.0.1", 0))

            def sender():
                try:
                    result.put(send(b"hello", target.getsockname()[1]))
                except BaseException as exc:
                    result.put(exc)

            thread = threading.Thread(target=sender)
            thread.start()
            raw, source = target.recvfrom(1024)
            start = decode(raw)
            forged = encode(Packet(ACK, start.session, 0, start.total))
            impostor.sendto(forged, source)
            # Correct response deliberately delayed beyond the first timer.
            again, source2 = target.recvfrom(1024)
            self.assertEqual(raw, again)
            self.assertEqual(source, source2)
            target.sendto(forged, source)
            while True:
                raw, source = target.recvfrom(1024)
                p = decode(raw)
                target.sendto(encode(Packet(ACK, p.session, p.seq, p.total)), source)
                if p.kind == FIN:
                    break
            thread.join(3)
            self.assertFalse(thread.is_alive())
            answer = result.get_nowait()
            if isinstance(answer, BaseException):
                raise answer
            self.assertTrue(answer["confirmed"])
            self.assertEqual(answer["retransmissions"], 1)
            self.assertGreaterEqual(answer["ignored"], 1)
