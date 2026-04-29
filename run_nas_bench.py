#!/usr/bin/env python3
"""NAS benchmark suite for NFS and CIFS mounts — fio-based, cache-aware."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

console = Console()

CIFS_FSTYPES = {"cifs", "smb", "smb2", "smb3", "smbfs"}
NFS_FSTYPES = {"nfs", "nfs4", "nfs3"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="NAS benchmark suite for NFS and CIFS mounts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # NFS run (definitive)
              uv run run_nas_bench.py --label nfs

              # CIFS run for comparison (mount CIFS first, then:)
              uv run run_nas_bench.py --mount ~/nas-cifs --label cifs

              # Quick validation
              uv run run_nas_bench.py --quick --label nfs-quick

              # Larger file / longer runtime
              uv run run_nas_bench.py --size 256G --runtime 180 --label nfs-256g

            For a definitive CIFS vs NFS comparison:
              1. Mount NFS at ~/nas-nfs, CIFS at ~/nas-cifs
              2. Run with --label nfs and --label cifs respectively
              3. Compare results/TIMESTAMP-nfs vs results/TIMESTAMP-cifs

            True cold baseline (cache fully flushed):
              Reboot the NAS, then run immediately. Alternatively use
              --size larger than the NVMe cache (>2T for UNAS Pro 4).
        """),
    )
    p.add_argument("--mount", default="~/nas", help="NAS mount point (default: ~/nas)")
    p.add_argument(
        "--bench-dir", default=None, help="Benchmark directory (default: <mount>/bench)"
    )
    p.add_argument("--size", default="128G", help="Test file size (default: 128G)")
    p.add_argument(
        "--runtime",
        type=int,
        default=120,
        help="Runtime for sequential/uniform tests in seconds (default: 120)",
    )
    p.add_argument(
        "--zipf-runtime",
        type=int,
        default=None,
        help="Runtime for Zipf tests (default: runtime+60)",
    )
    p.add_argument(
        "--jobs", type=int, default=4, help="Number of fio jobs (default: 4)"
    )
    p.add_argument(
        "--iodepth", type=int, default=32, help="IO queue depth (default: 32)"
    )
    p.add_argument(
        "--label",
        default=None,
        help="Tag appended to results directory name, e.g. 'nfs' or 'cifs' (default: fstype detected from mount)",
    )
    p.add_argument(
        "--skip-prepare", action="store_true", help="Skip test file preparation"
    )
    p.add_argument(
        "--quick",
        action="store_true",
        help="Short runs (30s / 60s) for quick validation",
    )
    p.add_argument(
        "--cleanup", action="store_true", help="Remove benchmark files after run"
    )
    p.add_argument(
        "--results-dir", default=None, help="Override results output directory entirely"
    )
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def check_fio() -> None:
    if not shutil.which("fio"):
        console.print(
            "[bold red]Error:[/] fio is not installed.\n"
            "  Install with:  sudo apt update && sudo apt install -y fio"
        )
        sys.exit(1)


