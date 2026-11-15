// Python bindings for bookcore. Built only by scikit-build-core (see CMakeLists.txt).
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>
#include <tuple>
#include <vector>

#include "bookcore/l2book.hpp"
#include "bookcore/matching_engine.hpp"
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
      .def(
          "apply_many",
          [](L2Book& book,
             const std::vector<std::tuple<int, std::int64_t, std::int64_t>>& updates) {
            // One call per message instead of one per level: crossing into C++ costs more than
            // a single level update, so batching is what makes the C++ book pay off from Python.
            for (const auto& [side, price, size] : updates) {
              if (side != 1 && side != 2) {
                throw std::invalid_argument("side must be 1 (bid) or 2 (ask)");
              }
              book.apply(static_cast<Side>(side), price, size);
            }
          },
          py::arg("updates"), "Apply (side, price, size) tuples; side 1 is bid, 2 is ask.")
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

  using bookcore::Event;
  using bookcore::EventType;
  using bookcore::MatchingEngine;
  using bookcore::OrderType;
  using bookcore::RejectReason;

  py::enum_<OrderType>(m, "OrderType")
      .value("LIMIT", OrderType::Limit)
      .value("MARKET", OrderType::Market)
      .value("IOC", OrderType::Ioc);
  py::enum_<EventType>(m, "EventType")
      .value("ACCEPTED", EventType::Accepted)
      .value("TRADE", EventType::Trade)
      .value("CANCELLED", EventType::Cancelled)
      .value("REJECTED", EventType::Rejected);
  py::enum_<RejectReason>(m, "RejectReason")
      .value("NONE", RejectReason::None)
      .value("DUPLICATE_ID", RejectReason::DuplicateId)
      .value("INVALID_SIZE", RejectReason::InvalidSize)
      .value("INVALID_PRICE", RejectReason::InvalidPrice)
      .value("UNKNOWN_ORDER", RejectReason::UnknownOrder);

  py::class_<Event>(m, "Event")
      .def_readonly("seq", &Event::seq)
      .def_readonly("type", &Event::type)
      .def_readonly("order", &Event::order)
      .def_readonly("maker", &Event::maker)
      .def_readonly("side", &Event::side)
      .def_readonly("price", &Event::price)
      .def_readonly("size", &Event::size)
      .def_readonly("reason", &Event::reason)
      .def("__repr__", [](const Event& e) {
        return "Event(seq=" + std::to_string(e.seq) +
               ", type=" + std::to_string(static_cast<int>(e.type)) +
               ", order=" + std::to_string(e.order) + ", price=" + std::to_string(e.price) +
               ", size=" + std::to_string(e.size) + ")";
      });

  py::class_<MatchingEngine>(m, "MatchingEngine", "Limit order book with price-time priority.")
      .def(py::init<>())
      .def("submit", &MatchingEngine::submit, py::arg("order"), py::arg("side"), py::arg("type"),
           py::arg("price"), py::arg("size"))
      .def("cancel", &MatchingEngine::cancel, py::arg("order"))
      .def(
          "depth",
          [](const MatchingEngine& engine, Side side, std::size_t n) {
            return to_tuples(engine.depth(side, n));
          },
          py::arg("side"), py::arg("n"), "Aggregated levels, best first.")
      .def(
          "best",
          [](const MatchingEngine& engine, Side side) -> std::optional<PyLevel> {
            auto level = engine.best(side);
            if (!level) {
              return std::nullopt;
            }
            return PyLevel{level->price, level->size};
          },
          py::arg("side"))
      .def("resting_orders", &MatchingEngine::resting_orders)
      .def("last_seq", &MatchingEngine::last_seq);
}
