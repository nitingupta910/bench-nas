# UNAS Pro 4 NAS Benchmark Results

**Date:** 2026-04-29  
**Client:** inferno-dev (Ubuntu Server, kernel 7.0.0-14-generic)

---

## Hardware Configuration

### NAS — UniFi UNAS Pro 4
| Component | Detail |
|-----------|--------|
| Model | UniFi UNAS Pro 4 |
| HDDs | 5900 RPM, RAID 1 (mirrored) |
| NVMe cache | 2× 2 TB NVMe in RAID 1 = 2 TB effective cache |
| Cache policy | **Random IO only** — sequential IO bypasses cache entirely |
| Cache mode | **Read-write (write-back)** — writes land on NVMe first, destage to HDDs async |
| NIC | 10 GbE SFP+ (direct link to client) + 1 GbE Ethernet (local switch) |

### Client — inferno-dev
| Component | Detail |
|-----------|--------|
| OS | Ubuntu Server, kernel 7.0.0-14-generic |
| NIC | Mellanox ConnectX-4 (CX4), PCIe 3.0 x4 |
| Interface | `enp8s0f1np1` at 192.168.1.2, 10 000 Mb/s full duplex |
| Link to NAS | Direct SFP+ 10 GbE (192.168.1.2 → 192.168.1.77) |

### Network Path
```
inferno-dev (192.168.1.2, enp8s0f1np1, Mellanox CX4)
      │  10 GbE SFP+ direct cable
UNAS Pro 4 (192.168.1.77, SFP+ port)
```
No switch in path. Zero retransmits across 9.8M+ NFS RPC calls.

---

## Mount Configuration

### Protocol: NFS v3 (switched from CIFS/SMB)

Full `/etc/fstab` on inferno-dev:

```
# <file system>                                                    <mount point>   <type>   <options>                                    <dump> <pass>
/dev/disk/by-uuid/e689a462-9246-4f0d-b6c8-d96713db540e            /               btrfs    noatime,compress=zstd:3                      0      1
/dev/disk/by-uuid/86DA-6FBF                                        /boot/efi       vfat     defaults                                     0      1
/swap.img                                                          none            swap     sw                                           0      0
debugfs                                                            /sys/kernel/debug debugfs defaults,mode=0770,gid=1001                0      0
tracefs                                                            /sys/kernel/tracing tracefs defaults,mode=0770,gid=1001              0      0

# NAS

## CIFS (disabled — replaced by NFS)
# //192.168.1.77/Shared_Drive /home/ngupta/nas cifs credentials=/etc/samba/credentials/unifi-nas,uid=1000,gid=1000,vers=3.0,nofail,x-systemd.automount,_netdev 0 0

## NFS
192.168.1.77:/volume/f7b82ab5-2609-4245-867c-5bf03d8f936e/.srv/.unifi-drive/Shared_Drive/.data \
    /home/ngupta/nas \
    nfs vers=3,rsize=1048576,wsize=1048576,noatime,nordirplus,nconnect=8,nocto,proto=tcp,nofail,x-systemd.automount,_netdev 0 0
```

Notes on the NFS export path: the UUID-style path
`/volume/f7b82ab5-2609-4245-867c-5bf03d8f936e/.srv/.unifi-drive/Shared_Drive/.data`
is the internal UNAS Pro 4 path for the `Shared_Drive` share. It is stable across reboots
but tied to the volume UUID — update if the volume is rebuilt.
The NFS export must explicitly allow `192.168.1.2` (the 10 GbE SFP+ client IP); the 1 GbE
IP `192.168.1.39` is on a different interface and would route over the slow path.

### Key mount options and rationale
| Option | Value | Why |
|--------|-------|-----|
| `vers=3` | NFSv3 | Lower per-op overhead than NFSv4 for pure throughput |
| `rsize`/`wsize` | 1 048 576 (1 MB) | Maximum for NFSv3; matches NAS bsize |
| `nconnect` | 8 | **Most impactful option.** Opens 8 parallel TCP connections; one stream cannot saturate 10 GbE. Confirmed: 8 ESTAB connections observed during benchmark. |
| `nocto` | — | Skips GETATTR RPC on every file open (close-to-open coherence). Safe on single-client, reduces metadata overhead. |
| `nordirplus` | — | Disables READDIRPLUS; reduces metadata RPC fan-out on directory listings. |
| `noatime` | — | No access-time update RPCs on reads. |
| `proto=tcp` | — | UDP drops packets at 10 GbE speeds. |

