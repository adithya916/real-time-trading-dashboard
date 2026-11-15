#pragma once

#include <string_view>

namespace bookcore {

// Library version, from the CMake project version.
std::string_view version() noexcept;

}  // namespace bookcore
