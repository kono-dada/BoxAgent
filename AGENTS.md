# BoxAgent Engineering Instructions

- Target Python 3.12.x (`>=3.12,<3.13`) and use `uv` for Python commands.
- Preserve the dependency direction:
  `entrypoints -> bootstrap -> application/domain/agent <- infrastructure`.
- Keep production dependency construction in `boxagent/bootstrap/`; domain
  code must not import concrete infrastructure or UI modules.

## Python Types

- Type public functions, dataclass fields, protocols, factories, callbacks,
  and cross-module boundaries. Local variables may rely on inference.
- Prefer `Protocol`, `TypedDict`, dataclasses, and explicit unions over `Any`.
- Use `Any` only at dynamic external or JSON boundaries, then validate or
  narrow it before passing data into domain logic.
- Give factories explicit parameter and return contracts; use a callable
  `Protocol` when `Callable[..., T]` hides meaningful arguments.
- Do not weaken Pyright settings or add unjustified suppressions to pass.

## Verification

Before completing Python changes, run:

```bash
uvx --from pyright==1.1.414 pyright
UV_CACHE_DIR=/tmp/uvcache uv run --with pytest python -m pytest -q
```
