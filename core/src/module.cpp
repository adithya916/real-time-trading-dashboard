// Python bindings for bookcore. Built only by scikit-build-core (see CMakeLists.txt).
#include <pybind11/pybind11.h>

#include <string>

#include "bookcore/version.hpp"

namespace py = pybind11;

PYBIND11_MODULE(_bookcore, m) {
  m.doc() = "C++ L2 order book and matching engine";
  m.def("version", [] { return std::string(bookcore::version()); }, "Library version.");
}
