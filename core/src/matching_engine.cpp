#include "bookcore/matching_engine.hpp"

#include <algorithm>

namespace bookcore {

Event MatchingEngine::make(EventType type, OrderId order, Side side, std::int64_t price,
                           std::int64_t size, OrderId maker, RejectReason reason) {
  return Event{++seq_, type, order, maker, side, price, size, reason};
}

std::vector<Event> MatchingEngine::submit(OrderId id, Side side, OrderType type, std::int64_t price,
                                          std::int64_t size) {
  std::vector<Event> events;
  RejectReason reason = RejectReason::None;
  if (index_.contains(id)) {
    reason = RejectReason::DuplicateId;
  } else if (size <= 0) {
    reason = RejectReason::InvalidSize;
  } else if (type != OrderType::Market && price <= 0) {
    reason = RejectReason::InvalidPrice;
  }
  if (reason != RejectReason::None) {
    events.push_back(make(EventType::Rejected, id, side, price, size, 0, reason));
    return events;
  }

  events.push_back(make(EventType::Accepted, id, side, price, size));
  const std::int64_t left = side == Side::Bid ? match(asks_, id, side, type, price, size, events)
                                              : match(bids_, id, side, type, price, size, events);
  if (left > 0) {
    if (type == OrderType::Limit) {
      if (side == Side::Bid) {
        rest(bids_, side, id, price, left);
      } else {
        rest(asks_, side, id, price, left);
      }
    } else {
      events.push_back(make(EventType::Cancelled, id, side, price, left));
    }
  }
  return events;
}

template <typename Book>
std::int64_t MatchingEngine::match(Book& opposite, OrderId taker, Side taker_side, OrderType type,
                                   std::int64_t limit, std::int64_t quantity,
                                   std::vector<Event>& events) {
  while (quantity > 0 && !opposite.empty()) {
    auto level_it = opposite.begin();
    const std::int64_t level_price = level_it->first;
    if (type != OrderType::Market) {
      const bool crosses = taker_side == Side::Bid ? level_price <= limit : level_price >= limit;
      if (!crosses) {
        break;
      }
    }
    PriceLevel& level = level_it->second;
    while (quantity > 0 && !level.queue.empty()) {
      Order& maker = level.queue.front();
      const std::int64_t traded = std::min(quantity, maker.remaining);
      events.push_back(make(EventType::Trade, taker, taker_side, level_price, traded, maker.id));
      quantity -= traded;
      maker.remaining -= traded;
      level.total -= traded;
      if (maker.remaining == 0) {
        index_.erase(maker.id);
        level.queue.pop_front();
      }
    }
    if (level.queue.empty()) {
      opposite.erase(level_it);
    }
  }
  return quantity;
}

template <typename Book>
void MatchingEngine::rest(Book& own, Side side, OrderId id, std::int64_t price,
                          std::int64_t quantity) {
  PriceLevel& level = own[price];
  level.total += quantity;
  level.queue.push_back(Order{id, quantity});
  index_.emplace(id, Locator{side, price, std::prev(level.queue.end())});
}

std::vector<Event> MatchingEngine::cancel(OrderId id) {
  std::vector<Event> events;
  auto found = index_.find(id);
  if (found == index_.end()) {
    events.push_back(make(EventType::Rejected, id, Side::Bid, 0, 0, 0, RejectReason::UnknownOrder));
    return events;
  }
  const Locator locator = found->second;
  const std::int64_t remaining = locator.position->remaining;
  auto remove = [&](auto& book) {
    auto level_it = book.find(locator.price);
    level_it->second.total -= remaining;
    level_it->second.queue.erase(locator.position);
    if (level_it->second.queue.empty()) {
      book.erase(level_it);
    }
  };
  if (locator.side == Side::Bid) {
    remove(bids_);
  } else {
    remove(asks_);
  }
  index_.erase(found);
  events.push_back(make(EventType::Cancelled, id, locator.side, locator.price, remaining));
  return events;
}

std::vector<Level> MatchingEngine::depth(Side side, std::size_t n) const {
  std::vector<Level> out;
  auto collect = [&](const auto& book) {
    for (const auto& [price, level] : book) {
      if (out.size() == n) {
        break;
      }
      out.push_back(Level{price, level.total});
    }
  };
  if (side == Side::Bid) {
    collect(bids_);
  } else {
    collect(asks_);
  }
  return out;
}

std::optional<Level> MatchingEngine::best(Side side) const {
  auto top = depth(side, 1);
  if (top.empty()) {
    return std::nullopt;
  }
  return top.front();
}

}  // namespace bookcore
