"""Opt-in macOS game companion composition."""

import asyncio
from collections.abc import Callable

from boxagent.application.assistant import BoxAgentApplication
from boxagent.application.game_companion import QUEST_PERIOD, GameCompanion
from boxagent.application.game_entry import GameEntry, GameEntryInbox
from boxagent.bootstrap.settings import Settings
from boxagent.domain.proactivity.game_entry import TrackedQuest
from boxagent.domain.proactivity.game_highlight import GameHighlight
from boxagent.infrastructure.perception.deepseek_quest import DeepSeekQuestVision
from boxagent.infrastructure.perception.game_stream import GameWindowStream
from boxagent.infrastructure.perception.qwen_mlx import WindowSummary
from boxagent.infrastructure.perception.wuwa import WuwaVision
from boxagent.infrastructure.perception.wuwa_quest import read_quest
from boxagent.infrastructure.persistence.game_entry import QuestProgressFile


def install_game_companion(
    application: BoxAgentApplication, settings: Settings, inbox: GameEntryInbox
) -> None:
    interaction = application.interaction_service

    async def publish(receipt: str, text: str, still_current: Callable[[], bool], *,
                      event_type: str = "game.entry") -> bool:
        return await inbox.publish(receipt, text,
            lambda: not interaction.game_speech_paused and still_current(), event_type=event_type)

    local_model = WindowSummary(
        application.emit,
        model=settings.perception_model,
        workspace=settings.root,
        python=settings.perception_python,
        worker_script=settings.root / "scripts/window_summary_worker.py",
        log_path=settings.log_dir / "game-vision-worker.log",
        vision_bundle=settings.vision_bundle,
    )
    entry = GameEntry(QuestProgressFile(settings.data_dir / "games/wuwa-progress.json"), publish)
    cloud = (
        DeepSeekQuestVision(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
        if settings.wuwa_cloud_vision_enabled or settings.wuwa_scene_vision_enabled
        else None
    )
    cloud_quest = cloud if settings.wuwa_cloud_vision_enabled else None
    cloud_scene = cloud if settings.wuwa_scene_vision_enabled else None

    async def recognize_local_quest(image: bytes) -> TrackedQuest | None:
        return await asyncio.to_thread(read_quest, image, cropped=True)

    async def announce(highlight: GameHighlight, still_current: Callable[[], bool]) -> bool:
        return await publish(
            highlight.receipt_id, highlight.review.praise, still_current, event_type="game.scene"
        )

    vision = WuwaVision(
        local_model,
        cloud_scene=cloud_scene,
        recent_comment=lambda: companion.last_moment,
        resumed_quest=entry.resumed_quest,
        capture_stream=GameWindowStream(
            source=settings.root / "scripts/game-window-stream.swift",
            cache=settings.data_dir / "runtimes/game-capture",
        ),
    )
    companion = GameCompanion(
        vision,
        interaction.game_voice,
        announce,
        entry=entry,
        recognize_quest=cloud_quest or recognize_local_quest,
        quest_period=QUEST_PERIOD if cloud_quest else 0.0,
        cloud_scene=cloud_scene is not None,
        prepare_audio=interaction.prepare_game_audio,
        enabled=lambda: application.state.game_companion_enabled,
    )
    interaction.game_reference = companion.reference
    interaction.game_playback_started = companion.playback_started
    interaction.game_entry_playback_started = companion.entry_playback_started
    application.runtime_resources = (*application.runtime_resources, companion)
    application.emit("game.companion_available", game_companion_available=True)
