cmake_minimum_required(VERSION 3.25)
project(takt VERSION 0.0.1 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 23)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# Pull in einheit-ui as a library — skip its binary,
# adapters, and tests.
set(EINHEIT_UI_BUILD_BINARY OFF CACHE BOOL "" FORCE)
set(EINHEIT_UI_BUILD_ADAPTERS OFF CACHE BOOL "" FORCE)
set(EINHEIT_UI_BUILD_TESTS OFF CACHE BOOL "" FORCE)

include(FetchContent)

FetchContent_Declare(inja
  GIT_REPOSITORY https://github.com/pantor/inja.git
  GIT_TAG v3.5.0
  GIT_SHALLOW TRUE
  PATCH_COMMAND ${CMAKE_COMMAND} -E env
    sed -i "s/install(TARGETS nlohmann_json EXPORT injaTargets)/# patched out/" CMakeLists.txt
  OVERRIDE_FIND_PACKAGE
)

FetchContent_Declare(einheit-ui
  GIT_REPOSITORY /home/karl/dev/root/ui.git
  GIT_TAG master
)
FetchContent_MakeAvailable(einheit-ui)

# The shell and editor adapters are framework features we
# need. Build them from the fetched source.
add_subdirectory(
  ${einheit-ui_SOURCE_DIR}/adapters/shell
  ${einheit-ui_BINARY_DIR}/adapters/shell
)
add_subdirectory(
  ${einheit-ui_SOURCE_DIR}/adapters/editor
  ${einheit-ui_BINARY_DIR}/adapters/editor
)

# The shell adapter uses PROJECT_SOURCE_DIR-relative
# includes internally. Fix up the path when building
# out-of-tree.
target_include_directories(einheit_ui_adapter_shell
  PRIVATE ${einheit-ui_SOURCE_DIR}
)

# Pull in einheit-cli as a library.
set(EINHEIT_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(EINHEIT_BUILD_ADAPTERS OFF CACHE BOOL "" FORCE)

FetchContent_Declare(einheit-cli
  GIT_REPOSITORY /home/karl/dev/root/cli.git
  GIT_TAG main
)
FetchContent_MakeAvailable(einheit-cli)

# takt UI adapter — lives here, not in the ui repo.
add_subdirectory(adapters/ui)


# takt CLI adapter.
add_subdirectory(adapters/cli)

# takt-ui binary.
add_executable(takt-ui
  binaries/takt-ui/src/main.cc
)
target_link_libraries(takt-ui
  PRIVATE
    takt_ui_adapter
    einheit_ui_adapter_shell
    einheit_ui_adapter_editor
    einheit_ui
    CLI11::CLI11
)
target_compile_definitions(takt-ui PRIVATE
  EINHEIT_UI_DEV_TEMPLATES_DIR="${einheit-ui_SOURCE_DIR}/templates"
  EINHEIT_UI_DEV_ASSETS_DIR="${einheit-ui_SOURCE_DIR}/assets"
  EINHEIT_UI_TEMPLATE_HOT_RELOAD
)

# takt-cli binary.
add_executable(takt-cli
  binaries/takt-cli/src/main.cc
)
target_link_libraries(takt-cli
  PRIVATE
    takt_cli_adapter
    einheit_cli
    CLI11::CLI11
)
