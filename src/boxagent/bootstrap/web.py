"""Construct web evidence dependencies at the production composition boundary."""

from boxagent.application.web_research import WebResearchService
from boxagent.bootstrap.settings import Settings
from boxagent.infrastructure.web.deepseek import DeepSeekWebProvider


def create_web_research(settings: Settings) -> WebResearchService:
    provider = DeepSeekWebProvider(api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url) if settings.web_search_enabled and settings.deepseek_api_key else None
    return WebResearchService(provider)
