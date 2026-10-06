"""Product Skill storage and Codex projection contracts."""

import tempfile
import unittest
from pathlib import Path

from boxagent.infrastructure.persistence import SkillFileRepository
from boxagent.domain.skill import SkillService, SkillSource
from boxagent.infrastructure.runtimes.codex.skills import CodexSkillAdapter


def write_skill(root: Path, skill_id: str, *, name=None, description="测试技能"):
    path = root / skill_id
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(
        "---\n"
        f'name: "{name or skill_id}"\n'
        f'description: "{description}"\n'
        "---\n\n"
        "按照测试要求执行。\n",
        encoding="utf-8",
    )
    return path


class SkillFileRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.builtin = self.root / "builtin"
        self.user = self.root / "user"
        write_skill(self.builtin, "desktop-assistant", name="Desktop Assistant")
        self.service = SkillService(SkillFileRepository(
            builtin_root=self.builtin, user_root=self.user))

    def test_user_skill_crud_and_enabled_state_are_persistent(self):
        created = self.service.create(
            "music-helper", name="Music Helper", description="控制音乐播放",
            instructions="根据用户请求播放、暂停或切换音乐。")
        self.assertEqual(created.source, SkillSource.USER)
        self.assertTrue(created.enabled)
        self.assertEqual(created.instructions, "根据用户请求播放、暂停或切换音乐。")

        disabled = self.service.set_enabled("music-helper", False)
        self.assertFalse(disabled.enabled)
        reloaded = SkillService(SkillFileRepository(
            builtin_root=self.builtin, user_root=self.user))
        records = {item.skill_id: item for item in reloaded.list()}
        self.assertFalse(records["music-helper"].enabled)
        self.assertTrue(records["desktop-assistant"].enabled)

        updated = self.service.update(
            "music-helper", name="Music DJ", description="管理音乐播放",
            instructions="先观察播放器，再执行操作。")
        self.assertEqual(updated.name, "Music DJ")
        self.assertFalse(updated.enabled)

        self.service.delete("music-helper")
        self.assertNotIn("music-helper", {item.skill_id for item in self.service.list()})

    def test_rejects_unsafe_or_builtin_overwrite(self):
        with self.assertRaises(ValueError):
            self.service.create(
                "../escape", name="bad", description="bad", instructions="bad")
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.service.create(
                "desktop-assistant", name="duplicate",
                description="duplicate", instructions="duplicate")

    def test_scan_ignores_symlinks_and_invalid_manual_directories(self):
        external = self.root / "external"
        write_skill(external, "linked-directory")
        self.user.mkdir(parents=True, exist_ok=True)
        (self.user / "linked-directory").symlink_to(
            external / "linked-directory", target_is_directory=True)

        linked_manifest = self.user / "linked-manifest"
        linked_manifest.mkdir()
        (linked_manifest / "SKILL.md").symlink_to(
            external / "linked-directory" / "SKILL.md")
        write_skill(self.user, "INVALID ID")

        identifiers = {item.skill_id for item in self.service.list()}
        self.assertEqual(identifiers, {"desktop-assistant"})

    def test_runtime_policy_contains_only_enabled_managed_skills(self):
        self.service.create(
            "music-helper", name="Music Helper", description="控制音乐播放",
            instructions="播放音乐。")
        self.service.set_enabled("music-helper", False)
        policy = self.service.runtime_policy()
        self.assertEqual(policy.roots, (self.builtin.resolve(), self.user.resolve()))
        self.assertEqual(policy.enabled_paths, frozenset({
            (self.builtin / "desktop-assistant/SKILL.md").resolve(),
        }))


class CodexSkillAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_disables_external_skills_and_enables_managed_skills(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            builtin = root / "builtin"
            user = root / "user"
            workspace.mkdir()
            managed = write_skill(builtin, "desktop-assistant") / "SKILL.md"
            external = root / "global/irrelevant/SKILL.md"
            external.parent.mkdir(parents=True)
            external.write_text("---\nname: irrelevant\ndescription: no\n---\n")
            service = SkillService(SkillFileRepository(
                builtin_root=builtin, user_root=user))
            state = {str(managed.resolve()): False, str(external.resolve()): True}
            calls = []

            async def rpc(method, params):
                calls.append((method, params))
                if method == "skills/config/write":
                    state[params["path"]] = params["enabled"]
                    return {"effectiveEnabled": params["enabled"]}
                if method == "skills/list":
                    return {"data": [{"cwd": str(workspace), "errors": [], "skills": [
                        {"name": "desktop-assistant", "description": "desktop",
                         "path": str(managed), "scope": "user",
                         "enabled": state[str(managed.resolve())]},
                        {"name": "irrelevant", "description": "global",
                         "path": str(external), "scope": "user",
                         "enabled": state[str(external.resolve())]},
                    ]}]}
                return {}

            adapter = CodexSkillAdapter(service, workspace=workspace, log=lambda *_a, **_k: None)
            skills = await adapter.sync(rpc)

        self.assertTrue(state[str(managed.resolve())])
        self.assertFalse(state[str(external.resolve())])
        self.assertEqual(sum(1 for item in skills if item["enabled"]), 1)
        self.assertEqual(calls[0], ("skills/extraRoots/set", {
            "extraRoots": [str(builtin.resolve()), str(user.resolve())]}))
        self.assertEqual([method for method, _params in calls].count(
            "skills/config/write"), 2)

    async def test_unchanged_policy_does_not_rescan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / "workspace"
            builtin = root / "builtin"
            user = root / "user"
            workspace.mkdir()
            write_skill(builtin, "desktop-assistant")
            service = SkillService(SkillFileRepository(
                builtin_root=builtin, user_root=user))
            calls = []

            async def rpc(method, params):
                calls.append((method, params))
                if method == "skills/list":
                    return {"data": [{"cwd": str(workspace), "errors": [], "skills": []}]}
                return {}

            adapter = CodexSkillAdapter(service, workspace=workspace, log=lambda *_a, **_k: None)
            await adapter.sync(rpc)
            first_count = len(calls)
            await adapter.sync(rpc)

        self.assertEqual(len(calls), first_count)


if __name__ == "__main__":
    unittest.main()