def check_mount(bench_dir: Path) -> dict:
    """Locate the mount containing bench_dir and return its info."""
    result = subprocess.run(
        [
            "findmnt",
            "--target",
            str(bench_dir),
            "--output",
            "SOURCE,FSTYPE,TARGET,OPTIONS",
            "--noheadings",
            "--first-only",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        for parent in bench_dir.parents:
            result = subprocess.run(
                [
                    "findmnt",
                    "--target",
                    str(parent),
                    "--output",
                    "SOURCE,FSTYPE,TARGET,OPTIONS",
                    "--noheadings",
                    "--first-only",
                ],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                break

    info = result.stdout.strip()
    if not info:
        console.print(
            "[bold red]Error:[/] Could not find mount info for bench directory."
        )
        console.print("  Make sure the NAS is mounted and the path is correct.")
        sys.exit(1)

    parts = info.split()
    source = parts[0] if parts else "unknown"
    fstype = parts[1] if len(parts) > 1 else "unknown"
    fstype_lower = fstype.lower()

    if fstype_lower not in CIFS_FSTYPES | NFS_FSTYPES:
        console.print(
            f"[bold yellow]Warning:[/] Unexpected filesystem type [bold]{fstype}[/] "
            f"— expected nfs or cifs. Continuing anyway."
        )
        console.print()

    return {"source": source, "fstype": fstype, "raw": info}


def collect_system_info(mount_path: Path) -> str:
    lines: list[str] = []

    def run(cmd: list[str]) -> str:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return r.stdout.strip()
        except Exception:
            return "(unavailable)"

    lines.append(f"date:      {datetime.now().isoformat()}")
    lines.append(f"hostname:  {run(['hostname'])}")
    lines.append(f"uname:     {run(['uname', '-a'])}")
    lines.append("")
    lines.append("=== lsblk ===")
    lines.append(run(["lsblk"]))
    lines.append("")
    lines.append(f"=== df -h {mount_path} ===")
    lines.append(run(["df", "-h", str(mount_path)]))
    lines.append("")
    lines.append(f"=== findmnt {mount_path} ===")
    lines.append(run(["findmnt", str(mount_path)]))
    lines.append("")
    lines.append("=== ip addr ===")
    lines.append(run(["ip", "-brief", "addr"]))

    try:
        iproute = subprocess.run(
            ["ip", "route", "get", "8.8.8.8"], capture_output=True, text=True, timeout=5
        )
        iface = None
        tokens = iproute.stdout.split()
        for i, tok in enumerate(tokens):
            if tok == "dev" and i + 1 < len(tokens):
                iface = tokens[i + 1]
                break
        if iface:
            lines.append("")
            lines.append(f"=== ethtool {iface} ===")
            lines.append(run(["ethtool", iface]))
    except Exception:
        pass

    return "\n".join(lines)


def half_size(size_str: str) -> str:
    """Return half of a size string, keeping the same unit."""
    s = size_str.upper().strip()
    unit = s[-1] if s[-1] in "GMTK" else ""
    val = int(s[:-1]) if unit else int(s)
    return f"{max(1, val // 2)}{unit}"


# ---------------------------------------------------------------------------
# fio runner
# ---------------------------------------------------------------------------


def run_fio(name: str, fio_args: list[str], out_file: Path) -> dict:
    """Run fio with JSON output, save to out_file, return parsed JSON."""
    cmd = ["fio"] + fio_args + [f"--output={out_file}", "--output-format=json"]
    console.print(f"  [cyan]→[/] Running [bold]{name}[/] ...")

    with Progress(
        SpinnerColumn(),
        TextColumn("    {task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(name, total=None)
        proc = subprocess.run(cmd, capture_output=True, text=True)
        progress.update(task, completed=1)

    if proc.returncode != 0:
        console.print(f"  [bold red]fio failed for {name}[/]")
        console.print(proc.stderr[-2000:] if proc.stderr else "(no stderr)")
        return {}

    try:
        return json.loads(out_file.read_text())
    except Exception:
        console.print(f"  [yellow]Warning:[/] Could not parse JSON output for {name}")
        return {}


# ---------------------------------------------------------------------------
# Quick inline result after each test
# ---------------------------------------------------------------------------


def _iops_bw(job: dict, rw: str) -> tuple[float, float, float]:
    d = job.get(rw, {})
    if not d:
        return 0.0, 0.0, 0.0
    return (
        float(d.get("iops", 0)),
        d.get("bw", 0) / 1024,  # KB/s → MB/s
        d.get("lat_ns", {}).get("mean", 0) / 1e6,  # ns → ms
    )


def print_quick_result(fio_data: dict) -> None:
    if not fio_data or "jobs" not in fio_data:
        return
    job = fio_data["jobs"][0]
    riops, rbw, rlat = _iops_bw(job, "read")
    wiops, wbw, wlat = _iops_bw(job, "write")

    t = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 1))
    t.add_column("Dir")
    t.add_column("BW MB/s", justify="right")
    t.add_column("IOPS", justify="right")
    t.add_column("Lat ms", justify="right")

    if rbw > 0 or riops > 0:
        t.add_row("read", f"{rbw:>8.1f}", f"{riops:>8.0f}", f"{rlat:>6.2f}")
    if wbw > 0 or wiops > 0:
        t.add_row("write", f"{wbw:>8.1f}", f"{wiops:>8.0f}", f"{wlat:>6.2f}")

    console.print(t)
    console.print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    mount_path = expand(args.mount)
    bench_dir = expand(args.bench_dir) if args.bench_dir else mount_path / "bench"
    test_file = bench_dir / "cache-test.bin"
    rw_file = bench_dir / "cache-randrw.bin"
    size = args.size
    mixed_size = half_size(size)

    runtime = 30 if args.quick else args.runtime
    zipf_runtime = 60 if args.quick else (args.zipf_runtime or args.runtime + 60)
    jobs = args.jobs
    iodepth = args.iodepth

    # Preflight
    check_fio()
    bench_dir.mkdir(parents=True, exist_ok=True)
    mount_info = check_mount(bench_dir)
    fstype = mount_info["fstype"].lower()
    label = args.label or fstype

    # Results directory: timestamp + label for easy CIFS/NFS comparison
    if args.results_dir:
        results_root = Path(args.results_dir)
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        results_root = Path("results") / f"{stamp}-{label}"
    results_root.mkdir(parents=True, exist_ok=True)

    console.print(
        Panel(
            f"[bold]NAS Benchmark Suite[/]\n"
            f"mount:     {mount_path}  ([bold]{mount_info['fstype']}[/])\n"
            f"source:    {mount_info['source']}\n"
            f"bench dir: {bench_dir}\n"
            f"size:      {size}  (randrw: {mixed_size})\n"
            f"runtime:   {runtime}s  (Zipf: {zipf_runtime}s)\n"
            f"jobs:      {jobs}  iodepth: {iodepth}\n"
            f"label:     {label}\n"
            f"results:   {results_root}",
            title="bench-nas",
            border_style="blue",
        )
    )
    console.print()

    # Warn about cold-baseline reliability
    console.print(
        "[dim]Note: tests C/D measure demand-caching (first-touch vs warm), not a "
        "guaranteed cold baseline.\n"
        "      For a true cold start, reboot the NAS before running, or use "
        "--size larger than the NVMe cache.\n"
        "      Tests E/F (Zipf two-pass) are self-contained and do not require "
        "an external cache state.[/]\n"
    )

    sysinfo = collect_system_info(mount_path)
    (results_root / "sysinfo.txt").write_text(sysinfo)
    console.print("[dim]System info saved to sysinfo.txt[/]\n")

    # Common fio flag groups
    base_file = [f"--filename={test_file}"]
    base_rw_file = [f"--filename={rw_file}"]
    base_size = [f"--size={size}"]
    rw_size = [f"--size={mixed_size}"]
    base_flags = ["--direct=1", "--group_reporting"]

    def fio_run(name: str, extra: list[str]) -> dict:
        data = run_fio(name, extra, results_root / f"{name}.json")
        print_quick_result(data)
        return data

    # -----------------------------------------------------------------------
    # Prepare test file
    # -----------------------------------------------------------------------
    if not args.skip_prepare:
        console.rule("[bold]Preparing test file[/]")
        fio_run(
            "prepare",
            [
                "--name=prepare",
                *base_file,
                *base_size,
                "--bs=1M",
                "--rw=write",
                "--iodepth=16",
                "--numjobs=1",
                *base_flags,
            ],
        )
    else:
        console.print("[yellow]Skipping file preparation (--skip-prepare)[/]\n")

    # -----------------------------------------------------------------------
    # A. Sequential write — HDD+network baseline
    #    Cache policy: random-only → sequential IO bypasses NVMe cache entirely.
    #    Measures raw HDD RAID throughput over the network stack.
    # -----------------------------------------------------------------------
    console.rule("[bold]A. Sequential write — HDD+network baseline (cache bypassed)[/]")
    fio_run(
        "seqwrite-control",
        [
            "--name=seqwrite-control",
            *base_file,
            *base_size,
            "--bs=1M",
            "--rw=write",
            "--iodepth=16",
            "--numjobs=1",
            f"--runtime={runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # B. Sequential read — HDD+network baseline
    #    Same as A: bypasses NVMe cache. Measures HDD RAID read throughput.
    # -----------------------------------------------------------------------
    console.rule("[bold]B. Sequential read — HDD+network baseline (cache bypassed)[/]")
    fio_run(
        "seqread-control",
        [
            "--name=seqread-control",
            *base_file,
            *base_size,
            "--bs=1M",
            "--rw=read",
            "--iodepth=16",
            "--numjobs=1",
            f"--runtime={runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # C. Uniform random read — pass 1 (first-touch, demand-caching begins)
    #    4K random reads across the full file. Cache misses on first touch
    #    go to HDDs; the NAS progressively caches accessed blocks into NVMe.
    #    NOT a guaranteed cold baseline unless the NAS was just rebooted.
    # -----------------------------------------------------------------------
    console.rule(
        "[bold]C. Uniform random read — pass 1 (first-touch / demand-caching)[/]"
    )
    fio_run(
        "randread-uniform-1",
        [
            "--name=randread-uniform-1",
            *base_file,
            *base_size,
            "--bs=4k",
            "--rw=randread",
            f"--iodepth={iodepth}",
            f"--numjobs={jobs}",
            f"--runtime={runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # D. Uniform random read — pass 2 (demand-cached)
    #    Same workload immediately after pass 1. Blocks accessed in pass 1
    #    are now in NVMe cache; shows demand-caching benefit over pass 1.
    # -----------------------------------------------------------------------
    console.rule("[bold]D. Uniform random read — pass 2 (demand-cached)[/]")
    fio_run(
        "randread-uniform-2",
        [
            "--name=randread-uniform-2",
            *base_file,
            *base_size,
            "--bs=4k",
            "--rw=randread",
            f"--iodepth={iodepth}",
            f"--numjobs={jobs}",
            f"--runtime={runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # E. Zipf random read — pass 1 (hot-set warming)
    #    Zipf θ=1.2 access pattern: ~20% of blocks receive ~80% of accesses.
    #    Pass 1 warms the NVMe cache with the hot subset. Longer runtime
    #    ensures the hot set is fully resident before pass 2.
    #    This is the DEFINITIVE cache measurement for real-world workloads.
    # -----------------------------------------------------------------------
    console.rule("[bold]E. Zipf random read — pass 1 (hot-set warming, θ=1.2)[/]")
    fio_run(
        "zipf-randread-1",
        [
            "--name=zipf-randread-1",
            *base_file,
            *base_size,
            "--bs=4k",
            "--rw=randread",
            "--random_distribution=zipf:1.2",
            f"--iodepth={iodepth}",
            f"--numjobs={jobs}",
            f"--runtime={zipf_runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # F. Zipf random read — pass 2 (hot-set fully cached)
    #    Same workload as E. Hot set is now resident in NVMe cache.
    #    p99 latency and IOPS here are the DEFINITIVE NVMe cache numbers.
    #    Difference vs pass 1 = cache warming effect.
    # -----------------------------------------------------------------------
    console.rule("[bold]F. Zipf random read — pass 2 (hot-set cached, DEFINITIVE)[/]")
    fio_run(
        "zipf-randread-2",
        [
            "--name=zipf-randread-2",
            *base_file,
            *base_size,
            "--bs=4k",
            "--rw=randread",
            "--random_distribution=zipf:1.2",
            f"--iodepth={iodepth}",
            f"--numjobs={jobs}",
            f"--runtime={zipf_runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # G. Mixed Zipf randrw 70/30 — write pressure on cached hot-set
    #    Uses a SEPARATE file (cache-randrw.bin) to avoid destroying the
    #    read test's block pattern in cache-test.bin.
    #    Shows how write-back cache handles concurrent read/write workloads
    #    and whether writes evict cached read blocks.
    # -----------------------------------------------------------------------
    console.rule("[bold]G. Mixed Zipf randrw 70/30 — write pressure on cached reads[/]")
    fio_run(
        "randrw-zipf",
        [
            "--name=randrw-zipf",
            *base_rw_file,
            *rw_size,
            "--bs=4k",
            "--rw=randrw",
            "--rwmixread=70",
            "--random_distribution=zipf:1.2",
            f"--iodepth={iodepth}",
            f"--numjobs={jobs}",
            f"--runtime={zipf_runtime}",
            "--time_based",
            *base_flags,
        ],
    )

    # -----------------------------------------------------------------------
    # Parse and summarize
    # -----------------------------------------------------------------------
    console.rule("[bold]Results[/]")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "parse_results", Path(__file__).parent / "parse_fio_results.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.parse_and_print(results_root)

    if args.cleanup:
        console.print()
        console.print("[yellow]--cleanup: removing benchmark files...[/]")
        for f in [test_file, rw_file]:
            if f.exists():
                f.unlink()
                console.print(f"  removed {f}")

    console.print()
    console.print(f"[bold green]Done.[/] Results saved to [bold]{results_root}[/]")


if __name__ == "__main__":
    main()
