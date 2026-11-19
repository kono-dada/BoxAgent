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

## Performance Records

- Record every quantified performance improvement in `docs/BoxAgent-实践记录.md`.
- Include the date, baseline and changed behavior, measurement conditions,
  before/after values and units, sampling method, validation results, and
  remaining limitations. Distinguish configured targets from measured results.
- Keep supporting measurements and test logs under `experiments/performance/`;
  identify transcribed observations separately from original machine output.
- Do not infer GPU utilization, battery savings, or game stability from a GPU
  helper process's CPU usage. Mark short uncontrolled measurements as preliminary.
