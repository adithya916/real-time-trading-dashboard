# Baseline: legacy stack

Machine: GitHub Actions ubuntu-latest Â· Linux-7.0.0-1019-azure-x86_64-with-glibc2.43 Â· 4 CPUs Â· Python 3.12.16 Â· commit 9310bb5

| Metric | Value |
| --- | --- |
| Pipeline throughput, all symbols | 39,242 ticks/s |
| Generation â†’ Redis subscriber latency p50 / p99 | 2.2 / 16.3 ms |
| One viewer on AAPL: messages received | 6,277 /s |
| One viewer: bytes received | 3,173 KiB/s |
| One viewer: generation â†’ browser latency p50 / p99 | 2.1 / 7.4 ms |
| One viewer: messages the current UI draws | 10 /s |

## Viewer ramp (stops when p99 passes 100 ms)

| Viewers | Messages/s per viewer (median) | All viewers, messages/s | p50 ms | p99 ms | Errors |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 6,239 | 6,239 | 2.1 | 5.8 | 0 |
| 2 | 4,776 | 9,603 | 2.6 | 9.2 | 0 |
| 5 | 2,799 | 13,990 | 4.9 | 13.9 | 0 |
| 10 | 1,563 | 15,654 | 10.8 | 37.0 | 0 |
| 25 | 751 | 18,742 | 22.6 | 61.0 | 0 |
| 50 | 371 | 18,568 | 68.0 | 131.9 | 0 |

p99 first passed the limit at **50 viewers**.

## Method and caveats

- Each phase discards a 2 s warm-up, then measures for 30 s (10 s per ramp step).
- Latency is receive time minus the tick's generation timestamp. Generator, server and load generator share one machine, so they share a clock, and they also share CPU.
- Viewers parse 1 message in 10 for latency and count all of them.
- Ramp viewers are spread over 3 client processes and all five symbols.
- The Python load generator can saturate before the server does; Day 7 replaces it with a Go client that corrects for coordinated omission.
