# BoxAgent Engineering Instructions

- Target Python 3.12.x (`>=3.12,<3.13`) and use `uv` for Python commands.
- Preserve the dependency direction:
  `entrypoints -> bootstrap -> application/domain/agent <- infrastructure`.
- Keep production dependency construction in `boxagent/bootstrap/`; domain
  code must not import concrete infrastructure or UI modules.

## Python Types

- In all first-party Python code, including legacy code, type function parameters and returns (`-> None` included), dataclass fields, and callbacks. Use modern syntax (`list[str]`, `X | None`). Infer locals, `self`, and `cls`.
- Use dataclasses for internal records (`frozen=True` for immutable values), `TypedDict` for dictionary interfaces, and typed mappings for lookups. Replace untyped records in affected flows at the source, update all callers, and preserve compatibility by default.
- Use `Any` only at dynamic external or JSON boundaries, then validate or
  narrow it before passing data into domain logic.
- Give factories explicit parameter and return contracts; use a callable
  `Protocol` when `Callable[..., T]` hides meaningful arguments.
- Do not weaken Pyright settings or add unjustified suppressions to pass.

## Verification

Before handing off Python changes, run all three commands from the repository
root. These are the same commands used by [CI](.github/workflows/verify.yml).

```bash
uv run --locked ruff check
uv run --locked mypy
uv run --locked pytest
```

- Ruff and strict mypy must report zero diagnostics. The full pytest suite must
  finish with zero failures.
- Fix diagnostics and failures, then rerun the checks. Use the rules and
  coverage in [pyproject.toml](pyproject.toml). Keep mypy strict and avoid adding
  exclusions, suppressions, or test skips to get a passing run.
- Report the result of each command in the handoff. If an environment issue
  prevents a check from running, report the command and the blocker.

Also run the existing Pyright check for Python changes.

```bash
uvx --from pyright==1.1.414 pyright
```

## Implementation Records

- For each feature or bug fix, append a dated entry to `docs/BoxAgent-实践记录.md`
  in the same change. Keep it brief and cover background, problem, decision and
  implementation, and observed outcome.
- Link relevant code, tests, or existing evidence. State which checks passed and
  which behavior still needs live verification. Update the existing entry when
  follow-up work changes the outcome.

## Performance Records

- Record every quantified performance improvement in `docs/BoxAgent-实践记录.md`.
- Include the date, baseline and changed behavior, measurement conditions,
  before/after values and units, sampling method, validation results, and
  remaining limitations. Distinguish configured targets from measured results.
- Keep supporting measurements and test logs under `experiments/performance/`;
  identify transcribed observations separately from original machine output.
- Do not infer GPU utilization, battery savings, or game stability from a GPU
  helper process's CPU usage. Mark short uncontrolled measurements as preliminary.
