# Repository Guidelines

## Project Structure & Module Organization

This repository is a small Python 3.12 benchmarking toolkit for fio-based NAS tests. The main scripts live at the repo root:

- `run_nas_bench.py`: runs the benchmark suite and writes timestamped output.
- `parse_fio_results.py`: parses fio JSON/result directories into summaries.
- `plot_results.py`: generates benchmark plots from one or more result directories.
- `docs/benchmark-results.md`: written benchmark analysis.
- `docs/plots/`: checked-in plot images referenced by the README and docs.

Local benchmark output belongs in `results/`, which is intentionally git-ignored because it can be large.

## Build, Test, and Development Commands

- `uv sync`: create or update the local environment from `pyproject.toml` and `uv.lock`.
- `uv run run_nas_bench.py --quick --label nfs-quick`: run a short validation benchmark.
- `uv run run_nas_bench.py --label nfs`: run the standard NFS benchmark using the default `~/nas` mount.
- `uv run parse_fio_results.py results/TIMESTAMP-label/`: regenerate summaries for a previous run.
- `uv run plot_results.py results/TIMESTAMP-label/ --out-dir docs/plots`: regenerate documentation plots.
- `uv run ruff check .`: lint Python files.
- `uv run ruff format .`: format Python files.

Install the external `fio` package before running benchmarks.

## Coding Style & Naming Conventions

Use Python 3.12 syntax and keep scripts directly executable through `uv run`. Follow Ruff formatting; do not hand-format around the formatter. Prefer clear function names, `snake_case` variables, and descriptive argparse option names such as `--skip-prepare` or `--zipf-runtime`. Keep comments focused on benchmark assumptions, fio behavior, or NAS-specific caveats.

## Testing Guidelines

There is no dedicated automated test suite yet. Validate changes by running `uv run ruff check .` and, when touching benchmark execution, a quick benchmark with `--quick` against a safe mount. For parser or plotting changes, re-run against an existing `results/` directory and inspect regenerated summaries or plots.

## Commit & Pull Request Guidelines

Recent commits use concise, imperative, sentence-case messages such as `Add plot generation with docs/plots and README image links` or `Fix --pre-warm to use random reads, not sequential`. Keep commits scoped to one logical change.

Pull requests should describe the benchmark or documentation impact, list commands run, and mention any mount protocol or hardware assumptions. Include updated plots when changing result interpretation or visualization.

## Security & Configuration Tips

Do not commit private mount paths, credentials, raw large result directories, or host-specific secrets. CIFS/NFS mount examples in documentation should remain generic unless the values are intentionally public.
