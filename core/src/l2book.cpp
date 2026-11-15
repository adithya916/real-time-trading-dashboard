#include "bookcore/l2book.hpp"

#include <algorithm>
#include <array>
#include <charconv>
#include <stdexcept>
#include <string>

namespace bookcore {

namespace {

// Position of `price` in a side, or where it would be inserted. Bids are ascending and
// asks descending, so both sides keep their best price at the back.
std::vector<Level>::iterator find(std::vector<Level>& levels, Side side, std::int64_t price) {
  if (side == Side::Bid) {
    return std::lower_bound(levels.begin(), levels.end(), price,
                            [](const Level& level, std::int64_t p) { return level.price < p; });
  }
  return std::lower_bound(levels.begin(), levels.end(), price,
                          [](const Level& level, std::int64_t p) { return level.price > p; });
}

constexpr std::array<std::uint32_t, 256> make_crc_table() {
  std::array<std::uint32_t, 256> table{};
  for (std::uint32_t i = 0; i < 256; ++i) {
    std::uint32_t c = i;
    for (int bit = 0; bit < 8; ++bit) {
      c = (c & 1U) ? 0xEDB88320U ^ (c >> 1U) : c >> 1U;
    }
    table[i] = c;
  }
  return table;
}

constexpr auto kCrcTable = make_crc_table();
constexpr std::size_t kChecksumLevels = 10;

void append_digits(std::string& out, std::int64_t value) {
  std::array<char, 24> buffer{};
  auto [end, error] = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value);
  (void)error;  // 24 characters always fit an int64
  out.append(buffer.data(), end);
}

}  // namespace

void L2Book::apply(Side side, std::int64_t price, std::int64_t size) {
  if (size < 0) {
    throw std::invalid_argument("negative size " + std::to_string(size) + " at price " +
                                std::to_string(price));
  }
  auto& side_levels = levels(side);
  auto it = find(side_levels, side, price);
  const bool exists = it != side_levels.end() && it->price == price;
  if (size == 0) {
    if (exists) {
      side_levels.erase(it);
    }
  } else if (exists) {
    it->size = size;
  } else {
    side_levels.insert(it, Level{price, size});
  }
}

void L2Book::clear() noexcept {
  bids_.clear();
  asks_.clear();
}

std::optional<Level> L2Book::best(Side side) const noexcept {
  const auto& side_levels = levels(side);
  if (side_levels.empty()) {
    return std::nullopt;
  }
  return side_levels.back();
}

std::vector<Level> L2Book::top(Side side, std::size_t n) const {
  const auto& side_levels = levels(side);
  const std::size_t count = std::min(n, side_levels.size());
  return {side_levels.rbegin(), side_levels.rbegin() + static_cast<std::ptrdiff_t>(count)};
}

bool L2Book::crossed() const noexcept {
  return !bids_.empty() && !asks_.empty() && bids_.back().price >= asks_.back().price;
}

void L2Book::truncate(std::size_t depth) noexcept {
  for (auto* side_levels : {&bids_, &asks_}) {
    if (side_levels->size() > depth) {
      side_levels->erase(side_levels->begin(),
                         side_levels->end() - static_cast<std::ptrdiff_t>(depth));
    }
  }
}

std::size_t L2Book::depth(Side side) const noexcept { return levels(side).size(); }

std::uint32_t crc32(const char* data, std::size_t length) noexcept {
  std::uint32_t crc = 0xFFFFFFFFU;
  for (std::size_t i = 0; i < length; ++i) {
    const auto byte = static_cast<std::uint8_t>(data[i]);
    crc = kCrcTable[(crc ^ byte) & 0xFFU] ^ (crc >> 8U);
  }
  return crc ^ 0xFFFFFFFFU;
}

std::uint32_t kraken_checksum(const L2Book& book) {
  std::string payload;
  payload.reserve(2 * kChecksumLevels * 2 * 20);
  for (Side side : {Side::Ask, Side::Bid}) {
    for (const Level& level : book.top(side, kChecksumLevels)) {
      append_digits(payload, level.price);
      append_digits(payload, level.size);
    }
  }
  return crc32(payload.data(), payload.size());
}

}  // namespace bookcore
