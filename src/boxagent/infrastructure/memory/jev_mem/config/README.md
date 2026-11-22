# Experiment profiles

- `jev_mem.json`: the default live profile used in README examples.
- `laya_mem.json`: local Laya decisions with PyTorch; install `.[laya]`.
- `laya_mlx_mem.json`: local Laya decisions on Apple Silicon with MLX;
  install `.[mlx]`. See the [Laya guide](../docs/laya.md).
- `jev_mem_accuracy.json`: an alternate experimental profile with different
  anchor counts, evidence limits, graph activation, and stopping thresholds.
  Its name does not establish an accuracy advantage or identify a paper run.

Pass a profile with `--jev-config PATH`. Validation and defaults are defined in
[`JevMemConfig`](../memory/jev_mem_config.py). Record the full profile with each
experiment. Credentials belong in `.env`, not configuration JSON files.
