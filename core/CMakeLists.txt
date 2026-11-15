cmake_minimum_required(VERSION 3.25)
project(bookcore VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
# The static library is linked into a Python extension module.
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(BOOKCORE_TESTS "Build the GoogleTest suite" ${PROJECT_IS_TOP_LEVEL})
option(BOOKCORE_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

if(MSVC)
  set(BOOKCORE_WARNINGS /W4 /WX)
else()
  set(BOOKCORE_WARNINGS -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion -Werror)
endif()

if(BOOKCORE_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

add_library(bookcore STATIC src/version.cpp)
target_include_directories(bookcore PUBLIC include)
target_compile_options(bookcore PRIVATE ${BOOKCORE_WARNINGS})
target_compile_definitions(bookcore PRIVATE BOOKCORE_VERSION="${PROJECT_VERSION}")

if(BOOKCORE_TESTS)
  include(FetchContent)
  FetchContent_Declare(
    googletest
    GIT_REPOSITORY https://github.com/google/googletest.git
    GIT_TAG v1.15.2
    GIT_SHALLOW TRUE
    SYSTEM)
  set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
  set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
  FetchContent_MakeAvailable(googletest)

  enable_testing()
  add_executable(bookcore_tests tests/test_version.cpp)
  target_link_libraries(bookcore_tests PRIVATE bookcore GTest::gtest_main)
  target_compile_options(bookcore_tests PRIVATE ${BOOKCORE_WARNINGS})
  include(GoogleTest)
  gtest_discover_tests(bookcore_tests)
endif()

# Set by scikit-build-core when pip/uv builds the Python package.
if(SKBUILD)
  find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_bookcore MODULE src/module.cpp)
  target_link_libraries(_bookcore PRIVATE bookcore)
  install(TARGETS _bookcore DESTINATION bookcore)
endif()
