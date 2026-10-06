#!/usr/bin/env python3
"""Small JSONL process used to test the BoxAgent worker adapter."""

import json
import sys
import time


memories = []
for line in sys.stdin:
    request = json.loads(line)
    operation = request.get("operation")
    if operation == "health":
        result = {"status": "ready", "backend": "fixture", "memory_count": len(memories)}
    elif operation == "remember":
        created = []
        for observation in request["observations"]:
            item = {"id": f"memory-{len(memories) + 1}",
                    "content": observation["content"], "timestamp": None,
                    "metadata": observation.get("metadata", {})}
            memories.append(item)
            created.append(item)
        result = {"admitted": len(request["observations"]), "rejected": 0,
                  "created": created, "memory_count": len(memories)}
    elif operation == "query":
        result = {"evidence": "fixture evidence", "memories": memories[:request["top_k"]],
                  "trace": {"controller": "fixture"}}
    elif operation == "inspect":
        nodes = [{"id": item["id"], "type": "EVENT", "content": item["content"],
                  "timestamp": item.get("timestamp"), "source": "fixture"} for item in memories]
        result = {"nodes": nodes[:request.get("node_limit", 100)], "edges": [],
                  "selected_id": request.get("selected_id"), "truncated": False,
                  "statistics": {"node_count": len(nodes), "edge_count": 0,
                                 "matched_count": len(nodes), "node_types": {"EVENT": len(nodes)},
                                 "link_types": {}}}
    elif operation == "save":
        result = {"saved": True, "memory_count": len(memories)}
    elif operation == "forget":
        requested = set(request["memory_ids"])
        deleted = [item for item in memories if item["id"] in requested]
        missing = [item for item in request["memory_ids"] if not any(m["id"] == item for m in memories)]
        memories[:] = [item for item in memories if item["id"] not in requested]
        result = {"deleted": deleted, "missing": missing, "memory_count": len(memories)}
    elif operation == "shutdown":
        result = {"stopped": True}
    elif operation == "slow":
        time.sleep(10)
        result = {"finished": True}
    elif operation == "fail":
        print(json.dumps({"id": request["id"], "ok": False,
                          "error": {"code": "fixture", "message": "expected failure"}}), flush=True)
        continue
    else:
        result = {}
    print(json.dumps({"id": request["id"], "ok": True, "result": result}), flush=True)
    if operation == "shutdown":
        break
