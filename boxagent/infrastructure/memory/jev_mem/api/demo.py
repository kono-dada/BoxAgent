"""Run the Jev-Mem pipeline offline with explicitly mocked Jev and embeddings."""
import argparse
import json
from datetime import datetime
from pathlib import Path

from boxagent.infrastructure.memory.jev_mem.core.memory_builder import MemoryBuilder
from boxagent.infrastructure.memory.jev_mem.core.mock_encoder import MockEncoder
from boxagent.infrastructure.memory.jev_mem.core.query_engine import QueryEngine
from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig
from boxagent.infrastructure.memory.jev_mem.core.trg_memory import TemporalResonanceGraphMemory
from boxagent.infrastructure.memory.jev_mem.core.vector_db import NumpyVectorDB


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", default="./jev_mem_cache/demo")
    args = parser.parse_args()
    encoder = MockEncoder()
    trg = TemporalResonanceGraphMemory(vector_db=NumpyVectorDB(encoder.dimension), encoder=encoder, llm_backend=None)
    config = JevMemConfig(write_enabled=True, read_enabled=True, jev_mock=True,
                          audit_path=str(Path(args.cache_dir) / "decisions.jsonl"), anchor_count=1)
    builder = MemoryBuilder(args.cache_dir, jev_config=config, trg_memory=trg, llm_enabled=False)
    observations = ["Thanks!", "Alice started the Jev-Mem project in Dallas.",
                    "Alice prefers concise explanations about the Jev-Mem project.",
                    "Alice presented the Jev-Mem project results on Friday."]
    for day, text in enumerate(observations, 1):
        node = builder.build(text, timestamp=datetime(2026, 9, day), metadata={"source": "offline_demo", "entities": ["Alice"]})
        print(("STORED: " if node else "REJECTED: ") + text)
    builder.save()
    engine = QueryEngine(trg, builder.node_index, jev_config=config, jev_client=builder.jev)
    context, evidence = engine.query("What does Alice prefer about Jev-Mem explanations?")
    print(evidence)
    print(json.dumps(context.metadata, indent=2))
    print("Offline mock demo complete. System-Two generation is not called; these are retrieved observations.")


if __name__ == "__main__":
    main()
