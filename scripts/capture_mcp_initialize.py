"""仅记录握手结构的测试服务；不连接执行器、不操作界面。"""

import json
from pathlib import Path
import sys


def redact(value):
    if isinstance(value, dict):
        return {key: "[已隐藏]" if any(word in key.lower() for word in ("token", "secret", "authorization")) else redact(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "initialize":
        with Path(sys.argv[1]).open("a") as output:
            output.write(json.dumps(redact(message), ensure_ascii=False) + "\n")
        result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "仅用于诊断的测试服务", "version": "0.1"}}
    elif message.get("method") == "tools/list":
        result = {"tools": []}
    elif "id" in message:
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32601, "message": "测试服务不执行工具"}}), flush=True)
        continue
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
