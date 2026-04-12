cmake_minimum_required(VERSION 3.16)
project(sdr_core LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(Threads REQUIRED)
find_package(PkgConfig REQUIRED)
pkg_check_modules(FFTW3F REQUIRED IMPORTED_TARGET fftw3f)

add_executable(sdr_core
    main.cc
)

target_include_directories(sdr_core PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(sdr_core PRIVATE Threads::Threads PkgConfig::FFTW3F)
