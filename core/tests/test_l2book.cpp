#include <gtest/gtest.h>

#include <stdexcept>
#include <vector>

#include "bookcore/l2book.hpp"

using bookcore::kraken_checksum;
using bookcore::L2Book;
using bookcore::Level;
using bookcore::Side;

namespace {

std::vector<std::int64_t> prices(const std::vector<Level>& levels) {
  std::vector<std::int64_t> out;
  for (const auto& level : levels) {
    out.push_back(level.price);
  }
  return out;
}

}  // namespace

TEST(L2Book, InsertsUpdatesAndDeletesLevels) {
  L2Book book;
  book.apply(Side::Bid, 100, 5);
  book.apply(Side::Bid, 101, 7);
  book.apply(Side::Ask, 103, 2);
  book.apply(Side::Bid, 100, 9);
  book.apply(Side::Bid, 101, 0);

  EXPECT_EQ(book.top(Side::Bid, 10), (std::vector<Level>{{100, 9}}));
  EXPECT_EQ(book.best(Side::Ask), (Level{103, 2}));
  EXPECT_EQ(book.size(), 2U);
}

TEST(L2Book, RemovingAMissingLevelIsANoOp) {
  L2Book book;
  book.apply(Side::Ask, 105, 0);
  EXPECT_EQ(book.size(), 0U);
  EXPECT_FALSE(book.best(Side::Ask).has_value());
}

TEST(L2Book, TopListsEachSideBestFirst) {
  L2Book book;
  for (std::int64_t price : {98, 100, 99}) {
    book.apply(Side::Bid, price, 1);
  }
  for (std::int64_t price : {103, 101, 102}) {
    book.apply(Side::Ask, price, 1);
  }

  EXPECT_EQ(prices(book.top(Side::Bid, 10)), (std::vector<std::int64_t>{100, 99, 98}));
  EXPECT_EQ(prices(book.top(Side::Ask, 2)), (std::vector<std::int64_t>{101, 102}));
  EXPECT_EQ(book.top(Side::Ask, 0).size(), 0U);
}

TEST(L2Book, TruncateKeepsTheBestLevels) {
  L2Book book;
  for (std::int64_t price = 90; price < 100; ++price) {
    book.apply(Side::Bid, price, 1);
  }
  for (std::int64_t price = 101; price < 111; ++price) {
    book.apply(Side::Ask, price, 1);
  }
  book.truncate(3);

  EXPECT_EQ(prices(book.top(Side::Bid, 10)), (std::vector<std::int64_t>{99, 98, 97}));
  EXPECT_EQ(prices(book.top(Side::Ask, 10)), (std::vector<std::int64_t>{101, 102, 103}));
  book.truncate(10);  // already shallower: nothing changes
  EXPECT_EQ(book.size(), 6U);
}

TEST(L2Book, DetectsACrossedBook) {
  L2Book book;
  book.apply(Side::Bid, 100, 1);
  book.apply(Side::Ask, 101, 1);
  EXPECT_FALSE(book.crossed());
  book.apply(Side::Bid, 101, 1);
  EXPECT_TRUE(book.crossed());
}

TEST(L2Book, RejectsNegativeSizes) {
  L2Book book;
  EXPECT_THROW(book.apply(Side::Bid, 100, -1), std::invalid_argument);
}

TEST(L2Book, ClearEmptiesBothSides) {
  L2Book book;
  book.apply(Side::Bid, 100, 1);
  book.apply(Side::Ask, 101, 1);
  book.clear();
  EXPECT_EQ(book.size(), 0U);
}

TEST(Crc32, MatchesZlib) {
  // zlib.crc32(b"123456789") == 0xCBF43926, the standard check value.
  EXPECT_EQ(bookcore::crc32("123456789", 9), 0xCBF43926U);
}

TEST(KrakenChecksum, IsAsksThenBidsAsDigits) {
  L2Book book;
  book.apply(Side::Bid, 100, 5);
  book.apply(Side::Bid, 99, 7);
  book.apply(Side::Ask, 101, 2);
  book.apply(Side::Ask, 102, 9);
  // zlib.crc32(b"101210291005997")
  EXPECT_EQ(kraken_checksum(book), 3251632201U);
}

TEST(KrakenChecksum, MatchesKrakenFormattingForScaledValues) {
  // BTC/USD: price 67412.5 at 1 dp is 674125 ticks; qty 0.84210000 at 8 dp is 84210000 lots.
  // Kraken's digits for those are "674125" and "84210000", the tick values themselves.
  L2Book book;
  book.apply(Side::Ask, 674125, 84210000);
  book.apply(Side::Bid, 674120, 12000000);
  // zlib.crc32(b"6741258421000067412012000000")
  EXPECT_EQ(kraken_checksum(book), 2189439602U);
}
