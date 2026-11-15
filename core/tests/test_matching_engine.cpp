#include <gtest/gtest.h>

#include <cstdint>
#include <random>
#include <vector>

#include "bookcore/matching_engine.hpp"

using bookcore::Event;
using bookcore::EventType;
using bookcore::Level;
using bookcore::MatchingEngine;
using bookcore::OrderType;
using bookcore::RejectReason;
using bookcore::Side;

namespace {

std::vector<Event> trades(const std::vector<Event>& events) {
  std::vector<Event> out;
  for (const auto& event : events) {
    if (event.type == EventType::Trade) {
      out.push_back(event);
    }
  }
  return out;
}

}  // namespace

TEST(MatchingEngine, NonCrossingLimitOrdersRest) {
  MatchingEngine engine;
  engine.submit(1, Side::Bid, OrderType::Limit, 100, 5);
  engine.submit(2, Side::Bid, OrderType::Limit, 100, 3);
  engine.submit(3, Side::Ask, OrderType::Limit, 102, 4);

  EXPECT_EQ(engine.depth(Side::Bid, 10), (std::vector<Level>{{100, 8}}));
  EXPECT_EQ(engine.best(Side::Ask), (Level{102, 4}));
  EXPECT_EQ(engine.resting_orders(), 3U);
}

TEST(MatchingEngine, CrossingLimitTradesAtTheMakersPriceAndRestsTheRest) {
  MatchingEngine engine;
  engine.submit(1, Side::Ask, OrderType::Limit, 101, 3);
  auto events = engine.submit(2, Side::Bid, OrderType::Limit, 105, 5);

  auto fills = trades(events);
  ASSERT_EQ(fills.size(), 1U);
  EXPECT_EQ(fills[0].price, 101);  // the resting order's price, not the taker's limit
  EXPECT_EQ(fills[0].size, 3);
  EXPECT_EQ(fills[0].maker, 1U);
  EXPECT_EQ(engine.best(Side::Bid), (Level{105, 2}));
  EXPECT_FALSE(engine.best(Side::Ask).has_value());
}

TEST(MatchingEngine, EarlierOrdersAtAPriceFillFirst) {
  MatchingEngine engine;
  engine.submit(1, Side::Ask, OrderType::Limit, 101, 2);
  engine.submit(2, Side::Ask, OrderType::Limit, 101, 2);
  engine.submit(3, Side::Ask, OrderType::Limit, 101, 2);
  auto fills = trades(engine.submit(4, Side::Bid, OrderType::Limit, 101, 3));

  ASSERT_EQ(fills.size(), 2U);
  EXPECT_EQ(fills[0].maker, 1U);
  EXPECT_EQ(fills[1].maker, 2U);
  EXPECT_EQ(fills[1].size, 1);
  EXPECT_EQ(engine.best(Side::Ask), (Level{101, 3}));
}

TEST(MatchingEngine, BetterPricesFillBeforeWorseOnes) {
  MatchingEngine engine;
  engine.submit(1, Side::Ask, OrderType::Limit, 103, 1);
  engine.submit(2, Side::Ask, OrderType::Limit, 101, 1);
  engine.submit(3, Side::Ask, OrderType::Limit, 102, 1);
  auto fills = trades(engine.submit(4, Side::Bid, OrderType::Limit, 102, 5));

  ASSERT_EQ(fills.size(), 2U);
  EXPECT_EQ(fills[0].price, 101);
  EXPECT_EQ(fills[1].price, 102);
  EXPECT_EQ(engine.best(Side::Bid), (Level{102, 3}));
  EXPECT_EQ(engine.best(Side::Ask), (Level{103, 1}));
}

TEST(MatchingEngine, MarketOrdersSweepAndCancelWhatIsLeft) {
  MatchingEngine engine;
  engine.submit(1, Side::Bid, OrderType::Limit, 99, 2);
  engine.submit(2, Side::Bid, OrderType::Limit, 98, 2);
  auto events = engine.submit(3, Side::Ask, OrderType::Market, 0, 6);

  EXPECT_EQ(trades(events).size(), 2U);
  ASSERT_EQ(events.back().type, EventType::Cancelled);
  EXPECT_EQ(events.back().size, 2);
  EXPECT_EQ(engine.resting_orders(), 0U);
}

TEST(MatchingEngine, IocOrdersNeverRest) {
  MatchingEngine engine;
  engine.submit(1, Side::Ask, OrderType::Limit, 101, 2);
  engine.submit(2, Side::Ask, OrderType::Limit, 104, 2);
  auto events = engine.submit(3, Side::Bid, OrderType::Ioc, 102, 5);

  EXPECT_EQ(trades(events).size(), 1U);
  EXPECT_EQ(events.back().type, EventType::Cancelled);
  EXPECT_EQ(events.back().size, 3);
  EXPECT_FALSE(engine.best(Side::Bid).has_value());
  EXPECT_EQ(engine.best(Side::Ask), (Level{104, 2}));
}

