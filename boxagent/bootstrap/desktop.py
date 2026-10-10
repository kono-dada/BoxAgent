"""The production composition root for the native macOS host."""

from boxagent.interfaces.macos.pets.appearance import CodexPetsAppearance
from boxagent.interfaces.macos.pets.vrm import VrmAppearance
from boxagent.interfaces.macos.pets.catalog import PetCatalog
from boxagent.interfaces.macos.windows.pet_store import PetStoreWindow
from boxagent.interfaces.macos.app import Desktop
from boxagent.interfaces.macos.engine_bridge import EngineBridge
from boxagent.interfaces.macos.windows.memory import MemoryDashboardWindow
from boxagent.interfaces.macos.windows.skills import SkillManagerWindow
from boxagent.interfaces.macos.windows.persona import PersonaSettingsWindow
from boxagent.bootstrap.settings import load_settings
import json
from boxagent.agent.harness.persona import load_persona


def create_pet_store(owner, catalog):
    return PetStoreWindow.alloc().init().configure(owner, catalog)


def create_memory_dashboard(owner):
    return MemoryDashboardWindow.alloc().init().configure(owner)


def create_skill_manager(owner):
    return SkillManagerWindow.alloc().init().configure(owner)


def create_persona_settings(owner, source_file, target_file):
    return PersonaSettingsWindow.alloc().init().configure(
        owner, source_file, target_file)


def create_desktop_host(*, pet_directory=None, task_provider=None, task_model=None,
                        auto_approve=True, context_interval=0, context_size=960,
                        app_settings=None):
    """Create every production implementation used by the macOS host."""
    app_settings = app_settings or load_settings()
    contract_path = app_settings.root / "assets/pet/atlas-contract.json"
    def appearance_preparer(directory):
        manifest = json.loads((directory / "pet.json").read_text())
        if manifest.get("type") == "vrm":
            return VrmAppearance.prepare(directory)
        return CodexPetsAppearance.prepare(directory, contract_path=contract_path)

    def appearance_factory(directory, prepared=None):
        prepared = prepared or appearance_preparer(directory)
        if isinstance(prepared, dict) and prepared.get("type") == "vrm":
            return VrmAppearance(directory, prepared=prepared)
        return CodexPetsAppearance(directory, prepared=prepared, contract_path=contract_path)
    catalog = PetCatalog(app_settings.root / ".runtime/pets",
                         default_pet=app_settings.default_pet,
                         bundled_root=app_settings.root / "assets/vrm/models")
    directory = pet_directory or catalog.current_directory()
    try:
        appearance = appearance_factory(directory)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"形象无法启动，请检查模型与动作资源：{directory}；{error}") from error
    backend = EngineBridge(
        root=app_settings.root,
        data_dir=app_settings.data_dir,
        log_dir=app_settings.log_dir,
        task_provider=task_provider or app_settings.task_provider,
        task_model=task_model,
        auto_approve=auto_approve,
        context_interval=context_interval,
        context_size=context_size,
        watch=app_settings.engine_watch,
        soul_file=app_settings.soul_file,
    )
    persona = load_persona(app_settings.soul_file)
    default_soul = app_settings.root / "assets/personas/default/SOUL.md"
    editable_soul = (app_settings.data_dir / "SOUL.md"
                     if app_settings.soul_file == default_soul
                     else app_settings.soul_file)
    delegate = Desktop.alloc().init().configure(
        backend, appearance, catalog, data_dir=app_settings.data_dir,
        appearance_factory=appearance_factory,
        appearance_preparer=appearance_preparer,
        pet_store_factory=create_pet_store,
        memory_dashboard_factory=create_memory_dashboard,
        skill_manager_factory=create_skill_manager,
        persona_settings_factory=create_persona_settings,
        persona=persona, persona_loader=load_persona,
        soul_file=app_settings.soul_file,
        editable_soul_file=editable_soul)
    return backend, delegate
