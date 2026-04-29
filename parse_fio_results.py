#!/usr/bin/env python3
"""Parse fio JSON results and print a summary table."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from tabulate import tabulate

console = Console(width=160)

# Canonical display order — matches run_nas_bench.py test sequence
TEST_ORDER = [
    "seqwrite-control",
    "seqread-control",
    "randread-uniform-1",
    "randread-uniform-2",
    "zipf-randread-1",
    "zipf-randread-2",
    "randrw-zipf",
    # Legacy names from earlier runs (kept for backwards compat)
    "randread-cold",
    "randread-prewarm",
    "randread-hot",
    "zipf-randread-2-hot",
    "randrw-cache",
]

DISPLAY_NAMES = {
    "seqwrite-control": "Seq write — HDD baseline",
    "seqread-control": "Seq read  — HDD baseline",
    "randread-uniform-1": "Uniform rand read — pass 1 (first-touch)",
    "randread-uniform-2": "Uniform rand read — pass 2 (demand-cached)",
    "zipf-randread-1": "Zipf rand read — pass 1 (warming)",
    "zipf-randread-2": "Zipf rand read — pass 2 (cached) ★",
    "randrw-zipf": "Mixed randrw Zipf 70/30",
    # Legacy
    "randread-cold": "Rand read — cold (legacy)",
    "randread-prewarm": "Rand read — pre-warmed (legacy)",
    "randread-hot": "Rand read — hot (legacy)",
    "zipf-randread-2-hot": "Zipf read — pass 2 hot (legacy)",
    "randrw-cache": "Mixed randrw (legacy)",
}


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


def _ns_to_ms(ns: float) -> float:
    return ns / 1_000_000.0


def _kb_to_mb(kb: float) -> float:
    return kb / 1024.0


def _percentile(clat: dict, pct: str) -> float:
    """Extract a clat percentile from fio's nested percentile dict."""
    pcts = clat.get("percentile", {})
    for k, v in pcts.items():
        if abs(float(k) - float(pct)) < 0.01:
            return _ns_to_ms(v)
    return 0.0


def extract_metrics(fio_json: dict) -> dict:
    """Return a flat metrics dict from a parsed fio JSON result."""
    m: dict = {
        "read_bw_mb": 0.0,
        "write_bw_mb": 0.0,
        "read_iops": 0.0,
        "write_iops": 0.0,
        "read_lat_ms": 0.0,
        "write_lat_ms": 0.0,
        "read_p95_ms": 0.0,
        "read_p99_ms": 0.0,
        "write_p95_ms": 0.0,
        "write_p99_ms": 0.0,
    }
    if not fio_json or "jobs" not in fio_json:
        return m

    job = fio_json["jobs"][0]  # group_reporting → single aggregated job

    for rw in ("read", "write"):
        d = job.get(rw, {})
        if not d:
            continue
        m[f"{rw}_bw_mb"] = _kb_to_mb(d.get("bw", 0))
        m[f"{rw}_iops"] = float(d.get("iops", 0))
        m[f"{rw}_lat_ms"] = _ns_to_ms(d.get("lat_ns", {}).get("mean", 0))
        clat = d.get("clat_ns", {})
        m[f"{rw}_p95_ms"] = _percentile(clat, "95.000000")
        m[f"{rw}_p99_ms"] = _percentile(clat, "99.000000")

    return m


