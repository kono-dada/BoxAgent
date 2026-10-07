"""Native window implementations, imported lazily outside macOS hosts."""

__all__ = ["MemoryDashboardWindow", "PersonaSettingsWindow", "SkillManagerWindow"]


def __getattr__(name):
    if name == "MemoryDashboardWindow":
        from boxagent.interfaces.macos.windows.memory import MemoryDashboardWindow
        return MemoryDashboardWindow
    if name == "PersonaSettingsWindow":
        from boxagent.interfaces.macos.windows.persona import PersonaSettingsWindow
        return PersonaSettingsWindow
    if name == "SkillManagerWindow":
        from boxagent.interfaces.macos.windows.skills import SkillManagerWindow
        return SkillManagerWindow
    raise AttributeError(name)
