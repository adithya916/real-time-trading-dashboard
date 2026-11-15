// Python bindings for bookcore. Built only by scikit-build-core (see CMakeLists.txt).
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <optional>
#include <string>
#include <tuple>
#include <vector>

#include "bookcore/l2book.hpp"
#include "bookcore/version.hpp"

namespace py = pybind11;
using bookcore::L2Book;
using bookcore::Side;

namespace {

using PyLevel = std::tuple<std::int64_t, std::int64_t>;

std::vector<PyLevel> to_tuples(const std::vector<bookcore::Level>& levels) {
  std::vector<PyLevel> out;
  out.reserve(levels.size());
  for (const auto& level : levels) {
    out.emplace_back(level.price, level.size);
  }
  return out;
}

}  // namespace

PYBIND11_MODULE(_bookcore, m) {
  m.doc() = "C++ L2 order book and matching engine";
  m.def("version", [] { return std::string(bookcore::version()); }, "Library version.");

  py::enum_<Side>(m, "Side").value("BID", Side::Bid).value("ASK", Side::Ask);

  py::class_<L2Book>(m, "L2Book", "L2 order book of integer ticks and lots.")
      .def(py::init<>())
      .def("apply", &L2Book::apply, py::arg("side"), py::arg("price"), py::arg("size"),
           "Set a level's size; size 0 removes it.")
      .def("clear", &L2Book::clear)
      .def(
          "best",
          [](const L2Book& book, Side side) -> std::optional<PyLevel> {
            auto level = book.best(side);
            if (!level) {
              return std::nullopt;
            }
            return PyLevel{level->price, level->size};
          },
          py::arg("side"))
      .def(
          "top",
          [](const L2Book& book, Side side, std::size_t n) { return to_tuples(book.top(side, n)); },
          py::arg("side"), py::arg("n"), "Up to n levels, best first.")
      .def("crossed", &L2Book::crossed)
      .def("truncate", &L2Book::truncate, py::arg("depth"))
      .def("depth", &L2Book::depth, py::arg("side"))
      .def("__len__", &L2Book::size);

  m.def("kraken_checksum", &bookcore::kraken_checksum, py::arg("book"),
        "Kraken's CRC32 of the top 10 asks then bids.");
}
