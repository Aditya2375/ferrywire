# Ferrywire

Across the gap. An original, bounded reliable-datagram protocol over real IPv4 loopback UDP, with a separate browser packet lab. Python standard-library endpoints implement framing, checksums, session IDs, stop-and-wait acknowledgments, retransmission, duplicate suppression and verified in-memory delivery. No TCP, TFTP, QUIC or other transport implementation is wrapped or invoked.

[Open the packet lab](https://aditya2375.github.io/ferrywire/) · [Source](https://github.com/Aditya2375/ferrywire)

**This is a trusted-local-machine protocol lab, not an Internet transport.** Native endpoints bind and send only to the literal IPv4 address `127.0.0.1`. There is no remote-host option, DNS lookup, public listener, encryption, authentication or Internet congestion-control claim. Another local process can interfere, pin the receiver, forge packets or cause denial of service. CRC-32 and the announced SHA-256 digest detect accidental corruption, not an attacker. Use a standard transport for real networks or sensitive messages.

The browser lab is an independent JavaScript implementation of the wire format and state machines. It simulates virtual datagrams; it does **not** send UDP, open sockets, use the Python adapter or contact another device. Its invented sample text and traces stay in this tab with no storage or upload. Closing/resetting loses them. Downloading a trace creates a new JSON download including the message text, not a network transfer or confirmation of a saved file. Do not enter secrets.

## Run real UDP endpoints

Requirements: Linux and Python 3.10+. No runtime dependencies, credentials, elevated privilege or server account. Linux native and Chromium browser are tested; other operating systems/browsers, mobile keyboards, IME and assistive-technology combinations are untested.

From the source checkout, start the receiver:

```sh
python3 -m ferrywire receive --port 43127
```

It first prints a ready JSON line **after successful bind**. In another terminal, from the source checkout:

```sh
printf 'hello\n' | python3 -m ferrywire send --port 43127
```

A port might be occupied. Use `receive --port 0` (also the default) for an ephemeral port and pass the port from ready JSON to the sender. There is no `SO_REUSEADDR` sharing. The receiver accepts **one** transfer and exits after its final-ACK recovery window or absolute timeout. It is not a long-running service. Send only to the actual receiver port; no port scanning or automatic discovery.

`send` reads stdin until EOF or 16,385 bytes. Pipe a finite input or finish interactive input with EOF. **Waiting for stdin is not covered by the protocol's 30 s deadline**; that clock begins after input is read. More than 16,384 bytes is refused before socket creation. Arbitrary bytes, including zero bytes and binary data, are supported. No filename, path or directory is sent over the network and no files are written by either endpoint.

The sender returns JSON with `confirmed`, byte count, sent-frame/retransmission/ignored-datagram counts and the meaning of confirmation. The receiver prints a second JSON line with the verified bytes as `base64`, receiver counters and the meaning of delivery. Normal output and errors are ASCII-escaped JSON, not raw terminal-control data. Exit 0 means the local operation completed; exit 2 means refusal/failure. `--help` is plain text. OS signals retain their normal exit behavior.

```sh
python3 -m ferrywire receive --timeout-ms 5000
python3 -m ferrywire send --port 43127 < example.bin
python3 -m ferrywire --help
```

The second command requires an independently running receiver at that port and an existing input file; it only reads that file through stdin. A short receiver timeout can expire before the sender finishes retrying. Default receiver timeout is 35,000 ms; accepted values are 100-35,000 ms. Invalid port numbers, occupied ports, malformed arguments, oversize input and socket errors return explicit errors. The adapter never retries an entire completed transfer automatically.

## What confirmation actually means

1. Receiver accepts the START metadata and pins its first valid sender IP/port and 16-byte session ID.
2. Each DATA chunk is accepted only at the next sequence number. An exact duplicate receives another ACK without appending bytes again. Out-of-order, conflicting duplicate, wrong-session and malformed frames are ignored.
3. FIN is accepted only after all announced bytes arrive and their SHA-256 digest matches. The receiver exposes the message **once in memory**, then sends the final ACK.
4. The sender confirms only after a matching final ACK returns. This is not a disk-persistence, authentication or exactly-once-across-restarts guarantee.

**Failure does not prove non-delivery.** If every final ACK is lost, the receiver may already have verified the full message while the sender exhausts its retry budget. Both the CLI error and browser split-state example preserve this uncertainty. Do not turn a sender timeout into an automatic application-level retry of a side effect. There are no side effects beyond memory and loopback sockets here.

After delivery, the receiver answers duplicates for a **fixed 2 s dally**, measured from first completion, never extended by duplicates. Its absolute receiver timeout can shorten that window. After it closes, old packets are not handled. A fresh native transfer uses a new random 128-bit session ID and socket; no persistent replay cache or restart recovery is implemented. The session ID reduces accidental cross-transfer confusion; it does not establish identity or prevent a local attacker from observing/forging it.

## Wire format

Every integer is unsigned big-endian. An entire frame is one UDP datagram.

| Field          |  Bytes | Meaning                                              |
| -------------- | -----: | ---------------------------------------------------- |
| Magic/version  |      4 | ASCII `FRY1`                                         |
| Kind           |      1 | START=1, DATA=2, FIN=3, ACK=4                        |
| Session        |     16 | Random per native sender invocation                  |
| Sequence       |      2 | START=0, DATA=1..N, FIN=N+1; ACK echoes the sequence |
| Total chunks   |      2 | N=ceil(message bytes/512), 0..32                     |
| Payload length |      2 | Exact payload byte count                             |
| Payload        | 0..512 | By frame kind below                                  |
| CRC-32         |      4 | CRC of all preceding header and payload bytes        |

Header is 27 bytes; fixed overhead is 31 bytes; maximum datagram is 543 bytes. START contains 4-byte total message size and 32-byte SHA-256 of the complete message. DATA contains exactly 512 bytes except a possibly shorter last chunk. FIN and ACK contain no payload. Empty messages use START and FIN with N=0 and no DATA frames. ACKs carry no filenames, content or status code.

Strict decoding checks datagram limits, exact declared length, magic/version, CRC, known kind, sequence/count bounds and per-kind payload shape before effects. The receiver additionally checks each DATA length against the announced whole-message size and checks full-message SHA-256 before delivery. Native reads up to max-frame+1 bytes, so oversized datagrams are rejected even when the socket truncates them. Wrong peer IP/port is ignored by the adapter. No parser invokes code, expands paths, decompresses content or executes commands.

A cooperating sender sends one outstanding frame at a time. Only its exact expected ACK can advance it, and only after it actually sent that frame. Duplicate/stale/future/wrong-session ACKs are ignored and **never trigger retransmission**. The timer is the only retransmission trigger, avoiding duplicate-ACK feedback loops.

## Bounds and timing

- 16,384-byte native message, 512-byte chunks, at most 32 DATA frames.
- One receiver session, one outstanding sender frame, no unbounded reorder queue.
- At most 6 attempts per frame: initial timeout 250 ms, then 500 ms, then capped at 1,000 ms. Every newly acknowledged frame resets its own attempt count.
- Absolute 30,000 ms monotonic sender deadline. It can stop a long lossy transfer before per-frame retries are exhausted.
- Default absolute receiver timeout 35,000 ms, with a fixed 2,000 ms post-delivery dally, subject to that outer timeout.
- At most 4,096 incoming datagrams per endpoint per invocation; excess traffic aborts, including malformed/foreign traffic. This bounds handling work, not operating-system queues or protection from a hostile local process.
- No general network congestion control, RTT estimator, sliding window, multiplexing, negotiation, resume, reconnect, persistent state, file writes or off-loopback communication.

The short initial RTO is deliberately for this loopback lab, **not** the Internet RTO recommended by RFC 8085. Exponential backoff and one outstanding frame are not a claim of Internet congestion-control compliance. Delivery is not guaranteed under arbitrary loss, delay, interference, process termination or resource exhaustion.

## Browser experiment

The browser UI limits editable UTF-8 text to 4,096 bytes for readable traces; its core model shares the native 16 KiB bound. Content is not converted to ASCII and may contain Unicode or BOM characters; the page is not a secure bidi/grapheme renderer. Unpaired Unicode surrogates and oversize input are refused. Changes disable stale trace downloads and mark prior results as belonging to the previous run. Reset requires confirmation.

The deterministic model uses 10 ms virtual ticks and 50 ms one-way transit. It is not a timing benchmark. Browser simulation session bytes are fixed `00..0f` for exact trace parity, unlike native random sessions. Injected faults:

| Scenario            | Injection                                                            |
| ------------------- | -------------------------------------------------------------------- |
| Clear               | No faults                                                            |
| DATA loss           | Drop the first DATA 1 transmission only                              |
| ACK loss            | Drop the first ACK 1 only                                            |
| Corruption          | Flip a CRC byte in the first DATA 1 transmission                     |
| Duplicate           | Deliver a second DATA 1 after 180 ms                                 |
| Reorder             | Delay original DATA 1 for 450 ms, allowing its retry to arrive first |
| Final ACK loss      | Drop the first FIN ACK, then recover during dally                    |
| Blackhole           | Drop every frame                                                     |
| Final ACK blackhole | Drop every FIN ACK, showing delivered but unconfirmed                |

For an empty message, DATA-specific fault scenarios have no DATA frame to affect. Traces stop when the sender reaches confirmation/failure, not after every delayed datagram in the model queue. The page shows both receiver delivery and sender confirmation separately. The JSON download contains input text/scenario and the actual result/event trace; it has no native protocol effect and no upload.

## Verify

```sh
make test
npm ci --ignore-scripts
npm test
npx playwright install chromium
npm run test:browser
```

22 native test groups include actual loopback empty/max-size binary transfers, a real UDP proxy dropping DATA/ACK/final ACK and corrupting DATA, peer pinning and a wrong-source forged ACK, bounded blackhole retries and malformed-datagram budget, real CLI ready/base64 behavior, exact wire/CRC vector, size boundaries, 2,000 randomized framing/parser cases, 250 independently checked receiver fault traces, duplicate/stale ACK suppression, out-of-order/conflicting data, final digest refusal, exact backoff, unsent/late ACK refusal and fixed dally boundaries. They do not establish correctness under every possible schedule.

The independent JS implementation has 2,181 assertions: 172 Python-vs-JS exact encoded-wire and full-event/result comparisons across all nine scenarios and binary sizes, 1,000 randomized framing/corruption cases, known CRC-32, Unicode/BOM and state boundaries. Shared parity is not a substitute for actual UDP tests.

Chromium has 228 checks at 1440/1024/768/390/320 widths: all fault scenarios, real trace-download bytes, exact text delivery, delivered/unconfirmed split, empty/oversize/Unicode/markup, stale trace disabling, reset confirmation, keyboard skip, 44px controls and no page overflow. Tests assert zero browser errors, failed requests and third-party runtime requests. Browser CSP forbids network connections, inline scripts and remote assets; DOM content uses text rather than HTML injection. Native wire and browser lab are deliberately separate capabilities.

CI uses Python 3.10/3.12/3.13, Node 22 and Ubuntu 24.04, SHA-pinned official actions, full-history Gitleaks and local Semgrep rules. Playwright 1.56.1 is an exact development-only dependency. Python and browser runtime have no third-party packages. MIT OR Apache-2.0.

## References

Original implementation, not copied protocol code. It is not TFTP-compatible or a full implementation of any cited RFC.

- [RFC 8085: UDP usage guidelines](https://www.rfc-editor.org/rfc/rfc8085.html): reliability, duplicate handling and the need for congestion control outside a controlled local lab.
- [RFC 1350: TFTP](https://www.rfc-editor.org/rfc/rfc1350.html): stop-and-wait framing and final-ACK dally/uncertainty background.
- [RFC 1123 section 4.2.3.1](https://www.freesoft.org/CIE/RFC/1123/77.htm): duplicate ACKs must not trigger fresh DATA retransmission.
- [Loyola networking textbook: UDP](https://intronetworks.cs.luc.edu/current2/ComputerNetworks/udp.html): delayed duplicates and transfer incarnations.
- [Python socket](https://docs.python.org/3/library/socket.html) and [zlib](https://docs.python.org/3/library/zlib.html): datagrams/timeouts and non-cryptographic CRC-32.
