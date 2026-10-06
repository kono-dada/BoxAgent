"""Executable dependency rules for the BoxAgent DDD-style package layout."""

import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "boxagent"


def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ["boxagent", *path.relative_to(PACKAGE).parent.parts]
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = package[:len(package) - node.level + 1]
                found.append(".".join([*prefix, *(node.module or "").split(".")]))
            else:
                found.append(node.module or "")
    return found


def layer_imports(layer):
    for path in (PACKAGE / layer).rglob("*.py"):
        for imported in imports(path):
            if imported.startswith("boxagent."):
                yield path, imported


class ArchitectureTests(unittest.TestCase):
    def test_settings_root_is_repository_root(self):
        from boxagent.bootstrap.settings import load_settings

        settings = load_settings()
        self.assertEqual(settings.root, ROOT)
        self.assertTrue((settings.root / "assets/pet/atlas-contract.json").is_file())

    def test_explicit_soul_file_overrides_default_persona(self):
        from boxagent.bootstrap.settings import load_settings

        with tempfile.TemporaryDirectory() as directory:
            soul = Path(directory) / "SOUL.md"
            soul.write_text("custom", encoding="utf-8")
            with patch.dict("os.environ", {"BOXAGENT_SOUL_FILE": str(soul)}):
                self.assertEqual(load_settings().soul_file, soul.resolve())

    def test_legacy_source_packages_do_not_return(self):
        legacy_files = (
            "audio.py", "config.py", "context.py", "deepseek.py", "desktop.py",
            "diagnostics.py", "domain.py", "executor.py", "hotkey.py", "memory.py",
            "memory_window.py", "planner.py", "providers.py", "runtime.py", "voice.py",
        )
        for name in legacy_files:
            self.assertFalse((PACKAGE / name).exists(), name)
        for name in ("features", "harness", "runtime", "ui", "engine", "contracts",
                     "appearance", "pets", "adapters"):
            source = list((PACKAGE / name).rglob("*.py")) if (PACKAGE / name).exists() else []
            self.assertEqual(source, [], f"legacy package {name}: {source}")

    def test_target_packages_and_files_exist(self):
        expected = {
            "application/assistant.py",
            "agent/harness/request_builder.py", "agent/harness/request.py",
            "agent/harness/context.py", "agent/harness/instructions.py",
            "agent/harness/persona.py", "agent/harness/executor.py",
            "agent/harness/policies/tools.py", "agent/harness/policies/permissions.py",
            "agent/harness/policies/result.py",
            "agent/runtime/contracts.py", "agent/runtime/models.py",
            "agent/runtime/registry.py",
            "domain/conversation/models.py", "domain/conversation/contracts.py",
            "domain/conversation/service.py", "domain/execution/service.py",
            "domain/environment/models.py", "domain/environment/contracts.py",
            "domain/interaction/contracts.py", "domain/interaction/service.py",
            "domain/memory/models.py", "domain/memory/contracts.py",
            "domain/memory/service.py", "domain/notification/models.py",
            "domain/notification/contracts.py", "domain/notification/service.py",
            "domain/perception/models.py", "domain/perception/service.py",
            "domain/skill/models.py", "domain/skill/contracts.py",
            "domain/skill/service.py", "domain/proactivity/contracts.py",
            "infrastructure/runtimes/codex/runtime.py",
            "infrastructure/runtimes/codex/app_server.py",
            "infrastructure/runtimes/codex/session.py",
            "infrastructure/runtimes/codex/computer_use.py",
            "infrastructure/runtimes/codex/skills.py",
            "infrastructure/runtimes/qwen/realtime.py",
            "infrastructure/memory/jev.py", "infrastructure/audio/pyaudio.py",
            "infrastructure/perception/qwen_mlx.py",
            "infrastructure/environment/local_system.py",
            "infrastructure/persistence/jsonl_session_repository.py",
            "infrastructure/persistence/json_notification_repository.py",
            "infrastructure/persistence/filesystem_skill_repository.py",
            "interfaces/engine/client.py", "interfaces/engine/protocol.py",
            "interfaces/engine/server.py", "interfaces/engine/supervisor.py",
            "interfaces/macos/app.py", "interfaces/macos/engine_bridge.py",
            "interfaces/macos/state.py", "interfaces/macos/menu.py",
            "interfaces/macos/windows/pet.py",
            "interfaces/macos/windows/conversation.py",
            "interfaces/macos/windows/memory.py",
            "interfaces/macos/windows/skills.py",
            "bootstrap/engine.py", "bootstrap/desktop.py", "bootstrap/settings.py",
            "entrypoints/engine.py", "entrypoints/desktop.py",
        }
        missing = sorted(path for path in expected if not (PACKAGE / path).is_file())
        self.assertEqual(missing, [])

    def assert_layer_excludes(self, layer, forbidden):
        offenders = []
        for path, imported in layer_imports(layer):
            if any(imported == f"boxagent.{name}" or
                   imported.startswith(f"boxagent.{name}.") for name in forbidden):
                offenders.append(f"{path.relative_to(PACKAGE)} -> {imported}")
        self.assertEqual(offenders, [])

    def test_core_is_independent(self):
        self.assert_layer_excludes(
            "core", {"domain", "agent", "application", "infrastructure",
                     "interfaces", "bootstrap", "entrypoints"})

    def test_domain_does_not_depend_on_outer_layers(self):
        self.assert_layer_excludes(
            "domain", {"agent", "application", "infrastructure", "interfaces",
                       "bootstrap", "entrypoints"})

    def test_agent_does_not_depend_on_implementations_or_delivery(self):
        self.assert_layer_excludes(
            "agent", {"application", "infrastructure", "interfaces", "bootstrap",
                      "entrypoints"})

    def test_application_does_not_depend_on_infrastructure_or_interfaces(self):
        self.assert_layer_excludes(
            "application", {"infrastructure", "interfaces", "bootstrap", "entrypoints"})

    def test_infrastructure_does_not_depend_on_application_or_interfaces(self):
        self.assert_layer_excludes(
            "infrastructure", {"application", "interfaces", "bootstrap", "entrypoints"})

    def test_interfaces_do_not_construct_or_import_infrastructure(self):
        self.assert_layer_excludes("interfaces", {"infrastructure", "bootstrap"})

    def test_subprocesses_are_confined_to_infrastructure_or_engine_interface(self):
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            if "create_subprocess_exec" not in path.read_text(encoding="utf-8"):
                continue
            relative = path.relative_to(PACKAGE)
            allowed = relative.parts[0] == "infrastructure" or relative.parts[:2] == (
                "interfaces", "engine")
            if not allowed:
                offenders.append(str(relative))
        self.assertEqual(offenders, [])

    def test_production_implementations_are_constructed_in_bootstrap(self):
        concrete = {
            "BoxAgentApplication", "EngineBridge", "CodexPetsAppearance",
            "JsonlSessionStore", "JevMemoryWorker", "PetCatalog",
            "AgentRuntimeRegistry", "CodexRuntimeHost", "MemoryDashboardWindow",
            "PetStoreWindow", "TaskExecutor", "SkillFileRepository", "SkillService",
            "WindowSummary",
            "LocalSystemEnvironmentProvider",
        }
        offenders = []
        for path in PACKAGE.rglob("*.py"):
            if path.parent == PACKAGE / "bootstrap":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id in concrete):
                    offenders.append(
                        f"{path.relative_to(PACKAGE)}:{node.lineno}:{node.func.id}")
        self.assertEqual(offenders, [])

    def test_entrypoints_are_thin(self):
        for name in ("desktop.py", "engine.py"):
            path = PACKAGE / "entrypoints" / name
            self.assertLessEqual(len(path.read_text(encoding="utf-8").splitlines()), 80)
            found = imports(path)
            self.assertFalse(any(
                imported.startswith(("boxagent.domain", "boxagent.agent",
                                     "boxagent.infrastructure"))
                for imported in found), found)

    def test_deepseek_is_a_model_profile_not_a_second_runtime(self):
        from boxagent.agent.runtime.registry import AgentRuntimeRegistry
        from boxagent.infrastructure.runtimes.codex.runtime import CodexAgentRuntime

        self.assertFalse((PACKAGE / "infrastructure/runtimes/deepseek.py").exists())
        registry = AgentRuntimeRegistry(
            task_model="codex", deepseek_model="deepseek-flash",
            runtime_factory=CodexAgentRuntime)
        self.assertIsInstance(registry.runtime("deepseek").factory(object()),
                              CodexAgentRuntime)


if __name__ == "__main__":
    unittest.main()
