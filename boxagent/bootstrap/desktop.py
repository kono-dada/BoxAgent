"""The production composition root for the native macOS host."""

from functools import partial

from boxagent.interfaces.macos.pets.appearance import CodexPetsAppearance
from boxagent.interfaces.macos.pets.catalog import PetCatalog
from boxagent.interfaces.macos.windows.pet_store import PetStoreWindow
from boxagent.interfaces.macos.app import Desktop
from boxagent.interfaces.macos.engine_bridge import EngineBridge
from boxagent.interfaces.macos.windows.memory import MemoryDashboardWindow
from boxagent.interfaces.macos.windows.skills import SkillManagerWindow
from boxagent.bootstrap.settings import load_settings


def create_pet_store(owner, catalog):
    return PetStoreWindow.alloc().init().configure(owner, catalog)


def create_memory_dashboard(owner):
    return MemoryDashboardWindow.alloc().init().configure(owner)


def create_skill_manager(owner):
    return SkillManagerWindow.alloc().init().configure(owner)


def create_desktop_host(*, pet_directory=None, task_provider=None, task_model=None,
                        auto_approve=True, context_interval=15, context_size=960,
                        app_settings=None):
    """Create every production implementation used by the macOS host."""
    app_settings = app_settings or load_settings()
    contract_path = app_settings.root / "assets/pet/atlas-contract.json"
    appearance_factory = partial(CodexPetsAppearance, contract_path=contract_path)
    appearance_preparer = partial(CodexPetsAppearance.prepare, contract_path=contract_path)
    catalog = PetCatalog(app_settings.root / ".runtime/pets",
                         default_pet=app_settings.default_pet)
    directory = pet_directory or catalog.current_directory()
    try:
        appearance = appearance_factory(directory)
    except (OSError, ValueError, KeyError, TypeError):
        if pet_directory:
            raise
        print("上次形象无法显示，已恢复内置小鸭。", flush=True)
        appearance = appearance_factory(app_settings.default_pet)
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
    )
    delegate = Desktop.alloc().init().configure(
        backend, appearance, catalog, data_dir=app_settings.data_dir,
        appearance_factory=appearance_factory,
        appearance_preparer=appearance_preparer,
        pet_store_factory=create_pet_store,
        memory_dashboard_factory=create_memory_dashboard,
        skill_manager_factory=create_skill_manager)
    return backend, delegate