def load_results(results_dir: Path) -> dict[str, dict]:
    """Load all fio JSON files from a results directory."""
    data: dict[str, dict] = {}
    for jf in sorted(results_dir.glob("*.json")):
        name = jf.stem
        if name in ("prepare", "prewarm"):
            continue
        try:
            parsed = json.loads(jf.read_text())
            data[name] = extract_metrics(parsed)
        except Exception as e:
            console.print(f"[yellow]Warning:[/] could not parse {jf.name}: {e}")
    return data


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_table(results: dict[str, dict]) -> Table:
    t = Table(
        title="fio Benchmark Results",
        show_header=True,
        header_style="bold cyan",
        border_style="blue",
        row_styles=["", "dim"],
    )
    t.add_column("Test", style="bold", no_wrap=True)
    t.add_column("R BW\nMB/s", justify="right")
    t.add_column("W BW\nMB/s", justify="right")
    t.add_column("R IOPS", justify="right")
    t.add_column("W IOPS", justify="right")
    t.add_column("R lat\nms", justify="right")
    t.add_column("W lat\nms", justify="right")
    t.add_column("R p95\nms", justify="right")
    t.add_column("R p99\nms", justify="right")
    t.add_column("W p95\nms", justify="right")
    t.add_column("W p99\nms", justify="right")

    ordered = [k for k in TEST_ORDER if k in results]
    ordered += [k for k in results if k not in TEST_ORDER]

    def fmt(v: float, decimals: int = 1) -> str:
        return f"{v:.{decimals}f}" if v > 0 else "—"

    for name in ordered:
        m = results[name]
        t.add_row(
            DISPLAY_NAMES.get(name, name),
            fmt(m["read_bw_mb"]),
            fmt(m["write_bw_mb"]),
            fmt(m["read_iops"], 0),
            fmt(m["write_iops"], 0),
            fmt(m["read_lat_ms"], 2),
            fmt(m["write_lat_ms"], 2),
            fmt(m["read_p95_ms"], 2),
            fmt(m["read_p99_ms"], 2),
            fmt(m["write_p95_ms"], 2),
            fmt(m["write_p99_ms"], 2),
        )

    return t


def render_comparisons(results: dict[str, dict]) -> list[str]:
    lines: list[str] = []

    def ratio(a: float, b: float) -> str:
        return "N/A" if b == 0 else f"{a / b:.2f}x"

    def improve(a: float, b: float) -> str:
        """Latency improvement b→a (lower is better)."""
        if b == 0:
            return "N/A"
        pct = (b - a) / b * 100
        return f"{'+' if pct > 0 else ''}{pct:.1f}%"

    # Sequential summary (HDD baseline, cache bypassed)
    sw = results.get("seqwrite-control", {})
    sr = results.get("seqread-control", {})
    if sw or sr:
        lines.append(
            "Sequential throughput — HDD+network baseline (NVMe cache bypassed):"
        )
        if sw:
            lines.append(
                f"  Write: {sw['write_bw_mb']:.1f} MB/s  p99={sw['write_p99_ms']:.2f}ms"
            )
        if sr:
            lines.append(
                f"  Read:  {sr['read_bw_mb']:.1f} MB/s  p99={sr['read_p99_ms']:.2f}ms"
            )
    lines.append("")

    # Uniform random: pass1 → pass2 (demand-caching effect)
    u1 = results.get("randread-uniform-1") or results.get("randread-cold") or {}
    u2 = results.get("randread-uniform-2") or results.get("randread-hot") or {}
    if u1 and u2:
        lines.append("Uniform random read — demand-caching effect (pass1 → pass2):")
        lines.append(
            f"  IOPS ratio (pass2/pass1):   {ratio(u2['read_iops'], u1['read_iops'])}"
        )
        lines.append(
            f"  p99 improvement:            {improve(u2['read_p99_ms'], u1['read_p99_ms'])}"
        )
        lines.append(
            f"  pass1 IOPS={u1['read_iops']:.0f}  pass2 IOPS={u2['read_iops']:.0f}"
        )
        lines.append(
            f"  pass1 p99={u1['read_p99_ms']:.2f}ms  pass2 p99={u2['read_p99_ms']:.2f}ms"
        )
    lines.append("")

    # Zipf: pass1 → pass2 (DEFINITIVE cache measurement)
    z1 = results.get("zipf-randread-1", {})
    z2 = results.get("zipf-randread-2") or results.get("zipf-randread-2-hot") or {}
    if z1 and z2:
        lines.append(
            "Zipf hot-set — cache warming effect (pass1 → pass2)  ★ DEFINITIVE:"
        )
        lines.append(
            f"  IOPS ratio (pass2/pass1):   {ratio(z2['read_iops'], z1['read_iops'])}"
        )
        lines.append(
            f"  p99 improvement:            {improve(z2['read_p99_ms'], z1['read_p99_ms'])}"
        )
        lines.append(
            f"  pass1 IOPS={z1['read_iops']:.0f}  pass2 IOPS={z2['read_iops']:.0f}"
        )
        lines.append(
            f"  pass1 p99={z1['read_p99_ms']:.2f}ms  pass2 p99={z2['read_p99_ms']:.2f}ms"
        )

        # vs HDD baseline (if available)
        if sr:
            hdd_iops = sr["read_iops"]
            lines.append(
                f"  vs HDD baseline: {ratio(z2['read_iops'], hdd_iops)} IOPS lift  "
                f"({hdd_iops:.0f} → {z2['read_iops']:.0f} IOPS)"
            )
    lines.append("")

    # Mixed randrw
    rw = results.get("randrw-zipf") or results.get("randrw-cache") or {}
    if rw:
        lines.append("Mixed Zipf randrw 70/30 — write pressure on cached reads:")
        lines.append(
            f"  Read  IOPS={rw['read_iops']:.0f}  BW={rw['read_bw_mb']:.1f} MB/s  p99={rw['read_p99_ms']:.2f}ms"
        )
        lines.append(
            f"  Write IOPS={rw['write_iops']:.0f}  BW={rw['write_bw_mb']:.1f} MB/s  p99={rw['write_p99_ms']:.2f}ms"
        )

    return lines


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "test",
    "read_bw_mb",
    "write_bw_mb",
    "read_iops",
    "write_iops",
    "read_lat_ms",
    "write_lat_ms",
    "read_p95_ms",
    "read_p99_ms",
    "write_p95_ms",
    "write_p99_ms",
]


