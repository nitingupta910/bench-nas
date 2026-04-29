# bench-nas

fio-based NAS performance benchmarking suite for CIFS/SMB mounts, designed to
reveal the effect of the UniFi UNAS Pro 4 NVMe cache tier.

## What this measures

| Test | Purpose |
|------|---------|
| Sequential write / read | Network + HDD RAID baseline (control) |
| Cold uniform random read | Baseline random IO with cold cache |
| Hot uniform random read | Same file immediately after — cache warm-up effect |
| Zipf random read pass 1 | Hot-set access, warming the NVMe cache |
| Zipf random read pass 2 | Same hot-set — measures actual cache hit improvement |
| Mixed randrw (Zipf 70/30) | Real-world mixed workload with write pressure |

## Why sequential tests don't show NVMe cache benefit

The UNAS Pro 4 NVMe SSD acts as a **random-IO cache**, not a throughput
accelerator. Sequential workloads saturate the 1 GbE / 2.5 GbE link (≈ 125
MB/s or 312 MB/s respectively), so the bottleneck is the network, not the
drives. The NVMe cache cannot exceed that ceiling, and the RAID HDDs can
already sustain sequential reads at those speeds. You will see the same MB/s
with or without the cache on sequential benchmarks — that is expected and
correct.

## Why random IO and Zipf workloads are the important tests

- **4 KB random IO** is dominated by seek latency on HDDs (5–15 ms per IO).
  The NVMe SSD can serve the same IO in tens of microseconds.
- **Cold → hot comparison**: if the hot run shows higher IOPS and lower p99
  latency than the cold run, the cache is absorbing repeated reads.
- **Zipf distribution** (θ = 1.2) models real-world access patterns where a
  small hot subset of data (≈ 20%) receives most of the IO (≈ 80%). Pass 2
  should show significantly better numbers than pass 1 if the hot set fits in
  the NVMe cache.

## Prerequisites

```bash
sudo apt update
sudo apt install -y fio
```

Install Python tooling:

```bash
# Install uv if not present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install Python deps
uv sync
```

## Running

```bash
# Full benchmark with defaults (~/nas, 128G file, 120s / 180s runs)
uv run run_nas_bench.py

# Quick validation run (30s / 60s)
uv run run_nas_bench.py --quick

# Custom mount and larger file
uv run run_nas_bench.py --mount /mnt/nas --size 256G --runtime 180

# More parallelism
uv run run_nas_bench.py --jobs 8 --iodepth 64

# Skip file preparation if it already exists
uv run run_nas_bench.py --skip-prepare

# Remove benchmark files after run
uv run run_nas_bench.py --cleanup

# Re-parse a previous results directory
uv run parse_fio_results.py results/20240429-143022/
```

### All options

```
--mount         NAS mount point (default: ~/nas)
--bench-dir     Benchmark directory (default: <mount>/bench)
--size          Test file size (default: 128G)
--runtime       Standard test duration in seconds (default: 120)
--zipf-runtime  Zipf test duration (default: runtime + 60)
--jobs          fio numjobs (default: 4)
--iodepth       fio iodepth (default: 32)
--skip-prepare  Skip test file creation
--quick         Short runs: 30s standard, 60s Zipf
--cleanup       Delete benchmark files after run
--results-dir   Override results output directory
```

## Interpreting results

### Sequential MB/s
Reflects your **network link speed** + HDD RAID throughput. On 1 GbE expect
~110–120 MB/s. On 2.5 GbE expect ~280–310 MB/s. Numbers above this indicate
aggregated links or faster networking.

### Random IOPS and p95/p99 latency
This is where NVMe cache shows its value:

- **Cold IOPS** = baseline HDD performance (~50–200 IOPS for 4K random on RAID HDDs)
- **Hot IOPS** = cache-served reads (potentially 10–100× higher)
- **p99 latency** drop from cold → hot is the most reliable signal

### Cache-hit indicators

| Signal | No cache benefit | Cache helping |
|--------|-----------------|---------------|
| Hot/cold IOPS ratio | ~1.0× | 5×–50× |
| Zipf pass2/pass1 IOPS ratio | ~1.0× | 3×–30× |
| p99 latency improvement | <10% | 50%–99% |

### Mixed randrw
Shows whether write IO competes with cached reads. Heavy write pressure can
evict cached data, reducing read performance. Compare read IOPS here against
the hot read numbers.

## Warnings

- **Verify your mount**: the script checks `findmnt` for CIFS/SMB type. If
  you accidentally point it at a local directory it will warn you.
- **Large files**: the default 128G test file + 64G randrw file consume ~192G
  of NAS storage. Adjust `--size` accordingly.
- **Write tests modify data**: the sequential write and randrw tests overwrite
  benchmark files. Do not point `--bench-dir` at important data.
- **No sudo required**: the benchmark itself runs as your user. `lsblk` and
  `ethtool` in sysinfo are best-effort and won't fail the benchmark if absent.
- **Do not run on production directories**.

## Output

Results are saved to `results/YYYYMMDD-HHMMSS/`:

```
results/
  20240429-143022/
    sysinfo.txt           # hostname, uname, lsblk, df, findmnt, ethtool
    seqwrite-control.json
    seqread-control.json
    randread-cold.json
    randread-hot.json
    zipf-randread-1.json
    zipf-randread-2-hot.json
    randrw-cache.json
    summary.txt           # ASCII table + key comparisons
    summary.csv           # machine-readable
```
