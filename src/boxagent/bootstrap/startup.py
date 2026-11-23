"""Production startup checks are assembled at the composition boundary."""
import os

from boxagent.bootstrap.settings import load_settings
from boxagent.infrastructure.system.startup_probes import main as run_probe


def main() -> None:
    # The real Jev worker uses these SDK variables for the selected decision service.
    # As in bootstrap.memory, the default Aliyun service reuses the user's Qwen key.
    settings = load_settings()
    if settings.decision_api_key:
        os.environ['TYPESAFE_API_KEY'] = settings.decision_api_key
        os.environ['TYPESAFE_BASE_URL'] = settings.decision_base_url
        os.environ['TYPESAFE_DEFAULT_MODEL'] = settings.decision_model
    run_probe()

__all__ = ['main']
