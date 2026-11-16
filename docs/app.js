import { simulate, text } from "./model.js";
const $ = (id) => document.getElementById(id),
  sample = $("message").value;
const descriptions = {
  clean:
    "No injected fault. Every frame is acknowledged once before the sender advances.",
  "data-loss":
    "Drop the first DATA 1 transmission only. Its missing ACK makes the timer expire; a retry should deliver it.",
  "ack-loss":
    "Drop the first ACK 1 only. The receiver has the chunk; it must acknowledge the retry without appending bytes twice.",
  corruption:
    "Flip one CRC byte in the first DATA 1 transmission. The receiver must ignore it, then accept a clean retry.",
  duplicate:
    "Deliver another copy of DATA 1 after 180 ms. Old data and its stale ACK must not trigger a retransmission loop.",
  reorder:
    "Delay the original DATA 1 for 450 ms. Its retry can arrive first. The late original must not append data twice.",
  "final-ack":
    "Drop the first FIN acknowledgment. The receiver already has verified bytes and answers the retry during its 2 s dally.",
  blackhole:
    "Drop every packet. The sender stops after six attempts; there is no delivery or confirmation.",
  "final-blackhole":
    "Drop every FIN acknowledgment. The receiver delivers, but the sender cannot know that. Its failure must not be called non-delivery.",
};
let result = null,
  inputSnapshot = null,
  busy = false,
  dirty = false;
function notice(s, error = false) {
  $("notice").textContent = s;
  $("notice").classList.toggle("error", error);
}
function updateInput() {
  try {
    const size = text($("message").value).length;
    if (size > 4096) throw new Error("Lab text exceeds 4096 UTF-8 bytes");
    $("bytes").textContent = size + " bytes";
    $("run").disabled = busy;
    return true;
  } catch (e) {
    $("bytes").textContent = "Invalid input";
    $("run").disabled = true;
    notice(e.message + ". Previous results, if any, are unchanged.", true);
    return false;
  }
}
function selection() {
  const option = $("scenario").value;
  $("scenario-note").textContent = descriptions[option] ?? "Unknown scenario";
}
function invalidate() {
  dirty = true;
  $("download").disabled = true;
  if (result)
    notice(
      "Input changed. Results below describe the previous run. Run again to update them.",
    );
}
$("message").addEventListener("input", () => {
  invalidate();
  updateInput();
});
$("scenario").addEventListener("change", () => {
  selection();
  invalidate();
});
function node(tag, s, cls) {
  const n = document.createElement(tag);
  if (s !== undefined) n.textContent = s;
  if (cls) n.className = cls;
  return n;
}
async function run() {
  if (busy || !updateInput()) return;
  busy = true;
  for (const id of ["run", "reset", "message", "scenario", "download"])
    $(id).disabled = true;
  try {
    const content = $("message").value,
      scenario = $("scenario").value;
    const next = await simulate(text(content), scenario);
    result = next;
    inputSnapshot = { text: content, scenario };
    dirty = false;
    $("run-label").textContent = scenario.toUpperCase();
    $("sender-state").textContent = next.confirmed
      ? "Confirmed"
      : "Unconfirmed";
    $("receiver-state").textContent = next.delivered
      ? "Delivered"
      : "Not delivered";
    $("sender-detail").textContent = next.confirmed
      ? "Matching FIN ACK received. Peer reported verified in-memory delivery."
      : next.error + ". This does not prove non-delivery.";
    $("receiver-detail").textContent = next.delivered
      ? "All chunks accepted once and SHA-256 verified. No disk write or authenticated identity."
      : "No complete, digest-verified message in this experiment.";
    $("sender-card").className = next.confirmed ? "good" : "bad";
    $("receiver-card").className = next.delivered ? "good" : "bad";
    $("sent").textContent = String(next.sent);
    $("retries").textContent = String(next.retransmissions);
    $("elapsed").textContent = String(next.elapsed);
    $("duplicates").textContent = String(next.receiver.duplicates);
    $("received").value = next.delivered ? content : "";
    const body = $("trace-body");
    body.replaceChildren();
    for (const e of next.events) {
      const tr = node(
        "tr",
        undefined,
        ["drop", "corrupt", "delay", "duplicate"].includes(e.action)
          ? "fault"
          : "",
      );
      tr.append(
        node("td", e.ms + " ms"),
        node("td", e.actor === "sender" ? "Sender" : "Receiver"),
        node("td", e.kind + " " + e.seq),
        node("td", e.action),
      );
      body.append(tr);
    }
    $("event-count").textContent = next.events.length + " EVENTS";
    notice(
      next.confirmed
        ? "Crossing confirmed. The receiver verified " +
            text(content).length +
            " bytes and the final ACK returned."
        : next.delivered
          ? "Delivered, but UNCONFIRMED. Every final ACK was lost: the receiver has the bytes, while the sender stops without proof."
          : "No crossing confirmed. The retry budget was exhausted before the receiver verified a message.",
      !next.confirmed,
    );
  } catch (e) {
    notice("Experiment failed: " + e.message, true);
  } finally {
    busy = false;
    for (const id of ["reset", "message", "scenario"]) $(id).disabled = false;
    updateInput();
    $("download").disabled = !result || dirty;
  }
}
$("run").addEventListener("click", run);
$("reset").addEventListener("click", () => {
  if (busy) return;
  if (
    !confirm(
      "Discard the current message, experiment and trace, and restore the invented sample?",
    )
  )
    return;
  $("message").value = sample;
  $("scenario").value = "data-loss";
  result = null;
  inputSnapshot = null;
  dirty = false;
  $("run-label").textContent = "NOT RUN";
  $("sender-state").textContent = "Waiting";
  $("receiver-state").textContent = "Waiting";
  $("sender-detail").textContent = "No packets sent yet.";
  $("receiver-detail").textContent = "No message delivered yet.";
  $("sender-card").className = "";
  $("receiver-card").className = "";
  for (const id of ["sent", "retries", "elapsed", "duplicates"])
    $(id).textContent = "-";
  $("received").value = "";
  const tr = node("tr"),
    td = node("td", "Run an experiment to see the trace.", "empty");
  td.colSpan = 4;
  tr.append(td);
  $("trace-body").replaceChildren(tr);
  $("event-count").textContent = "0 EVENTS";
  $("download").disabled = true;
  selection();
  updateInput();
  notice("Invented sample restored. No network traffic was sent.");
});
$("download").addEventListener("click", () => {
  if (busy || dirty || !result || !inputSnapshot) return;
  const url = URL.createObjectURL(
    new Blob(
      [
        JSON.stringify(
          { format: "ferrywire-virtual-trace-1", input: inputSnapshot, result },
          null,
          2,
        ) + "\n",
      ],
      { type: "application/json" },
    ),
  );
  const a = node("a");
  a.href = url;
  a.download = "ferrywire-trace.json";
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notice(
    "Requested a new trace JSON download. It includes the message text. No network transfer or confirmed disk save.",
  );
});
selection();
updateInput();
