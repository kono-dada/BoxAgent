# Jev-Mem upstream

- Repository: https://github.com/libingzheren/Jev-Mem
- Vendored commit: `7ab0c73c6d8f4f611ad252c1e6ba8083f8df0e44`
- Imported on: 2026-10-07
- License: MIT; see `LICENSE` and `NOTICE`

BoxAgent vendors the runtime packages that provide admission, memory typing,
graph/vector/keyword storage, direct and multi-hop retrieval, temporal logic,
episode/narrative construction, consolidation, persistence, cache, audit,
Laya backends, and LoCoMo/LongMemEval adapters.

The upstream Git metadata, local caches, private data, and release automation
are deliberately excluded. Imports were mechanically namespaced under
`boxagent.infrastructure.memory.jev_mem` so the vendored code cannot collide with
generic top-level packages named `memory` or `utils`.
