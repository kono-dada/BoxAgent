"""Select a model profile and pair it with an injected Runtime factory."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from boxagent.agent.runtime.contracts import AgentRuntime
from boxagent.agent.runtime.models import ModelProfile


@dataclass(frozen=True)
class AgentRuntimeSelection:
    provider: str
    model: str
    profile: ModelProfile
    factory: Callable[[object], AgentRuntime]


class AgentRuntimeRegistry:
    """Select a Codex-backed model profile without duplicating the agent loop."""

    def __init__(self, *, task_model: str, deepseek_model: str,
                 runtime_factory: Callable[..., AgentRuntime],
                 deepseek_api_key: str = "", deepseek_base_url: str = "https://api.deepseek.com",
                 deepseek_model_catalog: Path | None = None):
        self.task_model = task_model
        self.deepseek_model = deepseek_model
        self.deepseek_api_key = deepseek_api_key
        self.deepseek_base_url = deepseek_base_url
        self.deepseek_model_catalog = (Path(deepseek_model_catalog).resolve()
                                       if deepseek_model_catalog else None)
        self.runtime_factory = runtime_factory

    def runtime(self, provider: str, model: str | None = None) -> AgentRuntimeSelection:
        provider = provider.strip().lower()
        if provider == "codex":
            selected_model = model or self.task_model
            profile = ModelProfile(
                provider="openai",
                model=selected_model,
                display_name="OpenAI",
            )
        elif provider == "deepseek":
            selected_model = model or self.deepseek_model
            profile = ModelProfile(
                provider="deepseek",
                model=selected_model,
                display_name="DeepSeek",
                base_url=self.deepseek_base_url,
                api_key_env="DEEPSEEK_API_KEY",
                api_key=self.deepseek_api_key,
                model_catalog=self.deepseek_model_catalog,
            )
        else:
            raise ValueError(f"不支持的任务模型供应商：{provider}")
        factory = lambda session: self.runtime_factory(session, model=selected_model)
        return AgentRuntimeSelection(provider, selected_model, profile, factory)
