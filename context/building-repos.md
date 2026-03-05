# Building Repos

## OTC.Relay

C++23 project using CMake presets. Two commands:

```bash
cmake --preset default
cmake --build build --parallel
```

That's it. Preset uses Ninja, Release mode. FetchContent
pulls all C++ deps (libzmq, crow, spdlog, etc.) automatically.
System packages needed: `libssl-dev`, `libgstreamer1.0-dev`,
`ninja-build`.

Sibling repos (OTC.SDK, OTC.SDK.Server, OTC.SDK.View) are
resolved via `../<repo>` relative paths by CMake.

### Tests

```bash
ctest --output-on-failure -j --test-dir build/tests
```

### UI (React)

```bash
cd ui && npm install && npm run build
```

### Common mistakes

- Do NOT run cmake without `--preset default`. The preset
  sets the generator (Ninja), build type, and output dir.
- Do NOT try to install deps manually — FetchContent handles
  everything except system packages.
- Build dir is `build/` (set by preset). Do not use a
  different directory.