def save_csv(results: dict[str, dict], out: Path) -> None:
    ordered = [k for k in TEST_ORDER if k in results]
    ordered += [k for k in results if k not in TEST_ORDER]
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for name in ordered:
            writer.writerow({"test": name, **results[name]})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def parse_and_print(results_dir: Path) -> None:
    results = load_results(results_dir)
    if not results:
        console.print("[yellow]No results found in directory.[/]")
        return

    console.print(render_table(results))
    console.print()

    comparisons = render_comparisons(results)
    if any(comparisons):
        console.print(
            Panel(
                "\n".join(comparisons),
                title="Key Comparisons",
                border_style="green",
            )
        )

    summary_txt = results_dir / "summary.txt"
    with summary_txt.open("w") as f:
        headers = [
            "Test",
            "R BW MB/s",
            "W BW MB/s",
            "R IOPS",
            "W IOPS",
            "R lat ms",
            "W lat ms",
            "R p95 ms",
            "R p99 ms",
            "W p95 ms",
            "W p99 ms",
        ]
        ordered = [k for k in TEST_ORDER if k in results]
        ordered += [k for k in results if k not in TEST_ORDER]
        rows = []
        for name in ordered:
            m = results[name]
            rows.append(
                [
                    DISPLAY_NAMES.get(name, name),
                    f"{m['read_bw_mb']:.1f}",
                    f"{m['write_bw_mb']:.1f}",
                    f"{m['read_iops']:.0f}",
                    f"{m['write_iops']:.0f}",
                    f"{m['read_lat_ms']:.2f}",
                    f"{m['write_lat_ms']:.2f}",
                    f"{m['read_p95_ms']:.2f}",
                    f"{m['read_p99_ms']:.2f}",
                    f"{m['write_p95_ms']:.2f}",
                    f"{m['write_p99_ms']:.2f}",
                ]
            )
        f.write(tabulate(rows, headers=headers, tablefmt="github"))
        f.write("\n\n")
        f.write("\n".join(comparisons))
        f.write("\n")

    save_csv(results, results_dir / "summary.csv")
    console.print(
        f"[dim]Summary saved: {summary_txt}  {results_dir / 'summary.csv'}[/]"
    )


def main() -> None:
    if len(sys.argv) < 2:
        console.print("Usage: uv run parse_fio_results.py <results-dir>")
        sys.exit(1)

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        console.print(f"[red]Not a directory:[/] {results_dir}")
        sys.exit(1)

    parse_and_print(results_dir)


if __name__ == "__main__":
    main()