TEST(MatchingEngine, CancelRemovesARestingOrder) {
  MatchingEngine engine;
  engine.submit(1, Side::Bid, OrderType::Limit, 100, 5);
  engine.submit(2, Side::Bid, OrderType::Limit, 100, 3);
  auto events = engine.cancel(1);

  ASSERT_EQ(events.size(), 1U);
  EXPECT_EQ(events[0].type, EventType::Cancelled);
  EXPECT_EQ(events[0].size, 5);
  EXPECT_EQ(engine.depth(Side::Bid, 10), (std::vector<Level>{{100, 3}}));
  engine.cancel(2);
  EXPECT_FALSE(engine.best(Side::Bid).has_value());
}

TEST(MatchingEngine, CancelReportsWhatWasLeftAfterAPartialFill) {
  MatchingEngine engine;
  engine.submit(1, Side::Ask, OrderType::Limit, 101, 5);
  engine.submit(2, Side::Bid, OrderType::Limit, 101, 2);
  auto events = engine.cancel(1);
  EXPECT_EQ(events[0].size, 3);
}

TEST(MatchingEngine, RejectsBadInput) {
  MatchingEngine engine;
  engine.submit(1, Side::Bid, OrderType::Limit, 100, 5);

  EXPECT_EQ(engine.submit(1, Side::Bid, OrderType::Limit, 100, 5)[0].reason,
            RejectReason::DuplicateId);
  EXPECT_EQ(engine.submit(2, Side::Bid, OrderType::Limit, 100, 0)[0].reason,
            RejectReason::InvalidSize);
  EXPECT_EQ(engine.submit(3, Side::Bid, OrderType::Limit, 0, 5)[0].reason,
            RejectReason::InvalidPrice);
  EXPECT_EQ(engine.cancel(99)[0].reason, RejectReason::UnknownOrder);
  EXPECT_EQ(engine.resting_orders(), 1U);
}

TEST(MatchingEngine, AFilledOrderCanNoLongerBeCancelled) {
  MatchingEngine engine;
  engine.submit(1, Side::Ask, OrderType::Limit, 101, 2);
  engine.submit(2, Side::Bid, OrderType::Limit, 101, 2);
  EXPECT_EQ(engine.cancel(1)[0].reason, RejectReason::UnknownOrder);
}

namespace {

struct Totals {
  std::int64_t submitted = 0;
  std::int64_t traded = 0;
  std::int64_t cancelled = 0;
};

// A seeded random session; returns every event and checks invariants as it goes.
std::vector<Event> random_session(MatchingEngine& engine, std::uint32_t seed, Totals& totals) {
  std::mt19937 rng(seed);
  std::uniform_int_distribution<int> action(0, 9);
  std::uniform_int_distribution<std::int64_t> price(95, 105);
  std::uniform_int_distribution<std::int64_t> size(1, 20);
  std::vector<Event> all;
  bookcore::OrderId next_id = 1;
  for (int step = 0; step < 5000; ++step) {
    std::vector<Event> events;
    const int roll = action(rng);
    if (roll < 2 && next_id > 1) {
      std::uniform_int_distribution<bookcore::OrderId> pick(1, next_id - 1);
      events = engine.cancel(pick(rng));
    } else {
      const Side side = roll % 2 == 0 ? Side::Bid : Side::Ask;
      const OrderType type = roll == 9   ? OrderType::Market
                             : roll == 8 ? OrderType::Ioc
                                         : OrderType::Limit;
      const std::int64_t quantity = size(rng);
      totals.submitted += quantity;
      events = engine.submit(next_id++, side, type, price(rng), quantity);
    }
    for (const auto& event : events) {
      if (event.type == EventType::Trade) {
        totals.traded += event.size;
      } else if (event.type == EventType::Cancelled) {
        totals.cancelled += event.size;
      }
    }
    auto bid = engine.best(Side::Bid);
    auto ask = engine.best(Side::Ask);
    EXPECT_FALSE(bid && ask && bid->price >= ask->price) << "crossed at step " << step;
    all.insert(all.end(), events.begin(), events.end());
  }
  return all;
}

std::int64_t resting_quantity(const MatchingEngine& engine) {
  std::int64_t total = 0;
  for (Side side : {Side::Bid, Side::Ask}) {
    for (const auto& level : engine.depth(side, 1000)) {
      total += level.size;
    }
  }
  return total;
}

}  // namespace

TEST(MatchingEngine, RandomSessionsConserveQuantityAndNeverCross) {
  for (std::uint32_t seed = 1; seed <= 5; ++seed) {
    MatchingEngine engine;
    Totals totals;
    random_session(engine, seed, totals);
    // Every submitted lot traded twice (both sides), rests, or was cancelled.
    EXPECT_EQ(totals.submitted, 2 * totals.traded + resting_quantity(engine) + totals.cancelled)
        << "seed " << seed;
  }
}

TEST(MatchingEngine, TheSameInputsGiveTheSameEvents) {
  MatchingEngine first;
  MatchingEngine second;
  Totals a;
  Totals b;
  EXPECT_EQ(random_session(first, 42, a), random_session(second, 42, b));
  EXPECT_EQ(first.last_seq(), second.last_seq());
}
