import json
import os
import sys
import time

scenario = json.loads(open(os.environ["FAKE_SCENARIO"]).read())
calls_path = os.environ["FAKE_CALLS"]


def next_of(key, table):
    replies = table[key]
    return replies.pop(0) if len(replies) > 1 else replies[0]


def record(entry):
    with open(calls_path, "a") as f:
        f.write(json.dumps(entry) + "\n")


if sys.argv[1:] == ["--version"]:
    print(scenario["version"])
    sys.exit(0)
if sys.argv[1:] == ["doctor", "--json"]:
    print(json.dumps(scenario["doctor"]))
    sys.exit(1)

with open(os.environ["FAKE_PID"], "w") as f:
    f.write(str(os.getpid()))

for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    method, params = message["method"], message.get("params", {})
    if method in scenario.get("silent_on", []):
        for _ in range(scenario.get("chatter", 0)):
            print(json.dumps({"jsonrpc": "2.0", "method": "notifications/message", "params": {}}), flush=True)
            time.sleep(0.1)
        continue
    if method == "initialize" and scenario.get("refuse_initialize"):
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"message": "no"}}), flush=True)
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {}}
    elif method == "resources/read":
        result = {"contents": [{"text": json.dumps(next_of(params["uri"], scenario["resources"]))}]}
    elif method == "tools/call":
        args = params["arguments"]
        key = f"{params['name']}.{args['command']}"
        record({"call": key, "params": args["params"]})
        body = next_of(key, scenario["tools"])
        result = {"content": [{"text": json.dumps(body)}], "isError": body.get("state") == "C"}
    else:
        result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
