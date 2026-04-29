#!/usr/bin/env python3
"""NAS benchmark runner — fio-based suite for CIFS/SMB mounts."""

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="NAS fio benchmark suite for CIFS/SMB mounts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              uv run run_nas_bench.py
              uv run run_nas_bench.py --mount ~/nas --size 64G --quick
              uv run run_nas_bench.py --size 256G --runtime 180 --jobs 8
              uv run run_nas_bench.py --cleanup
        """),
    )
    p.add_argument("--mount",     default="~/nas",            help="NAS mount point (default: ~/nas)")
    p.add_argument("--bench-dir", default=None,               help="Benchmark directory (default: <mount>/bench)")
    p.add_argument("--size",      default="128G",             help="Test file size (default: 128G)")
    p.add_argument("--runtime",   type=int, default=120,      help="Runtime for standard tests in seconds (default: 120)")
    p.add_argument("--zipf-runtime", type=int, default=None,  help="Runtime for Zipf/hot-set tests (default: runtime+60)")
    p.add_argument("--jobs",      type=int, default=4,        help="Number of fio jobs (default: 4)")
    p.add_argument("--iodepth",   type=int, default=32,       help="IO queue depth (default: 32)")
    p.add_argument("--skip-prepare", action="store_true",     help="Skip test file preparation")
    p.add_argument("--pre-warm",  action="store_true",        help="Sequential read pass before random tests to fully populate NVMe cache")
    p.add_argument("--quick",     action="store_true",        help="Short runs (30s / 60s) for quick validation")
    p.add_argument("--cleanup",   action="store_true",        help="Remove benchmark files after run")
    p.add_argument("--results-dir", default=None,             help="Override results output directory")
    return p


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def expand(path: str) -> Path:
    return Path(path).expanduser().resolve()


def check_fio() -> None:
    if not shutil.which("fio"):
        console.print("[bold red]Error:[/] fio is not installed.\n"
                      "  Install with:  sudo apt update && sudo apt install -y fio")
        sys.exit(1)


def check_mount(bench_dir: Path) -> dict:
    """Verify bench_dir is on a CIFS/SMB mount. Return findmnt info."""
    result = subprocess.run(
        ["findmnt", "--target", str(bench_dir), "--output", "SOURCE,FSTYPE,TARGET,OPTIONS",
         "--noheadings", "--first-only"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        # Walk up to find an ancestor mount
        for parent in bench_dir.parents:
            result = subprocess.run(
                ["findmnt", "--target", str(parent), "--output", "SOURCE,FSTYPE,TARGET,OPTIONS",
                 "--noheadings", "--first-only"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                break

    info = result.stdout.strip()
    if not info:
        console.print("[bold red]Error:[/] Could not find mount info for bench directory.")
        console.print("  Make sure the NAS is mounted and the path is correct.")
        sys.exit(1)

    parts = info.split()
    source = parts[0] if len(parts) > 0 else "unknown"
    fstype = parts[1] if len(parts) > 1 else "unknown"

    if fstype.lower() not in ("cifs", "smb", "smb2", "smb3", "smbfs"):
        console.print(f"[bold yellow]Warning:[/] Mount filesystem type is [bold]{fstype}[/], not CIFS/SMB.")
        console.print("  Are you sure this is your NAS mount? Continuing anyway.")
        console.print()

    return {"source": source, "fstype": fstype, "raw": info}


def collect_system_info(mount_path: Path, bench_dir: Path) -> str:
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

    # Try ethtool on the default route interface
    try:
        iproute = subprocess.run(
            ["ip", "route", "get", "8.8.8.8"], capture_output=True, text=True, timeout=5
        )
        iface = None
        for token in iproute.stdout.split():
            if token == "dev":
                idx = iproute.stdout.split().index("dev")
                iface = iproute.stdout.split()[idx + 1]
                break
        if iface:
            eth = run(["ethtool", iface])
            lines.append("")
            lines.append(f"=== ethtool {iface} ===")
            lines.append(eth)
    except Exception:
        pass

    return "\n".join(lines)


def half_size(size_str: str) -> str:
    """Return half of a size string, staying in the same unit."""
    s = size_str.upper().strip()
    unit = s[-1] if s[-1] in "GMTK" else ""
    val = int(s[:-1]) if unit else int(s)
    half = max(1, val // 2)
    return f"{half}{unit}"


# ---------------------------------------------------------------------------
# fio runner
# ---------------------------------------------------------------------------

def run_fio(name: str, args: list[str], out_file: Path) -> dict:
    """Run fio with JSON output, save to out_file, return parsed JSON."""
    cmd = ["fio"] + args + [f"--output={out_file}", "--output-format=json"]
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
# Quick summary table after each test
# ---------------------------------------------------------------------------

def _iops_bw(job: dict, rw: str) -> tuple[float, float, float]:
    """Return (iops, bw_mb, lat_ms_mean) for 'read' or 'write'."""
    d = job.get(rw, {})
    if not d:
        return 0.0, 0.0, 0.0
    iops = d.get("iops", 0)
    bw = d.get("bw", 0) / 1024  # KB/s → MB/s
    lat = d.get("lat_ns", {}).get("mean", 0) / 1e6  # ns → ms
    return float(iops), float(bw), float(lat)


def print_quick_result(fio_data: dict, name: str) -> None:
    if not fio_data or "jobs" not in fio_data:
        return
    job = fio_data["jobs"][0]
    riops, rbw, rlat = _iops_bw(job, "read")
    wiops, wbw, wlat = _iops_bw(job, "write")

    t = Table(show_header=True, header_style="bold magenta", box=None, padding=(0, 1))
    t.add_column("Dir")
    t.add_column("BW MB/s",    justify="right")
    t.add_column("IOPS",       justify="right")
    t.add_column("Lat ms",     justify="right")

    if rbw > 0 or riops > 0:
        t.add_row("read",  f"{rbw:>8.1f}", f"{riops:>8.0f}", f"{rlat:>6.2f}")
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

    # Resolve paths
    mount_path = expand(args.mount)
    bench_dir  = expand(args.bench_dir) if args.bench_dir else mount_path / "bench"
    test_file  = bench_dir / "cache-test.bin"
    rw_file    = bench_dir / "cache-randrw.bin"
    size       = args.size
    mixed_size = half_size(size)

    runtime      = 30  if args.quick else args.runtime
    zipf_runtime = 60  if args.quick else (args.zipf_runtime or args.runtime + 60)
    jobs         = args.jobs
    iodepth      = args.iodepth

    # Results directory
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results_root = Path(args.results_dir) if args.results_dir else Path("results") / stamp
    results_root.mkdir(parents=True, exist_ok=True)

    # Banner
    console.print(Panel(
        f"[bold]NAS Benchmark Suite[/]\n"
        f"mount:     {mount_path}\n"
        f"bench dir: {bench_dir}\n"
        f"size:      {size}  (randrw: {mixed_size})\n"
        f"runtime:   {runtime}s  (Zipf: {zipf_runtime}s)\n"
        f"jobs:      {jobs}  iodepth: {iodepth}\n"
        f"results:   {results_root}\n"
        f"pre-warm:  {'yes — full sequential read before random tests' if args.pre_warm else 'no'}",
        title="bench-nas", border_style="blue"
    ))

    # Preflight checks
    check_fio()

    bench_dir.mkdir(parents=True, exist_ok=True)
    mount_info = check_mount(bench_dir)

    console.print(f"[bold green]Mount OK:[/] {mount_info['source']} ({mount_info['fstype']})")
    console.print()

    # System info
    sysinfo = collect_system_info(mount_path, bench_dir)
    (results_root / "sysinfo.txt").write_text(sysinfo)
    console.print("[dim]System info saved to sysinfo.txt[/]")
    console.print()

    # -----------------------------------------------------------------------
    # Common fio base flags
    # -----------------------------------------------------------------------
    base_file  = [f"--filename={test_file}"]
    base_rw    = [f"--filename={rw_file}"]
    base_size  = [f"--size={size}"]
    rw_size    = [f"--size={mixed_size}"]
    base_flags = ["--direct=1", "--group_reporting"]

    def fio_run(name: str, extra: list[str]) -> dict:
        out = results_root / f"{name}.json"
        data = run_fio(name, extra, out)
        print_quick_result(data, name)
        return data

    # -----------------------------------------------------------------------
    # Prepare test file
    # -----------------------------------------------------------------------
    if not args.skip_prepare:
        console.rule("[bold]Preparing test file[/]")
        fio_run("prepare", [
            "--name=prepare",
            *base_file, *base_size,
            "--bs=1M", "--rw=write",
            "--iodepth=16", "--numjobs=1",
            *base_flags,
        ])
    else:
        console.print("[yellow]Skipping file preparation (--skip-prepare)[/]\n")

    # -----------------------------------------------------------------------
    # A. Sequential write (control)
    # -----------------------------------------------------------------------
    console.rule("[bold]A. Sequential write (control)[/]")
    fio_run("seqwrite-control", [
        "--name=seqwrite-control",
        *base_file, *base_size,
        "--bs=1M", "--rw=write",
        "--iodepth=16", "--numjobs=1",
        f"--runtime={runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # B. Sequential read (control)
    # -----------------------------------------------------------------------
    console.rule("[bold]B. Sequential read (control)[/]")
    fio_run("seqread-control", [
        "--name=seqread-control",
        *base_file, *base_size,
        "--bs=1M", "--rw=read",
        "--iodepth=16", "--numjobs=1",
        f"--runtime={runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # Pre-warm: sequential read pass to fully populate NVMe cache
    # -----------------------------------------------------------------------
    if args.pre_warm:
        console.rule("[bold]Pre-warm: populating NVMe cache (full sequential read)[/]")
        console.print("  [dim]Reading entire test file sequentially to fill NVMe cache before random tests...[/]")
        fio_run("prewarm", [
            "--name=prewarm",
            *base_file, *base_size,
            "--bs=1M", "--rw=read",
            "--iodepth=16", "--numjobs=1",
            *base_flags,
        ])
        console.print("  [green]Cache warm — proceeding to random tests[/]\n")

    # -----------------------------------------------------------------------
    # C. Cold uniform random read
    # -----------------------------------------------------------------------
    console.rule("[bold]C. {}uniform random read[/]".format(
        "Pre-warmed " if args.pre_warm else "Cold "
    ))
    cold_name = "randread-prewarm" if args.pre_warm else "randread-cold"
    fio_run(cold_name, [
        f"--name={cold_name}",
        *base_file, *base_size,
        "--bs=4k", "--rw=randread",
        f"--iodepth={iodepth}", f"--numjobs={jobs}",
        f"--runtime={runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # D. Hot uniform random read (same file, immediately after)
    # -----------------------------------------------------------------------
    console.rule("[bold]D. Hot uniform random read{}[/]".format(
        " (cache already warm)" if args.pre_warm else ""
    ))
    fio_run("randread-hot", [
        "--name=randread-hot",
        *base_file, *base_size,
        "--bs=4k", "--rw=randread",
        f"--iodepth={iodepth}", f"--numjobs={jobs}",
        f"--runtime={runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # E. Zipf hot-set random read (first pass)
    # -----------------------------------------------------------------------
    console.rule("[bold]E. Zipf hot-set random read — pass 1 (warming)[/]")
    fio_run("zipf-randread-1", [
        "--name=zipf-randread-1",
        *base_file, *base_size,
        "--bs=4k", "--rw=randread",
        "--random_distribution=zipf:1.2",
        f"--iodepth={iodepth}", f"--numjobs={jobs}",
        f"--runtime={zipf_runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # F. Zipf hot-set random read (second pass — cache should be warm)
    # -----------------------------------------------------------------------
    console.rule("[bold]F. Zipf hot-set random read — pass 2 (hot)[/]")
    fio_run("zipf-randread-2-hot", [
        "--name=zipf-randread-2-hot",
        *base_file, *base_size,
        "--bs=4k", "--rw=randread",
        "--random_distribution=zipf:1.2",
        f"--iodepth={iodepth}", f"--numjobs={jobs}",
        f"--runtime={zipf_runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # G. Mixed random read/write (Zipf, separate file)
    # -----------------------------------------------------------------------
    console.rule("[bold]G. Mixed random read/write (Zipf 70/30)[/]")
    fio_run("randrw-cache", [
        "--name=randrw-cache",
        *base_rw, *rw_size,
        "--bs=4k", "--rw=randrw", "--rwmixread=70",
        "--random_distribution=zipf:1.2",
        f"--iodepth={iodepth}", f"--numjobs={jobs}",
        f"--runtime={zipf_runtime}", "--time_based",
        *base_flags,
    ])

    # -----------------------------------------------------------------------
    # Parse and summarize
    # -----------------------------------------------------------------------
    console.rule("[bold]Parsing results[/]")
    import importlib.util, sys as _sys
    spec = importlib.util.spec_from_file_location(
        "parse_results", Path(__file__).parent / "parse_fio_results.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.parse_and_print(results_root)

    # -----------------------------------------------------------------------
    # Cleanup
    # -----------------------------------------------------------------------
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
