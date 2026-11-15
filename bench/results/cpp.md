# C++ core benchmarks

Machine: GitHub Actions ubuntu-latest · Linux-7.0.0-1016-aws-x86_64-with-glibc2.43 · 4 CPUs · Python 3.12.16

## C++ alone (Google Benchmark, seeded synthetic streams)

| Benchmark | Throughput | Time per run |
| --- | ---: | ---: |
| BM_L2BookApply | 32.43 M/s | 30.8 ms |
| BM_KrakenChecksum | 1.05 M/s | 951.7 ns |
| BM_MatchingEngine | 9.36 M/s | 53.4 ms |

## Recorded updates applied from Python

| Stream | Messages | Updates | PyBook | C++, one call per update | C++, one call per message |
| --- | ---: | ---: | ---: | ---: | ---: |
| Kraken BTC/USD | 2,131 | 2,819 | 13.37 M/s | 5.25 M/s (0.4×) | 5.63 M/s (0.4×) |
| Binance BTCUSDT | 178 | 8,517 | 11.03 M/s | 4.09 M/s (0.4×) | 7.89 M/s (0.7×) |

## Method

- Google Benchmark builds each input up front, so the timed loop is only the book or engine.
  `BM_L2BookApply`: 1M updates, mostly within a few levels of the top, a quarter deletes.
  `BM_MatchingEngine`: 500k actions, 70% limit, 10% market or IOC, 20% cancels.
- The recorded comparison drives each book from Python. One call per update pays the cost
  of entering C++ on every level, which is more than a Python dict update costs, so it is
  slower than PyBook. `apply_many` crosses once per message; Kraken messages carry one or
  two levels, so batching helps little there, while Binance messages carry dozens.
- Release build (-O2/-O3 per the compiler's CMake defaults); single thread.
