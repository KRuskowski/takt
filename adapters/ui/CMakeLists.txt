add_library(takt_ui_adapter STATIC
  src/adapter.cc
  src/takt_client.cc
)

target_include_directories(takt_ui_adapter
  PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
)

target_link_libraries(takt_ui_adapter
  PUBLIC
    einheit_ui
    httplib::httplib
    util
)

target_compile_definitions(takt_ui_adapter
  PRIVATE
    EINHEIT_UI_ADAPTER_TAKT_TEMPLATES_DIR="${CMAKE_CURRENT_SOURCE_DIR}/templates"
)
