"""Public build/query API for persistent Jev-Mem memory."""
from datetime import datetime
from pathlib import Path

from boxagent.infrastructure.memory.jev_mem.core.memory_builder import MemoryBuilder
from boxagent.infrastructure.memory.jev_mem.core.query_engine import QueryEngine
from boxagent.infrastructure.memory.jev_mem.core.jev_mem_config import JevMemConfig


class JevMemSystem:
    def __init__(self, model="gpt-4o-mini", embedding_model="minilm", cache_dir="./cache",
                 jev_config=None, memory_builder=None):
        self.cache_dir = Path(cache_dir)
        config = jev_config or JevMemConfig()
        self.memory_builder = memory_builder if memory_builder is not None else MemoryBuilder(
            cache_dir=str(self.cache_dir), llm_model=model, embedding_model=embedding_model, jev_config=config)
        self.trg_memory = self.memory_builder.trg
        self.graph_db = self.trg_memory.graph_db
        self.vector_db = self.trg_memory.vector_db
        self.llm_controller = self.memory_builder.llm_controller
        self.answer_formatter = self.memory_builder.answer_formatter
        self.query_engine = QueryEngine(self.trg_memory, self.memory_builder.node_index,
                                       jev_config=self.memory_builder.jev_config, jev_client=self.memory_builder.jev)

    def build_memory_from_conversation(self, conversation_data):
        """Accept raw observations; LoCoMo samples use the existing sample builder."""
        if hasattr(conversation_data, 'conversation'):
            return self.memory_builder.build_memory(conversation_data)
        if not isinstance(conversation_data, list):
            raise ValueError("Input must be a JSON list of strings or objects with content/text")
        # Validate the full input before any insertion.
        observations = []
        for item in conversation_data:
            if isinstance(item, str):
                item = {"content": item}
            if not isinstance(item, dict):
                raise ValueError("Each observation must be a string or object")
            content = item.get('content', item.get('text'))
            if not isinstance(content, str) or not content.strip():
                raise ValueError("Each observation needs non-empty content or text")
            timestamp = item.get('timestamp')
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            if timestamp is not None and not isinstance(timestamp, datetime):
                raise ValueError("timestamp must be ISO 8601 text or null")
            metadata = item.get('metadata', {})
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
            observations.append((content, timestamp, metadata))
        admitted = sum(self.memory_builder.build(content, timestamp, metadata) is not None
                       for content, timestamp, metadata in observations)
        return {"admitted": admitted, "rejected": len(observations) - admitted}

    def query(self, question):
        context, evidence = self.query_engine.query(question, top_k=5)
        if not self.llm_controller:
            return evidence or "Information not found"
        prompt = self.answer_formatter.build_qa_prompt(evidence, question)
        response = self.llm_controller.llm.get_completion(prompt, response_format={"type": "text"}, temperature=0.0)
        self.memory_builder.jev.audit.emit("system_two_answer", query_id=context.metadata.get('query_id'), llm_calls=1)
        return self.answer_formatter.extract_answer(response, question)

    def save_memory(self, save_path=None):
        if save_path is not None:
            self.memory_builder.cache_dir = Path(save_path)
        self.memory_builder.save()

    def load_memory(self, load_path=None):
        if load_path is not None:
            self.memory_builder.cache_dir = Path(load_path)
        self.memory_builder.load()
        self.query_engine.node_index = self.memory_builder.node_index
