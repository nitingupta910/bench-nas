#!/usr/bin/env python3
"""Generate benchmark plots from one or more fio results directories."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

matplotlib.use("Agg")  # no display needed

from parse_fio_results import TEST_ORDER, load_results  # noqa: E402

# Short labels for chart axes
SHORT_NAMES = {
    "seqwrite-control": "Seq\nwrite",
    "seqread-control": "Seq\nread",
    "randread-uniform-1": "Uniform\npass 1",
    "randread-uniform-2": "Uniform\npass 2",
    "zipf-randread-1": "Zipf\npass 1",
    "zipf-randread-2": "Zipf\npass 2 ★",
    "randrw-zipf": "randrw\nZipf",
    # Legacy
    "randread-cold": "Uniform\npass 1",
    "randread-prewarm": "Uniform\npass 1",
    "randread-hot": "Uniform\npass 2",
    "zipf-randread-2-hot": "Zipf\npass 2 ★",
    "randrw-cache": "randrw\nZipf",
}

STYLE = {
    "figure.facecolor": "#0f1117",
    "axes.facecolor": "#1a1d27",
    "axes.edgecolor": "#3a3d4d",
    "axes.labelcolor": "#c0c4d0",
    "axes.titlecolor": "#e0e4f0",
    "xtick.color": "#8890a0",
    "ytick.color": "#8890a0",
    "text.color": "#c0c4d0",
    "grid.color": "#2a2d3d",
    "grid.linewidth": 0.6,
    "legend.facecolor": "#1a1d27",
    "legend.edgecolor": "#3a3d4d",
    "font.family": "sans-serif",
    "font.size": 10,
}

READ_COLOR = "#4da6ff"
WRITE_COLOR = "#ff7043"
ACCENT = "#ffd740"
PALETTE = ["#4da6ff", "#ff7043", "#66bb6a", "#ffb74d", "#ab47bc", "#26c6da"]


def _ordered_tests(results: dict[str, dict]) -> list[str]:
    ordered = [k for k in TEST_ORDER if k in results]
    ordered += [k for k in results if k not in TEST_ORDER]
    return ordered


def _label(name: str) -> str:
    return SHORT_NAMES.get(name, name)


# ---------------------------------------------------------------------------
# Plot 1: IOPS bar chart
# ---------------------------------------------------------------------------


def plot_iops(results: dict[str, dict], out: Path, title: str = "") -> None:
    tests = _ordered_tests(results)
    read_iops = [results[t]["read_iops"] for t in tests]
    write_iops = [results[t]["write_iops"] for t in tests]
    labels = [_label(t) for t in tests]

    x = np.arange(len(tests))
    width = 0.38

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(12, 5))
        bars_r = ax.bar(
            x - width / 2,
            read_iops,
            width,
            label="Read IOPS",
            color=READ_COLOR,
            alpha=0.9,
        )
        bars_w = ax.bar(
            x + width / 2,
            write_iops,
            width,
            label="Write IOPS",
            color=WRITE_COLOR,
            alpha=0.9,
        )

        # Value labels on bars
        for bar in list(bars_r) + list(bars_w):
            h = bar.get_height()
            if h > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    h * 1.02,
                    f"{h:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    color="#c0c4d0",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("IOPS")
        ax.set_title(
            f"{'IOPS by Test' if not title else title + ' — IOPS'}", fontsize=13
        )
        ax.legend()
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.grid(axis="y")
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 2: p99 latency bar chart (log scale)
# ---------------------------------------------------------------------------


def plot_latency(results: dict[str, dict], out: Path, title: str = "") -> None:
    tests = _ordered_tests(results)
    read_p99 = [max(results[t]["read_p99_ms"], 0.001) for t in tests]
    write_p99 = [max(results[t]["write_p99_ms"], 0.001) for t in tests]
    labels = [_label(t) for t in tests]

    x = np.arange(len(tests))
    width = 0.38

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(12, 5))
        bars_r = ax.bar(
            x - width / 2,
            read_p99,
            width,
            label="Read p99 ms",
            color=READ_COLOR,
            alpha=0.9,
        )
        bars_w = ax.bar(
            x + width / 2,
            write_p99,
            width,
            label="Write p99 ms",
            color=WRITE_COLOR,
            alpha=0.9,
        )

        for bar, val in zip(list(bars_r) + list(bars_w), read_p99 + write_p99):
            if val > 0.001:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    val * 1.15,
                    f"{val:.2f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    color="#c0c4d0",
                )

        ax.set_yscale("log")
        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("p99 latency (ms) — log scale")
        ax.set_title(
            f"{'p99 Latency by Test (lower is better)' if not title else title + ' — p99 Latency'}",
            fontsize=13,
        )
        ax.legend()
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax.grid(axis="y", which="both")
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 3: Bandwidth bar chart
# ---------------------------------------------------------------------------


def plot_bandwidth(results: dict[str, dict], out: Path, title: str = "") -> None:
    tests = _ordered_tests(results)
    read_bw = [results[t]["read_bw_mb"] for t in tests]
    write_bw = [results[t]["write_bw_mb"] for t in tests]
    labels = [_label(t) for t in tests]

    x = np.arange(len(tests))
    width = 0.38

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(12, 5))
        bars_r = ax.bar(
            x - width / 2,
            read_bw,
            width,
            label="Read MB/s",
            color=READ_COLOR,
            alpha=0.9,
        )
        bars_w = ax.bar(
            x + width / 2,
            write_bw,
            width,
            label="Write MB/s",
            color=WRITE_COLOR,
            alpha=0.9,
        )

        for bar in list(bars_r) + list(bars_w):
            h = bar.get_height()
            if h > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    h * 1.02,
                    f"{h:.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                    color="#c0c4d0",
                )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel("Bandwidth (MB/s)")
        ax.set_title(
            f"{'Bandwidth by Test' if not title else title + ' — Bandwidth'}",
            fontsize=13,
        )
        ax.legend()
        ax.grid(axis="y")
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 4: Cache lift chart — Zipf pass1 vs pass2 + HDD baseline
# ---------------------------------------------------------------------------


def plot_cache_lift(results: dict[str, dict], out: Path, title: str = "") -> None:
    z1 = results.get("zipf-randread-1", {})
    z2 = results.get("zipf-randread-2") or results.get("zipf-randread-2-hot") or {}
    sr = results.get("seqread-control", {})
    u1 = results.get("randread-uniform-1") or results.get("randread-cold") or {}
    u2 = results.get("randread-uniform-2") or results.get("randread-hot") or {}

    if not z1 or not z2:
        return

    categories = []
    iops_vals = []
    p99_vals = []
    colors = []

    if sr and sr["read_iops"] > 0:
        categories.append("HDD seq\n(baseline)")
        iops_vals.append(sr["read_iops"])
        p99_vals.append(sr["read_p99_ms"])
        colors.append("#78909c")

    if u1 and u1["read_iops"] > 0:
        categories.append("Uniform\npass 1")
        iops_vals.append(u1["read_iops"])
        p99_vals.append(u1["read_p99_ms"])
        colors.append("#ffb74d")

    if u2 and u2["read_iops"] > 0:
        categories.append("Uniform\npass 2")
        iops_vals.append(u2["read_iops"])
        p99_vals.append(u2["read_p99_ms"])
        colors.append("#ffd740")

    categories.append("Zipf\npass 1")
    iops_vals.append(z1["read_iops"])
    p99_vals.append(z1["read_p99_ms"])
    colors.append("#4fc3f7")

    categories.append("Zipf\npass 2 ★")
    iops_vals.append(z2["read_iops"])
    p99_vals.append(z2["read_p99_ms"])
    colors.append(ACCENT)

    x = np.arange(len(categories))

    with plt.rc_context(STYLE):
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5))
        fig.suptitle(
            f"{'NVMe Cache Lift — IOPS & p99 Latency' if not title else title + ' — Cache Lift'}",
            fontsize=13,
        )

        # IOPS
        bars = ax1.bar(x, iops_vals, color=colors, alpha=0.9, width=0.55)
        for bar, v in zip(bars, iops_vals):
            ax1.text(
                bar.get_x() + bar.get_width() / 2,
                v * 1.03,
                f"{v:,.0f}",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#e0e4f0",
                fontweight="bold",
            )
        ax1.set_xticks(x)
        ax1.set_xticklabels(categories, fontsize=10)
        ax1.set_ylabel("Read IOPS")
        ax1.set_title("Read IOPS")
        ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax1.grid(axis="y")
        ax1.set_axisbelow(True)

        # p99 latency (log)
        bars2 = ax2.bar(
            x, [max(v, 0.01) for v in p99_vals], color=colors, alpha=0.9, width=0.55
        )
        for bar, v in zip(bars2, p99_vals):
            ax2.text(
                bar.get_x() + bar.get_width() / 2,
                max(v, 0.01) * 1.2,
                f"{v:.2f}ms",
                ha="center",
                va="bottom",
                fontsize=9,
                color="#e0e4f0",
                fontweight="bold",
            )
        ax2.set_yscale("log")
        ax2.set_xticks(x)
        ax2.set_xticklabels(categories, fontsize=10)
        ax2.set_ylabel("p99 latency (ms) — log scale")
        ax2.set_title("p99 Latency (lower is better)")
        ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
        ax2.grid(axis="y", which="both")
        ax2.set_axisbelow(True)

        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 5: Zipf RTT timeline (if rtt_timeline.csv exists)
# ---------------------------------------------------------------------------


def plot_rtt_timeline(rtt_csv: Path, out: Path) -> None:
    """Plot the live NFS RTT observations recorded during benchmarking."""
    import csv

    rows = []
    with rtt_csv.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    if not rows:
        return

    phases = [r["phase"] for r in rows]
    times = [float(r["elapsed_s"]) for r in rows]
    read_rtt = [float(r["read_rtt_ms"]) for r in rows]
    write_rtt = [float(r.get("write_rtt_ms", 0) or 0) for r in rows]

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(13, 5))

        ax.plot(
            times,
            read_rtt,
            color=READ_COLOR,
            linewidth=2,
            marker="o",
            markersize=5,
            label="Cumul. READ RTT (ms)",
        )
        if any(w > 0 for w in write_rtt):
            ax.plot(
                times,
                write_rtt,
                color=WRITE_COLOR,
                linewidth=2,
                linestyle="--",
                marker="s",
                markersize=4,
                label="Cumul. WRITE RTT (ms)",
            )

        # Phase boundaries
        phase_changes = [0]
        prev = phases[0]
        for i, p in enumerate(phases[1:], 1):
            if p != prev:
                phase_changes.append(i)
                prev = p
        phase_changes.append(len(phases))
        for start, end in zip(phase_changes[:-1], phase_changes[1:]):
            mid = (times[start] + times[min(end - 1, len(times) - 1)]) / 2
            ax.axvspan(
                times[start],
                times[min(end - 1, len(times) - 1)],
                alpha=0.08,
                color=PALETTE[phase_changes.index(start) % len(PALETTE)],
            )
            ax.text(
                mid,
                ax.get_ylim()[1] * 0.95,
                phases[start],
                ha="center",
                va="top",
                fontsize=8,
                color="#8890a0",
            )

        ax.set_xlabel("Elapsed time (s)")
        ax.set_ylabel("NFS RTT (ms) — cumulative average")
        ax.set_title(
            "Live NFS RTT During Benchmark (mountstats, 30s intervals)", fontsize=13
        )
        ax.legend()
        ax.grid()
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Plot 6: Multi-run comparison (CIFS vs NFS)
# ---------------------------------------------------------------------------


def plot_comparison(
    runs: dict[str, dict[str, dict]], metric: str, out: Path, ylabel: str, title: str
) -> None:
    """Compare a single metric across multiple labeled runs."""
    # Collect all test names present in any run
    all_tests: list[str] = []
    for t in TEST_ORDER:
        if any(t in r for r in runs.values()):
            all_tests.append(t)

    labels = [_label(t) for t in all_tests]
    run_names = list(runs.keys())
    x = np.arange(len(all_tests))
    width = 0.8 / len(run_names)

    log_scale = "p99" in metric or "lat" in metric

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(13, 5))

        for i, (run_name, results) in enumerate(runs.items()):
            vals = [
                max(results.get(t, {}).get(metric, 0), 0.001 if log_scale else 0)
                for t in all_tests
            ]
            offset = (i - (len(run_names) - 1) / 2) * width
            bars = ax.bar(
                x + offset,
                vals,
                width * 0.92,
                label=run_name,
                color=PALETTE[i % len(PALETTE)],
                alpha=0.9,
            )
            for bar, v in zip(bars, vals):
                real_v = v if not log_scale else v
                if real_v > 0.001:
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        v * (1.15 if log_scale else 1.02),
                        f"{real_v:.1f}" if real_v < 100 else f"{real_v:,.0f}",
                        ha="center",
                        va="bottom",
                        fontsize=7.5,
                        color="#c0c4d0",
                    )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=9)
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontsize=13)
        ax.legend()
        if log_scale:
            ax.set_yscale("log")
            ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
            ax.grid(axis="y", which="both")
        else:
            ax.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda v, _: f"{v:,.0f}")
            )
            ax.grid(axis="y")
        ax.set_axisbelow(True)
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# RTT timeline data (hardcoded from run 3 live observations)
# ---------------------------------------------------------------------------

RTT_TIMELINE = [
    # phase, elapsed_s, read_rtt_ms, write_rtt_ms
    ("Zipf pass 1", 0, 0.83, 5.89),
    ("Zipf pass 1", 30, 0.80, 5.89),
    ("Zipf pass 1", 60, 0.77, 5.89),
    ("Zipf pass 1", 90, 0.75, 5.89),
    ("Zipf pass 1", 120, 0.73, 5.89),
    ("Zipf pass 1", 150, 0.72, 5.89),
    ("Zipf pass 1", 180, 0.71, 5.89),
    ("Zipf pass 2", 210, 0.72, 5.89),
    ("Zipf pass 2", 240, 0.70, 5.89),
    ("Zipf pass 2", 270, 0.68, 5.89),
    ("Zipf pass 2", 300, 0.67, 5.89),
    ("Zipf pass 2", 330, 0.65, 5.89),
    ("Zipf pass 2", 360, 0.64, 5.89),
    ("randrw", 390, 0.638, 5.87),
    ("randrw", 420, 0.637, 5.81),
    ("randrw", 450, 0.635, 5.24),
    ("randrw", 480, 0.637, 5.37),
    ("randrw", 510, 0.642, 5.35),
    ("randrw", 540, 0.646, 5.32),
    ("randrw", 570, 0.647, 5.31),
]


def plot_rtt_from_data(out: Path) -> None:
    """Plot RTT timeline from hardcoded run 3 observations."""
    phases = [r[0] for r in RTT_TIMELINE]
    times = [r[1] for r in RTT_TIMELINE]
    read_rtt = [r[2] for r in RTT_TIMELINE]
    write_rtt = [r[3] for r in RTT_TIMELINE]

    phase_colors = {
        "Zipf pass 1": "#4da6ff",
        "Zipf pass 2": ACCENT,
        "randrw": WRITE_COLOR,
    }

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(14, 5))

        # Shaded phase regions
        phase_starts = {}
        for i, (p, t) in enumerate(zip(phases, times)):
            if p not in phase_starts:
                phase_starts[p] = t
        phase_ends = {}
        for p in phase_starts:
            idxs = [i for i, ph in enumerate(phases) if ph == p]
            phase_ends[p] = times[idxs[-1]]

        for phase, start in phase_starts.items():
            end = phase_ends[phase]
            ax.axvspan(
                start,
                end,
                alpha=0.10,
                color=phase_colors.get(phase, "#888"),
                label=f"_{phase}",
            )
            mid = (start + end) / 2
            ax.text(
                mid,
                0.57,
                phase,
                ha="center",
                va="bottom",
                fontsize=9,
                color=phase_colors.get(phase, "#888"),
                fontweight="bold",
            )

        ax.plot(
            times,
            read_rtt,
            color=READ_COLOR,
            linewidth=2.5,
            marker="o",
            markersize=6,
            label="Read RTT (cumul. avg)",
        )
        ax.plot(
            times,
            write_rtt,
            color=WRITE_COLOR,
            linewidth=2,
            linestyle="--",
            marker="s",
            markersize=5,
            label="Write RTT (cumul. avg)",
        )

        # Annotate key points
        ax.annotate(
            "Cache\nwarming",
            xy=(90, 0.75),
            xytext=(50, 1.2),
            arrowprops=dict(arrowstyle="->", color="#8890a0"),
            fontsize=8,
            color="#8890a0",
            ha="center",
        )
        ax.annotate(
            "NVMe floor\n~0.30ms actual",
            xy=(360, 0.64),
            xytext=(320, 0.45),
            arrowprops=dict(arrowstyle="->", color=ACCENT),
            fontsize=8,
            color=ACCENT,
            ha="center",
        )
        ax.annotate(
            "Write buffer\nat full speed",
            xy=(450, 5.24),
            xytext=(490, 4.5),
            arrowprops=dict(arrowstyle="->", color="#8890a0"),
            fontsize=8,
            color="#8890a0",
            ha="center",
        )
        ax.annotate(
            "+12µs\nin 3 min",
            xy=(570, 0.647),
            xytext=(540, 0.80),
            arrowprops=dict(arrowstyle="->", color="#66bb6a"),
            fontsize=8,
            color="#66bb6a",
            ha="center",
        )

        ax.set_xlabel("Elapsed time (s)")
        ax.set_ylabel("NFS RTT ms (cumulative mountstats average)")
        ax.set_title(
            "Live NFS RTT — Run 3: Zipf warming → hot → randrw write pressure",
            fontsize=12,
        )
        ax.legend(loc="upper right")
        ax.grid(alpha=0.4)
        ax.set_axisbelow(True)
        ax.set_ylim(0.3, 6.5)
        fig.tight_layout()
        fig.savefig(out, dpi=150)
        plt.close(fig)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def generate_plots(
    results_dirs: list[Path],
    comparison_label: str | None = None,
    out_dir: Path | None = None,
) -> None:
    from rich.console import Console

    console = Console()

    if len(results_dirs) == 1:
        d = results_dirs[0]
        label = d.name
        results = load_results(d)
        if not results:
            console.print(f"[red]No results in {d}[/]")
            return

        dest = out_dir or d
        dest.mkdir(parents=True, exist_ok=True)

        plot_iops(results, dest / "plot_iops.png", label)
        console.print(f"  [dim]→ {dest}/plot_iops.png[/]")

        plot_latency(results, dest / "plot_latency_p99.png", label)
        console.print(f"  [dim]→ {dest}/plot_latency_p99.png[/]")

        plot_bandwidth(results, dest / "plot_bandwidth.png", label)
        console.print(f"  [dim]→ {dest}/plot_bandwidth.png[/]")

        plot_cache_lift(results, dest / "plot_cache_lift.png", label)
        console.print(f"  [dim]→ {dest}/plot_cache_lift.png[/]")

        plot_rtt_from_data(dest / "plot_rtt_timeline.png")
        console.print(f"  [dim]→ {dest}/plot_rtt_timeline.png[/]")

        console.print(f"\n[green]5 plots saved to {dest}[/]")

    else:
        # Multi-run comparison
        all_results = {d.name: load_results(d) for d in results_dirs}
        dest = out_dir or results_dirs[0].parent
        dest.mkdir(parents=True, exist_ok=True)
        slug = comparison_label or "comparison"

        plot_comparison(
            all_results,
            "read_iops",
            dest / f"plot_{slug}_read_iops.png",
            "Read IOPS",
            f"{slug} — Read IOPS",
        )
        console.print(f"  [dim]→ {dest}/plot_{slug}_read_iops.png[/]")

        plot_comparison(
            all_results,
            "read_p99_ms",
            dest / f"plot_{slug}_read_p99.png",
            "Read p99 ms (log)",
            f"{slug} — Read p99 Latency",
        )
        console.print(f"  [dim]→ {dest}/plot_{slug}_read_p99.png[/]")

        plot_comparison(
            all_results,
            "read_bw_mb",
            dest / f"plot_{slug}_read_bw.png",
            "Read MB/s",
            f"{slug} — Read Bandwidth",
        )
        console.print(f"  [dim]→ {dest}/plot_{slug}_read_bw.png[/]")

        console.print(f"\n[green]3 comparison plots saved to {dest}[/]")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage:")
        print("  uv run plot_results.py <results-dir>")
        print(
            "  uv run plot_results.py <results-dir-1> <results-dir-2> [--label cifs-vs-nfs]"
        )
        sys.exit(1)

    dirs: list[Path] = []
    label: str | None = None
    out: Path | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--label" and i + 1 < len(args):
            label = args[i + 1]
            i += 2
        elif args[i] == "--out-dir" and i + 1 < len(args):
            out = Path(args[i + 1])
            i += 2
        else:
            d = Path(args[i])
            if not d.is_dir():
                print(f"Not a directory: {d}")
                sys.exit(1)
            dirs.append(d)
            i += 1

    generate_plots(dirs, label, out)


if __name__ == "__main__":
    main()
