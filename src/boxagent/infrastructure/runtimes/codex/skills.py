"""Project the BoxAgent Skill catalog into Codex App Server."""

from collections.abc import Awaitable
from pathlib import Path
from typing import Any, Protocol

from boxagent.domain.skill.service import SkillRuntimePolicy


class SkillPolicyProvider(Protocol):
    def runtime_policy(self) -> SkillRuntimePolicy: ...


type _RpcObject = dict[str, Any]


class _Rpc(Protocol):
    def __call__(
        self, method: str, params: _RpcObject, /, timeout: float = 120
    ) -> Awaitable[_RpcObject]: ...


class _Log(Protocol):
    def __call__(self, event: str, /, **data: object) -> None: ...


class CodexSkillAdapter:
    """Keep Codex discovery aligned with BoxAgent's path-based allowlist."""

    def __init__(self, service: SkillPolicyProvider, *, workspace: Path, log: _Log) -> None:
        self.service = service
        self.workspace = Path(workspace).resolve()
        self.log = log
        self.signature: tuple[str, ...] = ()
        self.inventory: list[_RpcObject] = []
        self.initialized = False

    async def sync(self, rpc: _Rpc, *, force: bool = False) -> list[_RpcObject]:
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

    async def _list(self, rpc: _Rpc) -> list[_RpcObject]:
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
    def _resolved_path(value: object) -> Path | None:
        if not isinstance(value, str) or not value:
            return None
        return Path(value).resolve()
