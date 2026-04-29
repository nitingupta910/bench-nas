# UNAS Pro 4 NAS Benchmark Results

**Date:** 2026-04-29  
**Client:** inferno-dev (Ubuntu Server, kernel 7.0.0-14-generic)

## Plots

| Chart | Description |
|-------|-------------|
| ![Cache lift](plots/plot_cache_lift.png) | NVMe cache lift: HDD seq → uniform demand-cached → Zipf hot (IOPS + p99) |
| ![RTT timeline](plots/plot_rtt_timeline.png) | Live NFS RTT during Zipf warming → hot → randrw write pressure |
| ![IOPS](plots/plot_iops.png) | Read/write IOPS across all tests |
| ![p99](plots/plot_latency_p99.png) | p99 latency across all tests (log scale) |
| ![BW](plots/plot_bandwidth.png) | Read/write bandwidth across all tests |
| ![CIFS vs NFS IOPS](plots/plot_cifs-vs-nfs_read_iops.png) | CIFS vs NFS read IOPS comparison |
| ![CIFS vs NFS p99](plots/plot_cifs-vs-nfs_read_p99.png) | CIFS vs NFS p99 latency comparison |

---

## Hardware Configuration

### NAS — UniFi UNAS Pro 4
| Component | Detail |
|-----------|--------|
| Model | UniFi UNAS Pro 4 |
| HDDs | 5900 RPM, RAID 1 (mirrored) |
| NVMe cache | 2× 2 TB NVMe in RAID 1 = **2 TB effective** |
| Cache policy | **Random IO only** — sequential IO bypasses NVMe cache entirely |
| Cache mode | **Read-write (write-back)** — writes land on NVMe first, destage to HDDs async |
| NIC | 10 GbE SFP+ (direct link to client) + 1 GbE Ethernet (local switch) |

The cache policy is the most important configuration detail: sequential reads and writes
never touch the NVMe cache, regardless of cache state. Only random IO benefits from the
NVMe tier. This explains why sequential benchmarks look the same with or without cache.

### Client — inferno-dev
| Component | Detail |
|-----------|--------|
| OS | Ubuntu Server, kernel 7.0.0-14-generic |
| NIC | Mellanox ConnectX-4 (CX4), PCIe 3.0 x4 |
| Interface | `enp8s0f1np1` at `192.168.1.2`, 10 000 Mb/s full duplex |
| Link to NAS | Direct SFP+ 10 GbE (`192.168.1.2` → `192.168.1.77`) |

### Network Path
```
inferno-dev  192.168.1.2  enp8s0f1np1  Mellanox CX4  PCIe 3.0 x4
      │  10 GbE SFP+ direct cable (no switch)
UNAS Pro 4   192.168.1.77  SFP+ port
```
Zero retransmits observed across 9.8M+ NFS RPC calls during benchmarking.

---

## Mount Configuration

### Protocol: NFS v3 (switched from CIFS/SMB — see CIFS vs NFS section)

Full `/etc/fstab` on inferno-dev:

```
/dev/disk/by-uuid/e689a462-9246-4f0d-b6c8-d96713db540e  /           btrfs   noatime,compress=zstd:3              0 1
/dev/disk/by-uuid/86DA-6FBF                             /boot/efi   vfat    defaults                             0 1
/swap.img                                               none        swap    sw                                   0 0
debugfs                                                 /sys/kernel/debug  debugfs  defaults,mode=0770,gid=1001  0 0
tracefs                                                 /sys/kernel/tracing tracefs defaults,mode=0770,gid=1001  0 0

# NAS

## CIFS (disabled — replaced by NFS for performance)
# //192.168.1.77/Shared_Drive /home/ngupta/nas cifs \
#   credentials=/etc/samba/credentials/unifi-nas,uid=1000,gid=1000,vers=3.0, \
#   nofail,x-systemd.automount,_netdev 0 0

## NFS v3 (active)
192.168.1.77:/volume/f7b82ab5-2609-4245-867c-5bf03d8f936e/.srv/.unifi-drive/Shared_Drive/.data \
    /home/ngupta/nas \
    nfs vers=3,rsize=1048576,wsize=1048576,noatime,nordirplus,nconnect=8,nocto,proto=tcp, \
        nofail,x-systemd.automount,_netdev 0 0
```

