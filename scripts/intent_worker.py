"""Decision-only JSONL worker. Runs in the configured Jev Python environment."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from typesafe_sdk import Choice
from boxagent.infrastructure.memory.jev_mem.core.jev_client import JevClient
from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig


def main() -> None:
    config = JevMemConfig.load(jev_mock=False, timeout_seconds=.35, max_retries=0,
                               cache_size=0, audit_path="")
    client = JevClient(config)
    questions = {"action": Choice(instructions="用户希望桌面助手做什么？", criteria={
        "operate": "现在去操作电脑：打开/关闭应用或网页，播放、暂停、收藏、点赞、发消息、搜索、计算、点击、输入、查看屏幕内容",
        "chat": "聊天、提问、让助手记住或回忆某事、评论或抱怨，没有要求现在操作电脑"})}
    try:
        for line in sys.stdin:
            request = json.loads(line)
            try:
                if request["operation"] == "shutdown":
                    print(json.dumps({"id": request["id"], "ok": True, "result": {}}), flush=True)
                    break
                if request["operation"] == "health":
                    print(json.dumps({"id": request["id"], "ok": True, "result": {"status": "ready"}}), flush=True)
                    continue
                result = client.evaluate("intent", {"用户这句话": request["query"]}, questions)
                value = {"p_action": result.choices["action"].probabilities.get("operate")} if result else {}
                response = {"id": request["id"], "ok": True, "result": value}
            except Exception as exc:
                response = {"id": request["id"], "ok": False,
                            "error": {"message": type(exc).__name__}}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        client.close()


if __name__ == "__main__":
    main()
