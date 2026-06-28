add_library(takt_cli_adapter STATIC
  src/adapter.cc
)

target_include_directories(takt_cli_adapter
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
)

target_link_libraries(takt_cli_adapter
  PUBLIC
    einheit_cli
)