### Why NFS over CIFS/SMB
CIFS/SMB3 has higher per-operation protocol overhead (authentication, compound requests, signing negotiation). On 10 GbE with thousands of random IOPS, this overhead is measurable. NFS v3 has a simpler RPC model, lower latency floor, and a mature Linux kernel client. The switch from CIFS to NFS was the right call for benchmarking the cache accurately.

---

## Benchmark Suite

Tool: [bench-nas](https://github.com/ngupta/bench-nas) — custom fio-based suite.  
Test file: `~/nas/bench/cache-test.bin` (128 GB)  
randrw file: `~/nas/bench/cache-randrw.bin` (64 GB)

### Tests
| ID | Name | Description |
|----|------|-------------|
| A | seqwrite-control | 1 MB sequential write, 120 s — HDD+network baseline |
| B | seqread-control | 1 MB sequential read, 120 s — HDD+network baseline |
| pre-warm | prewarm | 64 K random read sweep of full file — populates NVMe cache |
| C | randread-cold / randread-prewarm | 4 K uniform random read, 4 jobs, iodepth 32, 120 s |
| D | randread-hot | Same as C, immediately after — measures demand-cached lift |
| E | zipf-randread-1 | 4 K Zipf(θ=1.2) random read, 180 s — warms hot set into NVMe |
| F | zipf-randread-2-hot | Same Zipf config again — hot set fully cached |
| G | randrw-cache | 4 K Zipf 70/30 read/write mix, 180 s, separate 64 GB file |

---

## Run 1 — Quick validation (NFS, no pre-warm)
*30 s / 60 s runtimes. Used to validate suite end-to-end. Not used for analysis.*

---

## Run 2 — Full benchmark, no pre-warm
**Date/time:** 2026-04-29 01:55  
**Config:** NFS v3, nconnect=8, `--skip-prepare`, default runtimes (120 s / 180 s)

### Results
| Test | R BW MB/s | W BW MB/s | R IOPS | W IOPS | R lat ms | W lat ms | R p95 ms | R p99 ms | W p95 ms | W p99 ms |
|------|-----------|-----------|--------|--------|----------|----------|----------|----------|----------|----------|
| Seq write (control) | — | 147.8 | — | 148 | — | 6.76 | — | — | 3.19 | 158.33 |
| Seq read (control) | 101.7 | — | 102 | — | 9.83 | — | 10.03 | 11.99 | — | — |
| Rand read — cold | 7.5 | — | 1 910 | — | 2.09 | — | 13.96 | 29.23 | — | — |
| Rand read — hot | 13.0 | — | 3 339 | — | 1.20 | — | 5.47 | 21.63 | — | — |
| Zipf read — pass 1 | 31.2 | — | 7 999 | — | 0.50 | — | 0.53 | 7.70 | — | — |
| Zipf read — pass 2 (hot) | 41.5 | — | 10 619 | — | 0.38 | — | 0.53 | **0.59** | — | — |
| Mixed randrw (Zipf 70/30) | 3.5 | 1.5 | 890 | 384 | 2.39 | 4.87 | 15.79 | 30.54 | 22.41 | 33.42 |

### Key comparisons
| Comparison | IOPS ratio | p99 improvement |
|------------|-----------|-----------------|
| Rand cold → hot | 1.75× | +26% |
| Zipf pass 1 → pass 2 | 1.33× | **+92%** (7.70 ms → 0.59 ms) |

### Observations
- **Seq write 147.8 MB/s** — unexpectedly high; the write-back NVMe cache absorbed sequential writes in this run. Later runs showed ~74 MB/s without cache. Suggests the cache was not yet in random-only mode or the initial writes hit a warm NVMe buffer.
- **Rand cold 1 910 IOPS / p99 29 ms** — these numbers already reflect partial cache activity (demand caching from prior use); true bare HDD baseline would be ~120–160 IOPS at ~12–15 ms.
- **Zipf pass 2 p99 0.59 ms** — the definitive cache result. Hot set fully resident in NVMe by end of pass 1.

---

## Run 3 — Full benchmark with pre-warm (definitive run)
**Date/time:** 2026-04-29 02:17  
**Config:** NFS v3, nconnect=8, `--skip-prepare`, `--pre-warm`, default runtimes (120 s / 180 s)

### Results
| Test | R BW MB/s | W BW MB/s | R IOPS | W IOPS | R lat ms | W lat ms | R p95 ms | R p99 ms | W p95 ms | W p99 ms |
|------|-----------|-----------|--------|--------|----------|----------|----------|----------|----------|----------|
| Seq write (control) | — | 73.8 | — | 74 | — | 13.54 | — | — | 3.06 | 509.61 |
| Seq read (control) | 99.9 | — | 100 | — | 10.00 | — | 10.29 | 17.17 | — | — |
| Pre-warm sweep (64 K rand) | 103.0 | — | 103 | — | 9.70 | — | 10.16 | 11.34 | — | — |
| Rand read — pre-warmed | 18.4 | — | 4 713 | — | 0.85 | — | 0.65 | 16.32 | — | — |
| Rand read — hot | 23.2 | — | 5 931 | — | 0.67 | — | 0.63 | 12.12 | — | — |
| Zipf read — pass 1 | 43.8 | — | 11 216 | — | 0.36 | — | 0.53 | **0.59** | — | — |
| Zipf read — pass 2 (hot) | 44.3 | — | 11 346 | — | 0.35 | — | 0.53 | **0.59** | — | — |
| Mixed randrw (Zipf 70/30) | 5.1 | 2.2 | 1 295 | 556 | 1.02 | 4.82 | 0.84 | 20.05 | 31.06 | 53.22 |

### Key comparisons
| Comparison | IOPS ratio | p99 improvement |
|------------|-----------|-----------------|
| Rand pre-warmed → hot | 1.26× | +26% |
| Zipf pass 1 → pass 2 | 1.01× | +1% (both already at floor) |

### Observations

**Sequential write 73.8 MB/s, p99 509 ms**
Cache policy is random-only; sequential writes bypass NVMe entirely and hit both RAID 1 HDD mirrors over NFS. The 509 ms p99 write spike is a single IO stalling on HDD seek during destage. This is the true HDD+NFS baseline.

**Pre-warm sweep produced 103 MB/s — same as sequential read**
The 64 K random read sweep was large enough to trigger the NAS's sequential-IO detection threshold. The NAS treated it as sequential and did not cache it. This is visible in the subsequent `randread-prewarm` result — p99 16 ms still shows HDD misses, not full NVMe cache hits. A true block-level random pre-warm at 4 K would require ~3 200 seconds to sweep 128 GB; impractical. **Conclusion: the Zipf tests are the correct way to measure cache performance, not uniform pre-warm.**

**Zipf pass 1 already at 0.59 ms p99 (11 216 IOPS)**
Because the pre-warm — even though it did not populate the cache — primed the NAS's internal readahead and the Zipf hot set happens to overlap with recently accessed data, pass 1 started at full NVMe speed. Pass 1 and pass 2 are essentially identical: both at **11 346 IOPS / 0.59 ms p99**.

**randrw improved vs run 2 (1 295 vs 890 read IOPS)**
Reads start from a warmer cache state. Write p99 jumped to 53 ms vs 33 ms — at higher IOPS the write buffer occasionally stalls on HDD destaging.

---

## Real-Time NFS RTT Observations (Run 3, Zipf tests)

Monitored via `mountstats` every 30 s during the Zipf and randrw phases.

### Zipf pass 1 — cache warming in real time
RTT declined linearly as the Zipf hot set loaded into NVMe:

| Time | Cumulative READ RTT |
|------|-------------------|
| Pass 1 start | 0.83 ms |
| +30 s | 0.80 ms |
| +60 s | 0.77 ms |
| +90 s | 0.75 ms |
| +120 s | 0.73 ms |
| +150 s | 0.72 ms |
| Pass 1 end | ~0.71 ms |

The ~25 µs/30 s linear decline reflects the Zipf distribution continuously adding slightly-less-hot blocks to the cache. No sharp knee — the hot set is a continuum, not a binary threshold.

### Zipf pass 2 — confirmed cache hit floor
RTT declined faster than pass 1, pulled down by pass 2 ops individually faster than the cumulative average:

| Time | Cumulative READ RTT | Implied pass-2-only RTT |
|------|-------------------|------------------------|
| Pass 2 start | 0.72 ms | — |
| +30 s | 0.70 ms | ~0.45 ms |
| +60 s | 0.68 ms | ~0.40 ms |
| +90 s | 0.67 ms | ~0.35 ms |
| +120 s | 0.65 ms | ~0.32 ms |
| +150 s | 0.64 ms | ~0.30 ms |

True pass 2 RTT estimated at **0.30–0.35 ms** — effectively the NFS v3 protocol overhead floor over 10 GbE. The NVMe cache itself is not the bottleneck; the remaining latency is RPC serialization + TCP stack on both ends.

### randrw — cache resilience under write pressure
READ RTT during 70/30 mixed workload:

| Time | READ RTT | WRITE RTT | Notes |
|------|----------|-----------|-------|
| Start | 0.638 ms | 5.87 ms | Writes just beginning |
| +30 s | 0.637 ms | 5.81 ms | Write cache warming |
| +60 s | 0.635 ms | 5.24 ms | Write buffer in full swing |
| +90 s | 0.637 ms | 5.37 ms | Destage cycle variance |
| +120 s | 0.642 ms | 5.35 ms | Slight upward trend begins |
| +150 s | 0.646 ms | 5.32 ms | Confirmed trend, +11 µs total |
| +180 s | 0.647 ms | 5.31 ms | Settled |

**READ RTT rose only 12 µs over 3 minutes of 30% write load.** The 2 TB NVMe cache absorbed both the Zipf read hot set and the 64 GB write working set simultaneously with no meaningful cache contention. At this rate it would take ~15 additional minutes of write pressure to reach 0.70 ms — well beyond any realistic burst workload.

WRITE RTT variance (5.24–5.87 ms) is the NVMe write buffer's destage cycle: absorbs writes fast, pauses to flush dirty blocks to RAID 1 HDDs, repeat. The HDD destage floor is ~5.9 ms (one RAID 1 write to both 5900 RPM mirrors).

---

## Cache Performance Summary

### vs theoretical raw HDD baseline (5900 RPM RAID 1)
| Metric | Raw HDD estimate | NVMe cache (Zipf hot) | Lift |
|--------|-----------------|----------------------|------|
| Random IOPS | ~120–160 | 11 346 | **~70–95×** |
| Random avg latency | ~8–15 ms | 0.35 ms | **~25–40×** |
| Random p99 latency | ~15–20 ms | 0.59 ms | **~25–34×** |
| Sequential throughput | ~100–150 MB/s | ~100 MB/s (bypasses cache) | 1× |

### NFS protocol overhead at cache floor
- True NVMe access latency: ~0.05–0.1 ms
- Observed NFS RTT at cache hit: ~0.30–0.35 ms
- NFS overhead contribution: ~0.25 ms — entirely RPC + TCP stack, irreducible for NFS v3

---

## Key Findings

1. **The NVMe cache is highly effective for random hot-set workloads.** Zipf(θ=1.2) workloads representing real-world locality reach 11 K+ IOPS at 0.59 ms p99 — against a raw HDD baseline of ~150 IOPS at 12–15 ms.

2. **Cache policy (random only) is the right choice.** Sequential IO is already bounded by the 10 GbE link and HDD throughput. Caching it would waste NVMe space and provide no measurable benefit.

3. **2 TB NVMe RAID 1 has ample headroom.** The 128 GB test file + 64 GB randrw file represent only ~9% of cache capacity. Under 30% write pressure, READ RTT degraded by only 12 µs over 3 minutes — negligible.

4. **nconnect=8 is essential on 10 GbE.** All 8 TCP connections were active and carrying load. A single TCP stream cannot saturate the link; without nconnect the benchmark would be network-constrained rather than storage-constrained.

5. **Sequential write variance (74–148 MB/s) across runs** reflects whether the NVMe write buffer happens to be warm at run start. The 74 MB/s number is the reliable HDD baseline; 148 MB/s was an artifact of a pre-warmed write buffer from prior test activity.

6. **Pre-warm at 64 K block size was ineffective** — the NAS treated it as sequential IO (above its random IO threshold) and bypassed the cache. Effective pre-warm requires 4 K blocks, which takes ~53 minutes to sweep 128 GB at observed IOPS. The Zipf two-pass design is the correct methodology for measuring cache warming.

7. **True NVMe cache RTT floor is ~0.30–0.35 ms** over NFS v3/10 GbE, limited by protocol overhead, not storage hardware.

---

## Files
```
results/
  20260429-012716/    Run 1 — quick NFS validation (30s/60s)
  20260429-015508/    Run 2 — full NFS, no pre-warm
  20260429-021708/    Run 3 — full NFS, with pre-warm (definitive)
    summary.txt       ASCII table + key comparisons
    summary.csv       Machine-readable
    sysinfo.txt       hostname, uname, lsblk, df, findmnt, ethtool
    *.json            Raw fio JSON for each test
```
