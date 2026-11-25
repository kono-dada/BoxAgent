"""Construct web evidence dependencies at the production composition boundary."""

from boxagent.application.web_research import WebResearchService
from boxagent.bootstrap.settings import Settings, read_optional_setting
from boxagent.domain.web.contracts import WebProvider
from boxagent.infrastructure.web.bailian import BailianWebProvider
from boxagent.infrastructure.web.deepseek import DeepSeekWebProvider


def create_web_research(settings: Settings) -> WebResearchService:
    provider: WebProvider | None = (
        DeepSeekWebProvider(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
        if settings.web_search_enabled and settings.deepseek_api_key
        else None
    )
    mode = read_optional_setting("BOXAGENT_WEB_PROVIDER") or "deepseek"
    if mode not in {"deepseek", "bailian"}:
        raise ValueError("BOXAGENT_WEB_PROVIDER 只支持 deepseek 或 bailian")
    if settings.web_search_enabled and mode == "bailian":
        origin = (
            f"https://{settings.qwen_workspace_id}.{settings.qwen_region}.maas.aliyuncs.com"
            if settings.qwen_workspace_id
            else "https://dashscope.aliyuncs.com"
        )
        provider = BailianWebProvider(
            api_key=settings.qwen_api_key,
            endpoint=origin + "/api/v1/services/aigc/text-generation/generation",
            model=read_optional_setting("BOXAGENT_WEB_SEARCH_MODEL") or "qwen-plus",
            fallback=provider,
        )
    return WebResearchService(provider)
