import base64, json, sys
from ferrywire.protocol import frames, encode
from ferrywire.simulation import simulate

out = []
for a in json.load(sys.stdin):
    data = base64.b64decode(a["data"])
    out.append(
        {
            "frames": [encode(f).hex() for f in frames(data, bytes(range(16)))],
            "result": simulate(data, a["scenario"]),
        }
    )
json.dump(out, sys.stdout)
