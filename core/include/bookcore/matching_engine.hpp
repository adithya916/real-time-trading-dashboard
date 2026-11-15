#pragma once

#include <cstddef>
#include <cstdint>
#include <functional>
#include <list>
#include <map>
#include <optional>
#include <unordered_map>
#include <vector>

#include "bookcore/l2book.hpp"

namespace bookcore {

using OrderId = std::uint64_t;

enum class OrderType : std::uint8_t {
  Limit = 1,   // trade what crosses, rest the remainder
  Market = 2,  // trade at any price, cancel what is left
  Ioc = 3,     // trade what crosses the limit, cancel what is left
};

enum class EventType : std::uint8_t { Accepted = 1, Trade = 2, Cancelled = 3, Rejected = 4 };

enum class RejectReason : std::uint8_t {
  None = 0,
  DuplicateId = 1,
  InvalidSize = 2,
  InvalidPrice = 3,
  UnknownOrder = 4,
};

// One thing that happened. Every event gets the next sequence number, so replaying the same
// inputs always produces the same events.
//
//   Accepted   order, side, price, size (as submitted)
//   Trade      order is the taker, maker the resting order; price is the maker's; size traded
//   Cancelled  size is what was left when the order was cancelled
//   Rejected   reason says why; nothing changed
struct Event {
  std::uint64_t seq;
  EventType type;
  OrderId order;
  OrderId maker;
  Side side;
  std::int64_t price;
  std::int64_t size;
  RejectReason reason;

  friend bool operator==(const Event&, const Event&) = default;
};

// A single-threaded limit order book with price-time priority.
//
// Each side is a sorted map of price levels, each level a FIFO queue of resting orders.
// An index from order id to its queue position makes cancels O(1).
class MatchingEngine {
 public:
  std::vector<Event> submit(OrderId id, Side side, OrderType type, std::int64_t price,
                            std::int64_t size);
  std::vector<Event> cancel(OrderId id);

  // Aggregated size per price, best first: the L2 view of the L3 book.
  [[nodiscard]] std::vector<Level> depth(Side side, std::size_t n) const;
  [[nodiscard]] std::optional<Level> best(Side side) const;
  [[nodiscard]] std::size_t resting_orders() const noexcept { return index_.size(); }
  [[nodiscard]] std::uint64_t last_seq() const noexcept { return seq_; }

 private:
  struct Order {
    OrderId id;
    std::int64_t remaining;
  };
  struct PriceLevel {
    std::int64_t total = 0;
    std::list<Order> queue;
  };
  using Bids = std::map<std::int64_t, PriceLevel, std::greater<>>;  // best (highest) first
  using Asks = std::map<std::int64_t, PriceLevel, std::less<>>;     // best (lowest) first
  struct Locator {
    Side side;
    std::int64_t price;
    std::list<Order>::iterator position;
  };

  Event make(EventType type, OrderId order, Side side, std::int64_t price, std::int64_t size,
             OrderId maker = 0, RejectReason reason = RejectReason::None);

  template <typename Book>
  std::int64_t match(Book& opposite, OrderId taker, Side taker_side, OrderType type,
                     std::int64_t limit, std::int64_t quantity, std::vector<Event>& events);

  template <typename Book>
  void rest(Book& own, Side side, OrderId id, std::int64_t price, std::int64_t quantity);

  Bids bids_;
  Asks asks_;
  std::unordered_map<OrderId, Locator> index_;
  std::uint64_t seq_ = 0;
};

}  // namespace bookcore