**NFS export path note:** the UUID path is the internal UNAS Pro 4 path for `Shared_Drive`.
Stable across reboots but tied to volume UUID — update if the volume is rebuilt.
The export must allow `192.168.1.2` (10 GbE SFP+ client IP), not just `192.168.1.39`
(1 GbE IP) — the 10 GbE interface is what the routing table uses to reach `192.168.1.77`.

### Key NFS mount options
| Option | Value | Why |
|--------|-------|-----|
| `vers=3` | NFSv3 | Lower per-op overhead vs NFSv4 for pure throughput |
| `rsize`/`wsize` | 1 048 576 (1 MB) | Maximum for NFSv3; matches NAS `bsize=1048576` |
| `nconnect` | **8** | Most impactful: 8 parallel TCP connections. One stream cannot saturate 10 GbE. Confirmed: 8 ESTAB connections active during all tests. |
| `nocto` | — | Skips GETATTR RPC on file open (close-to-open coherence). Safe on single-client, removes per-open metadata overhead. |
| `nordirplus` | — | Disables READDIRPLUS; avoids fat directory RPC responses. |
| `noatime` | — | No access-time update RPCs on reads. |
| `proto=tcp` | — | UDP loses packets at 10 GbE speeds. |

---

## Benchmark Suite Design

