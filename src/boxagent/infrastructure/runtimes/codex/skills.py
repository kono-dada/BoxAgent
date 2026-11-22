"""Project the BoxAgent Skill catalog into Codex App Server."""

from pathlib import Path
from typing import Protocol


class SkillPolicyProvider(Protocol):
    def runtime_policy(self): ...


class CodexSkillAdapter:
    """Keep Codex discovery aligned with BoxAgent's path-based allowlist."""

    def __init__(self, service: SkillPolicyProvider, *, workspace: Path, log):
        self.service = service
        self.workspace = Path(workspace).resolve()
        self.log = log
        self.signature: tuple[str, ...] = ()
        self.inventory = []
        self.initialized = False

    async def sync(self, rpc, *, force=False):
        policy = self.service.runtime_policy()
        if self.initialized and not force and policy.signature == self.signature:
            return self.inventory
        await rpc("skills/extraRoots/set", {
            "extraRoots": [str(root) for root in policy.roots],
        })
        skills = await self._list(rpc)
        changed = 0
        for item in skills:
            path = self._resolved_path(item.get("path"))
            desired = path in policy.enabled_paths if path else False
            if bool(item.get("enabled")) == desired:
                continue
            await rpc("skills/config/write", {
                "path": str(path), "enabled": desired,
            })
            changed += 1
        if changed:
            skills = await self._list(rpc)
        self.signature = policy.signature
        self.inventory = skills
        self.initialized = True
        self.log(
            "skills_synchronized", discovered=len(skills), changed=changed,
            enabled=sum(1 for item in skills if item.get("enabled")),
            roots=[str(root) for root in policy.roots])
        return skills

    async def _list(self, rpc):
        result = await rpc("skills/list", {
            "cwds": [str(self.workspace)], "forceReload": True,
        })
        entries = result.get("data", [])
        entry = next(
            (item for item in entries
             if Path(item.get("cwd", "")).resolve() == self.workspace),
            entries[0] if entries else {},
        )
        errors = entry.get("errors", [])
        if errors:
            self.log("skills_discovery_errors", errors=errors)
        return list(entry.get("skills", []))

    @staticmethod
    def _resolved_path(value):
        if not isinstance(value, str) or not value:
            return None
        return Path(value).resolve()
