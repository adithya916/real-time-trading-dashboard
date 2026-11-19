# 004: A C++ order book and matching engine, bound with pybind11

## Context

Order book maintenance and order matching are tight loops over sorted price levels, the part
of a trading system where native code is expected and where reviewers look for it. The rest
of the system (venue I/O, the bus, analytics, the gateway) is I/O-bound glue that is faster to
write and change in Python.

## Decision

`core/` (`bookcore`): C++20, built with CMake, bound to Python with pybind11 and packaged with
scikit-build-core.

- `L2Book`: each side a sorted array stored worst-first, so the best levels (where most
  updates land) sit at the end and inserting or removing them moves few elements; binary
  search for lookups; Kraken's CRC32 checksum computed from the integer ticks.
- `MatchingEngine`: price-time priority over sorted maps of FIFO levels, O(1) cancels through
  an order index, limit / market / IOC orders, sequence-numbered events, deterministic for the
  same inputs.
- Checked against the Python reference book (`PyBook`) on the recorded Kraken and Binance
  sessions and on random update streams; GoogleTest in Release and under ASan + UBSan in CI.
- Used live by the simulator: every simulated order goes through the engine.

## Alternatives

- **Pure Python**: simpler, and (measured) faster than calling C++ per update from Python,
  but the engine work would cap the simulator and the project would show no systems code.
- **Rust with PyO3**: equally valid; C++ matches what most trading firms run.
- **Cython**: keeps one language, but a weaker story for a standalone engine with its own
  tests, sanitizers and benchmarks.

## Consequences

- In C++ alone the book applies 32.4M updates/s and the engine 9.4M actions/s on a GitHub
  runner (`bench/results/cpp.md`).
- Driven from Python one update or one message at a time, the C++ book is slower than PyBook
  (0.4 to 0.7x): crossing into C++ costs more than a dict update. The gain needs whole loops
  in C++ (the simulator runs its agents' orders through the engine; the venue adapters still
  use PyBook). This is reported as measured rather than hidden.
- Services that use it (the simulator) compile it: a build stage with g++ in their image, and
  a compiler on CI runners. The ARM server builds it natively.