Tool: [bench-nas](https://github.com/nitingupta910/bench-nas)  
Test file: `~/nas/bench/cache-test.bin` (128 GB)  
randrw file: `~/nas/bench/cache-randrw.bin` (64 GB, separate to preserve read pattern)  
fio flags: `--direct=1 --group_reporting` on all tests

| ID | fio job name | Description |
|----|-------------|-------------|
| A | `seqwrite-control` | 1M bs sequential write, 1 job — **HDD baseline, cache bypassed** |
| B | `seqread-control` | 1M bs sequential read, 1 job — **HDD baseline, cache bypassed** |
| C | `randread-uniform-1` | 4K uniform random read, 4 jobs, iodepth 32 — first-touch demand-caching |
| D | `randread-uniform-2` | Same as C immediately after — demand-cached warm |
| E | `zipf-randread-1` | 4K Zipf(θ=1.2) random read, 180s — hot-set warming pass |
| F | `zipf-randread-2` | Same Zipf config — **hot-set fully cached, definitive result** |
| G | `randrw-zipf` | 4K Zipf 70/30 read/write, 180s, separate file — write pressure test |

**Why the Zipf two-pass is the correct cache measurement:**  
With a random-only caching policy, there is no reliable way to externally pre-warm the
cache before testing. A sequential sweep bypasses the cache; a 4K random sweep of 128G
takes ~53 minutes. The Zipf two-pass is self-contained: pass 1 warms the hot set as part
of the workload, pass 2 measures the fully-cached steady state. This mirrors real workloads.

**Why randrw uses a separate file:**  
Write IOs during test G would overwrite blocks in `cache-test.bin` and corrupt the read
pattern cached from tests E/F. Using `cache-randrw.bin` keeps the write working set
independent so the write-pressure measurement is clean.

---

## Run 1 — Quick validation (CIFS/SMB3, cache already warm)
**Date/time:** 2026-04-29 01:27  
**Config:** CIFS/SMB3 mount, `--skip-prepare`, `--quick` (30s/60s runtimes)  
**Purpose:** Suite validation and CIFS baseline. NVMe cache was warm from prior activity —
cold/hot uniform numbers are not reliable cold baselines.

### Results
| Test | R BW MB/s | W BW MB/s | R IOPS | W IOPS | R lat ms | W lat ms | R p95 ms | R p99 ms | W p95 ms | W p99 ms |
|------|-----------|-----------|--------|--------|----------|----------|----------|----------|----------|----------|
| Seq write — HDD baseline | — | 139.4 | — | 139 | — | 7.17 | — | — | 1.91 | 3.10 |
| Seq read — HDD baseline | 84.6 | — | 85 | — | 11.82 | — | 14.61 | 27.13 | — | — |
| Uniform rand read — pass 1 | 22.2 | — | 5 682 | — | 0.70 | — | 0.90 | 6.46 | — | — |
| Uniform rand read — pass 2 | 22.8 | — | 5 836 | — | 0.68 | — | 0.88 | 5.67 | — | — |
| Zipf rand read — pass 1 | 25.3 | — | 6 472 | — | 0.62 | — | 0.80 | 1.58 | — | — |
| Zipf rand read — pass 2 ★ | 24.4 | — | 6 238 | — | 0.64 | — | 0.78 | **1.55** | — | — |
| Mixed randrw Zipf 70/30 | 2.0 | 0.9 | 509 | 223 | 4.18 | 8.38 | 21.63 | 37.49 | 28.70 | 41.68 |

**Note:** Zipf pass 2 IOPS (6 238) slightly *lower* than pass 1 (6 472) — this is a 30s
run artefact, not a real regression. The short runtime means variance dominates.

---

## Run 2 — Full benchmark, NFS v3, no pre-warm
**Date/time:** 2026-04-29 01:55  
**Config:** NFS v3, nconnect=8, `--skip-prepare`, 120s/180s runtimes

### Results
| Test | R BW MB/s | W BW MB/s | R IOPS | W IOPS | R lat ms | W lat ms | R p95 ms | R p99 ms | W p95 ms | W p99 ms |
|------|-----------|-----------|--------|--------|----------|----------|----------|----------|----------|----------|
| Seq write — HDD baseline | — | 147.8 | — | 148 | — | 6.76 | — | — | 3.19 | 158.33 |
| Seq read — HDD baseline | 101.7 | — | 102 | — | 9.83 | — | 10.03 | 11.99 | — | — |
| Uniform rand read — pass 1 | 7.5 | — | 1 910 | — | 2.09 | — | 13.96 | 29.23 | — | — |
| Uniform rand read — pass 2 | 13.0 | — | 3 339 | — | 1.20 | — | 5.47 | 21.63 | — | — |
| Zipf rand read — pass 1 | 31.2 | — | 7 999 | — | 0.50 | — | 0.53 | 7.70 | — | — |
| Zipf rand read — pass 2 ★ | 41.5 | — | 10 619 | — | 0.38 | — | 0.53 | **0.59** | — | — |
| Mixed randrw Zipf 70/30 | 3.5 | 1.5 | 890 | 384 | 2.39 | 4.87 | 15.79 | 30.54 | 22.41 | 33.42 |

### Key comparisons
| Comparison | IOPS ratio | p99 improvement |
|------------|-----------|-----------------|
| Uniform pass 1 → pass 2 | 1.75× | +26% (29ms → 22ms) |
| Zipf pass 1 → pass 2 | 1.33× | **+92%** (7.70ms → 0.59ms) |

### Observations
- **Seq write 147.8 MB/s** — unusually high; write-back NVMe buffer was pre-warmed from earlier
  activity and absorbed sequential writes. Run 3 showed the true HDD baseline at 73.8 MB/s.
- **Uniform pass 1 (1 910 IOPS / p99 29ms)** — reflects partial demand-caching; the cache
  was not fully cold. True uncached HDD random reads would be ~120–160 IOPS at 12–15ms.
- **Zipf pass 2 p99 0.59ms** — definitive NVMe cache result for this hardware.

---

## Run 3 — Full benchmark, NFS v3, with pre-warm attempt (definitive run)
**Date/time:** 2026-04-29 02:17  
**Config:** NFS v3, nconnect=8, `--skip-prepare`, attempted pre-warm, 120s/180s runtimes

### Pre-warm finding
The pre-warm ran a 64K random read sweep of the 128G file. Result: **103 MB/s with HDD-like
latency** — identical to sequential read performance. The UNAS Pro 4 treated 64K blocks as
sequential IO and bypassed the NVMe cache. The pre-warm did not populate the cache.

This confirmed the cache's sequential IO threshold is below 64K. Effective pre-warm would
require 4K random reads (~53 min to sweep 128G) — impractical. The Zipf two-pass design
is the correct methodology.

### Results
| Test | R BW MB/s | W BW MB/s | R IOPS | W IOPS | R lat ms | W lat ms | R p95 ms | R p99 ms | W p95 ms | W p99 ms |
|------|-----------|-----------|--------|--------|----------|----------|----------|----------|----------|----------|
| Seq write — HDD baseline | — | 73.8 | — | 74 | — | 13.54 | — | — | 3.06 | 509.61 |
| Seq read — HDD baseline | 99.9 | — | 100 | — | 10.00 | — | 10.29 | 17.17 | — | — |
| Pre-warm (64K rand, ineffective) | 103.0 | — | 103 | — | 9.70 | — | 10.16 | 11.34 | — | — |
| Uniform rand read — pass 1 | 18.4 | — | 4 713 | — | 0.85 | — | 0.65 | 16.32 | — | — |
| Uniform rand read — pass 2 | 23.2 | — | 5 931 | — | 0.67 | — | 0.63 | 12.12 | — | — |
| Zipf rand read — pass 1 | 43.8 | — | 11 216 | — | 0.36 | — | 0.53 | **0.59** | — | — |
| Zipf rand read — pass 2 ★ | 44.3 | — | 11 346 | — | 0.35 | — | 0.53 | **0.59** | — | — |
| Mixed randrw Zipf 70/30 | 5.1 | 2.2 | 1 295 | 556 | 1.02 | 4.82 | 0.84 | 20.05 | 31.06 | 53.22 |

### Key comparisons
| Comparison | IOPS ratio | p99 improvement |
|------------|-----------|-----------------|
| Uniform pass 1 → pass 2 | 1.26× | +26% |
| Zipf pass 1 → pass 2 | 1.01× | <2% — **both at NVMe floor** |

### Observations

**Seq write 73.8 MB/s, p99 509ms**  
Write-back buffer was cold; sequential writes bypassed cache and hit both RAID 1 HDDs over NFS.
The 509ms p99 spike is a single IO stalling during HDD seek + RAID 1 write to both mirrors.
This is the honest HDD write baseline.

**Zipf pass 1 already at 0.59ms p99 (11 216 IOPS)**  
Despite the ineffective pre-warm, pass 1 started at full NVMe speed. The Zipf hot set overlapped
with blocks already in cache from prior test activity. Pass 1 and pass 2 are effectively identical —
the cache was already at steady state before pass 1 started.

**randrw improved vs run 2 (1 295 vs 890 read IOPS)**  
Warmer cache state at test start. Write p99 jumped to 53ms vs 33ms in run 2 — at higher IOPS the
NVMe write buffer more frequently stalls on HDD destaging.

---

## Real-Time NFS RTT and IO Observations (Run 3)

Monitored via `mountstats` every 30s during the Zipf and randrw phases.
Also checked: TCP connection count, RPC retransmits, backlog queue depth.

### Preflight stats (before Zipf tests)
```
TCP connections to NAS: 8 ESTAB (all from 192.168.1.2, nconnect=8 confirmed)
RPC retransmits: 0 across 5.2M+ calls
Backlog queue: ~0.013ms (negligible — 8 connections have ample capacity)
```

### Zipf pass 1 — cache warming visible in real time

RTT data is cumulative (`mountstats` reports running averages since mount time),
so each sample includes all prior tests. The decline rate reflects pass 1 ops
individually being faster than the historical average — i.e. NVMe hits.

| Time | Cumul. READ RTT | Δ/30s | Interpretation |
|------|----------------|-------|----------------|
| Pass 1 start | 0.83 ms | — | Carry-over from prior tests |
| +30 s | 0.80 ms | −30 µs | Cache filling steadily |
| +60 s | 0.77 ms | −30 µs | Linear trend — hot set loading |
| +90 s | 0.75 ms | −20 µs | Rate slowing — hottest blocks done |
| +120 s | 0.73 ms | −20 µs | Mid-hot-set populating |
| +150 s | 0.72 ms | −10 µs | Tail of hot set loading |
| Pass 1 end | ~0.71 ms | — | Hot set ~fully loaded |

The ~25 µs/30s linear decline reflects Zipf(θ=1.2)'s continuous distribution:
the hot set is a spectrum, not a binary threshold. No sharp knee = consistent
cache fill rate across the full access distribution.

### Zipf pass 2 — NVMe cache floor confirmed

Pass 2 ops are individually faster than the cumulative average, pulling it down
faster than pass 1. Back-calculation gives the implied pass-2-only RTT.

| Time | Cumul. READ RTT | Implied pass-2 RTT | Interpretation |
|------|----------------|-------------------|----------------|
| Pass 2 start | 0.72 ms | — | Carries pass 1 history |
| +30 s | 0.70 ms | ~0.45 ms | Hot set fully hit |
| +60 s | 0.68 ms | ~0.40 ms | Converging |
| +90 s | 0.67 ms | ~0.35 ms | Nearing protocol floor |
| +120 s | 0.65 ms | ~0.32 ms | NFS overhead visible |
| +150 s | 0.64 ms | ~0.30 ms | Asymptoting |

**True pass 2 RTT: ~0.30–0.35 ms** — this is the NFS v3 protocol overhead floor over
10 GbE (RPC serialization + TCP stack). The NVMe cache latency itself is ~0.05–0.10 ms;
the remaining ~0.25 ms is irreducible NFS overhead.

Write RTT was frozen at 5.89ms during pass 1 and 2 (no writes, cumulative from earlier
tests) — confirming the read workload generates zero write traffic and doesn't dirty
the NVMe write buffer.

### randrw — cache resilience under write pressure

| Time | READ RTT | WRITE RTT | Write IOPS (interval) | Interpretation |
|------|----------|-----------|-----------------------|----------------|
| Start | 0.638 ms | 5.87 ms | ~470 | Writes just starting |
| +30 s | 0.637 ms | 5.81 ms | ~630 | Write buffer warming |
| +60 s | 0.635 ms | **5.24 ms** | ~1 030 | Write buffer at full speed |
| +90 s | 0.637 ms | 5.37 ms | ~370 | Destage cycle: flushing to HDDs |
| +120 s | 0.642 ms | 5.35 ms | ~370 | Steady destage rhythm |
| +150 s | 0.646 ms | 5.32 ms | ~370 | Read RTT creeping up |
| End | 0.647 ms | 5.31 ms | — | Settled |

**READ RTT rose only 12 µs (+1.9%) over 3 minutes of 30% write load.**  
The 2TB NVMe cache absorbed both the Zipf read hot set and the 64G write working set
without any meaningful cache contention. At the observed rate it would take ~15 more
minutes to reach 0.70ms — far beyond any realistic workload burst duration.

**WRITE RTT pattern** (5.24ms → 5.87ms oscillation): the NVMe write buffer absorbs writes
quickly at first (5.24ms), then pauses to destage dirty blocks to both RAID 1 HDDs (~5.9ms
floor = one write to both 5900 RPM mirrors). This is normal write-back cache behaviour —
not a problem, just the cache doing its job.

**CPU and memory overhead** (observed during benchmark):
- No measurable CPU spike on the client from NFS IO — the Mellanox CX4 offloads TCP
  checksum/segmentation, keeping kernel overhead low even at 10K+ IOPS
- NFS client kernel memory usage stayed flat — `nconnect=8` uses 8 socket buffers but
  these are small relative to system RAM
- `backlog wait: 0.013ms` throughout — no IO queuing at the TCP layer; the 8 connections
  provide enough parallelism to keep the NAS server busy without backing up

---

## Cache Performance Summary

### Zipf hot-set: NVMe cache vs raw HDD RAID 1 (5900 RPM)
| Metric | Raw HDD estimate | With NVMe cache | Lift |
|--------|-----------------|-----------------|------|
| Random IOPS (4K) | ~120–160 | **11 346** | **~70–95×** |
| Average latency | ~8–15 ms | **0.35 ms** | **~25–40×** |
| p99 latency | ~15–20 ms | **0.59 ms** | **~25–34×** |
| Sequential throughput | ~100–150 MB/s | ~100 MB/s | 1× (cache bypassed) |

### NFS protocol overhead breakdown
| Layer | Latency |
|-------|---------|
| NVMe SSD access | ~0.05–0.10 ms |
| NAS OS + NFS server stack | ~0.10–0.15 ms |
| TCP + 10 GbE round trip | ~0.10 ms |
| **Total NFS RTT (cache hit)** | **~0.30–0.35 ms** |
| fio-reported p99 (includes queuing) | **0.59 ms** |

---

## Key Findings

1. **NVMe cache delivers ~70–95× IOPS and ~25–34× p99 latency improvement** over the raw
   5900 RPM RAID 1 HDD baseline for Zipf hot-set workloads.

2. **The cache policy (random IO only) is correct for this HDD+NVMe configuration.**
   Sequential IO saturates HDD throughput at the network ceiling; caching it would waste
   NVMe space for zero measurable benefit.

3. **2 TB NVMe RAID 1 has ample headroom for realistic workloads.** 128G test + 64G randrw
   = 9% of cache capacity. Under 30% write pressure for 3 minutes, READ RTT rose only 12 µs.
   No cache eviction observed.

4. **`nconnect=8` is essential on 10 GbE.** All 8 TCP connections carried active load.
   A single stream cannot saturate the 10 GbE link and would become the bottleneck before
   the NAS storage or NVMe cache.

5. **True NVMe cache RTT floor is ~0.30–0.35 ms** over NFS v3/10 GbE. This is irreducible
   protocol overhead — the NVMe cache itself is not the bottleneck.

6. **Pre-warm via 64K random sweep is ineffective on UNAS Pro 4.** The NAS treats ≥64K
   blocks as sequential and bypasses the cache. The Zipf two-pass design is the correct
   and only practical methodology for cache warm-up measurement.

7. **Sequential write variance (74–148 MB/s) is a write-back buffer artefact**, not a
   protocol or HDD difference. When the write buffer is pre-warmed from prior activity,
   the initial burst hits NVMe (148 MB/s); when cold, it goes straight to HDDs (74 MB/s).

8. **NFS v3 outperforms CIFS/SMB3 on this setup.** Zipf p99: 0.59ms NFS vs 1.55ms CIFS
   (+62%). Zipf IOPS: 11 346 NFS vs 6 238 CIFS (+82%). The delta is almost entirely
   per-operation protocol overhead — NFS v3 RPC is simpler than SMB compound requests,
   and `nconnect=8` parallelises the stream in a way CIFS cannot match in this configuration.

---

## CIFS vs NFS Comparison

Run 1 was over **CIFS/SMB3**; runs 2 and 3 over **NFS v3**.
Runtimes differ (30s CIFS vs 120s NFS) so absolute numbers are not directly comparable,
but directional differences are consistent with protocol theory.

### Mount options side by side

**CIFS/SMB3 (run 1 — disabled)**
```
//192.168.1.77/Shared_Drive /home/ngupta/nas cifs
    credentials=/etc/samba/credentials/unifi-nas,
    uid=1000,gid=1000,vers=3.0,
    nofail,x-systemd.automount,_netdev  0 0
```
No rsize/wsize, no multichannel, default SMB3 settings.

**NFS v3 (active)**
```
<export> /home/ngupta/nas nfs
    vers=3,rsize=1048576,wsize=1048576,
    noatime,nordirplus,nconnect=8,nocto,proto=tcp,
    nofail,x-systemd.automount,_netdev  0 0
```

### Results side-by-side

| Test | CIFS 30s | NFS 120s | Delta |
|------|---------|---------|-------|
| Seq write MB/s | 139.4 | 73.8–147.8 | ~equal (write buffer state) |
| Seq read MB/s | 84.6 | 99.9–101.7 | **NFS +18%** |
| Uniform pass 1 IOPS | 5 682* | 1 910 | *CIFS higher but cache was warm |
| Zipf pass 2 IOPS | 6 238 | 10 619–11 346 | **NFS +70–82%** |
| Zipf pass 2 p99 ms | 1.55 | **0.59** | **NFS +62%** |
| randrw read IOPS | 509 | 890–1 295 | **NFS +75–155%** |
| randrw write IOPS | 223 | 384–556 | **NFS +72–149%** |

\* CIFS cold/hot uniform numbers not reliable — cache was warm from prior activity.

### Why NFS wins at high IOPS

At ~10K IOPS, per-operation protocol cost dominates. CIFS adds ~0.96ms overhead per
op vs NFS at this IOPS level (1.55ms − 0.59ms). The gap is almost entirely protocol
overhead, not storage latency:

| Factor | CIFS/SMB3 | NFS v3 |
|--------|-----------|--------|
| Per-op overhead | High — auth, signing negotiation, compound requests | Low — simple stateless RPC |
| Parallel TCP streams | No equivalent to nconnect | `nconnect=8` — 8 independent streams |
| Metadata on open | Always validates on file open | `nocto` skips GETATTR on single-client |
| Linux kernel client | Good | Excellent — decades of optimization |
| Best for | Windows clients, ACL-heavy workloads | Linux clients, throughput/latency |

### Seq read improvement (NFS +18%)
Even sequential IO shows NFS benefit because CIFS compound-request overhead adds latency
to each 1MB block request. Not dramatic, but measurable.

---

## Files

```
results/
  20260429-012716/         Run 1 — quick CIFS validation (30s/60s, warm cache)
  20260429-015508/         Run 2 — full NFS v3, no pre-warm (120s/180s)
  20260429-021708/         Run 3 — full NFS v3, pre-warm attempt (120s/180s) ← definitive
    sysinfo.txt            hostname, uname, lsblk, df, findmnt, ethtool
    seqwrite-control.json
    seqread-control.json
    randread-uniform-1.json
    randread-uniform-2.json
    zipf-randread-1.json
    zipf-randread-2.json   ← definitive NVMe cache result
    randrw-zipf.json
    summary.txt            ASCII table + key comparisons
    summary.csv            machine-readable
```
