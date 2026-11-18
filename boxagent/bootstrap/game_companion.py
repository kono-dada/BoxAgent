"""Opt-in macOS game companion composition."""

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.game_companion import GameCompanion
from boxagent.application.game_entry import GameEntry
from boxagent.bootstrap.settings import Settings
from boxagent.infrastructure.perception.qwen_mlx import WindowSummary
from boxagent.infrastructure.perception.wuwa import WuwaVision
from boxagent.infrastructure.persistence.game_entry import QuestProgressFile


def install_game_companion(application: BoxAgentApplication, settings: Settings) -> None:
    interaction = application.interaction_service
    local_model = WindowSummary(
        application.emit, model=settings.perception_model, workspace=settings.root,
        python=settings.perception_python, worker_script=settings.root / "scripts/window_summary_worker.py",
        log_path=settings.log_dir / "game-vision-worker.log",
    )
    entry = GameEntry(QuestProgressFile(settings.data_dir / "games/wuwa-progress.json"),
                      interaction.announce_game_line)
    companion = GameCompanion(WuwaVision(local_model), interaction.game_voice,
                              interaction.announce_game_highlight, entry=entry)
    interaction.game_reference = companion.reference
    interaction.game_playback_started = companion.playback_started
    interaction.game_entry_playback_started = entry.playback_started
    application.runtime_resources = (*application.runtime_resources, companion)
