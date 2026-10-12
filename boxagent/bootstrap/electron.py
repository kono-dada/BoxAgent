"""Compose nonvisual desktop services for Electron, without importing AppKit UI."""
from pathlib import Path

from boxagent.bootstrap.settings import Settings
from boxagent.interfaces.desktop.service import DesktopService
from boxagent.interfaces.desktop.engine_bridge import EngineBridge
from boxagent.interfaces.desktop.pets.catalog import PetCatalog
from boxagent.interfaces.desktop.pets.vrm import read_manifest


def create_electron_services(settings: Settings, *, pet_directory: Path | None,
                             task_provider: str, task_model: str | None,
                             auto_approve: bool, context_interval: float,
                             context_size: int) -> DesktopService:
    catalog = PetCatalog(settings.root / ".runtime/pets", default_pet=settings.default_pet,
                         bundled_root=settings.root / "assets/vrm/models")
    directory = pet_directory or catalog.current_directory()
    read_manifest(directory)
    backend = EngineBridge(root=settings.root, data_dir=settings.data_dir,
                           log_dir=settings.log_dir, task_provider=task_provider,
                           task_model=task_model, auto_approve=auto_approve,
                           context_interval=context_interval, context_size=context_size,
                           watch=settings.engine_watch, soul_file=settings.soul_file)
    return DesktopService(backend=backend, catalog=catalog, data_dir=settings.data_dir,
                          log_dir=settings.log_dir, soul_file=settings.soul_file,
                          directory=directory)
