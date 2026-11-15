#include <gtest/gtest.h>

#include "bookcore/version.hpp"

TEST(Version, MatchesTheCmakeProject) { EXPECT_EQ(bookcore::version(), "0.1.0"); }
