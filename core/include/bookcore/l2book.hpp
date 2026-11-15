#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace bookcore {

// Values match marketdata.v1 Side, so they cross the binding unchanged.
enum class Side : std::uint8_t { Bid = 1, Ask = 2 };

// Integer ticks and lots, the same units as marketdata.v1.
struct Level {
  std::int64_t price;
  std::int64_t size;

  friend bool operator==(const Level&, const Level&) = default;
};

// An L2 order book: one aggregated size per price level.
//
// Each side is a sorted contiguous array stored worst price first, so the best price is
// at the back. Almost every update lands near the top of the book, which makes inserts
// and erases there move only a few elements; lookups are binary searches.
class L2Book {
 public:
  // Set a level's size. Size 0 removes the level; removing a missing level is a no-op.
  // Throws std::invalid_argument on a negative size.
  void apply(Side side, std::int64_t price, std::int64_t size);

  void clear() noexcept;

  // Best bid (highest) or best ask (lowest), if the side has any levels.
  [[nodiscard]] std::optional<Level> best(Side side) const noexcept;

  // Up to n levels, best first.
  [[nodiscard]] std::vector<Level> top(Side side, std::size_t n) const;

  // Best bid at or above best ask, which a correct book never shows.
  [[nodiscard]] bool crossed() const noexcept;

  // Keep only the best `depth` levels per side.
  void truncate(std::size_t depth) noexcept;

  [[nodiscard]] std::size_t depth(Side side) const noexcept;
  [[nodiscard]] std::size_t size() const noexcept { return bids_.size() + asks_.size(); }

 private:
  [[nodiscard]] std::vector<Level>& levels(Side side) noexcept {
    return side == Side::Bid ? bids_ : asks_;
  }
  [[nodiscard]] const std::vector<Level>& levels(Side side) const noexcept {
    return side == Side::Bid ? bids_ : asks_;
  }

  std::vector<Level> bids_;  // ascending price: best bid at the back
  std::vector<Level> asks_;  // descending price: best ask at the back
};

// Kraken's book checksum: CRC32 over the top 10 asks then the top 10 bids, each price and
// size written as decimal digits. Kraken strips the decimal point and leading zeros from
// the fixed-precision strings, which leaves exactly the integer tick and lot values, so no
// scales are needed.
[[nodiscard]] std::uint32_t kraken_checksum(const L2Book& book);

[[nodiscard]] std::uint32_t crc32(const char* data, std::size_t length) noexcept;

}  // namespace bookcore
