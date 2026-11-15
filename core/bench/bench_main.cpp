// Google Benchmark suite for bookcore. Inputs are generated up front with fixed seeds, so the
// timed loops measure only the book and the engine.
#include <benchmark/benchmark.h>

#include <cstdint>
#include <random>
#include <vector>

#include "bookcore/l2book.hpp"
#include "bookcore/matching_engine.hpp"

namespace {

using bookcore::L2Book;
using bookcore::MatchingEngine;
using bookcore::OrderType;
using bookcore::Side;

struct Update {
  Side side;
  std::int64_t price;
  std::int64_t size;
};

// Shaped like a live L2 feed: most updates land within a few levels of the top, about a
// quarter are deletes, and the mid drifts slowly.
std::vector<Update> make_updates(std::size_t count) {
  std::mt19937_64 rng(7);
  std::geometric_distribution<std::int64_t> offset(0.15);
  std::uniform_int_distribution<std::int64_t> size(1, 1'000'000);
  std::bernoulli_distribution remove(0.25);
  std::bernoulli_distribution bid(0.5);
  std::bernoulli_distribution up(0.5);
  std::int64_t mid = 1'000'000;
  std::vector<Update> updates;
  updates.reserve(count);
  for (std::size_t i = 0; i < count; ++i) {
    if (i % 100 == 0) {
      mid += up(rng) ? 1 : -1;
    }
    const bool is_bid = bid(rng);
    const std::int64_t distance = 1 + offset(rng);
    updates.push_back({is_bid ? Side::Bid : Side::Ask, is_bid ? mid - distance : mid + distance,
                       remove(rng) ? 0 : size(rng)});
  }
  return updates;
}

void BM_L2BookApply(benchmark::State& state) {
  const auto updates = make_updates(1'000'000);
  for (auto _ : state) {
    L2Book book;
    for (const auto& u : updates) {
      book.apply(u.side, u.price, u.size);
    }
    benchmark::DoNotOptimize(book.best(Side::Bid));
  }
  state.SetItemsProcessed(state.iterations() * static_cast<std::int64_t>(updates.size()));
}
BENCHMARK(BM_L2BookApply)->Unit(benchmark::kMillisecond);

void BM_KrakenChecksum(benchmark::State& state) {
  L2Book book;
  for (std::int64_t i = 1; i <= 10; ++i) {
    book.apply(Side::Bid, 674'120 - i, 84'210'000 + i);
    book.apply(Side::Ask, 674'125 + i, 12'000'000 + i);
  }
  for (auto _ : state) {
    benchmark::DoNotOptimize(bookcore::kraken_checksum(book));
  }
  state.SetItemsProcessed(state.iterations());
}
BENCHMARK(BM_KrakenChecksum);

struct Order {
  bool cancel;
  bookcore::OrderId id;
  Side side;
  OrderType type;
  std::int64_t price;
  std::int64_t size;
};

// Mostly limit orders around the mid, some marketable, and cancels of earlier orders.
std::vector<Order> make_orders(std::size_t count) {
  std::mt19937_64 rng(11);
  std::uniform_int_distribution<int> roll(0, 99);
  std::uniform_int_distribution<std::int64_t> offset(-20, 20);
  std::uniform_int_distribution<std::int64_t> size(1, 100);
  std::vector<Order> orders;
  orders.reserve(count);
  bookcore::OrderId next_id = 1;
  for (std::size_t i = 0; i < count; ++i) {
    const int r = roll(rng);
    if (r < 20 && next_id > 1) {
      std::uniform_int_distribution<bookcore::OrderId> pick(1, next_id - 1);
      orders.push_back({true, pick(rng), Side::Bid, OrderType::Limit, 0, 0});
      continue;
    }
    const Side side = r % 2 == 0 ? Side::Bid : Side::Ask;
    const OrderType type = r >= 95   ? OrderType::Market
                           : r >= 90 ? OrderType::Ioc
                                     : OrderType::Limit;
    orders.push_back({false, next_id++, side, type, 1'000 + offset(rng), size(rng)});
  }
  return orders;
}

void BM_MatchingEngine(benchmark::State& state) {
  const auto orders = make_orders(500'000);
  for (auto _ : state) {
    MatchingEngine engine;
    for (const auto& o : orders) {
      if (o.cancel) {
        benchmark::DoNotOptimize(engine.cancel(o.id));
      } else {
        benchmark::DoNotOptimize(engine.submit(o.id, o.side, o.type, o.price, o.size));
      }
    }
  }
  state.SetItemsProcessed(state.iterations() * static_cast<std::int64_t>(orders.size()));
}
BENCHMARK(BM_MatchingEngine)->Unit(benchmark::kMillisecond);

}  // namespace

BENCHMARK_MAIN();
