/// @file main.cc
/// @brief takt CLI entry point. Interactive shell for workspace,
/// target, pipeline, and agent management via the takt REST API.
// Copyright (c) 2026 Einheit Networks

#include <cstdlib>
#include <filesystem>
#include <format>
#include <iostream>
#include <memory>
#include <string>

#include <sys/wait.h>
#include <unistd.h>

#include <CLI/CLI.hpp>

#include "adapters/takt/adapter.h"
#include "einheit/cli/command_tree.h"
#include "einheit/cli/globals.h"
#include "einheit/cli/learning_daemon.h"
#include "einheit/cli/render/terminal_caps.h"
#include "einheit/cli/render/theme.h"
#include "einheit/cli/shell.h"
#include "einheit/cli/transport/zmq_local.h"

namespace {

auto ResolveTaktDir() -> std::string {
  auto self = std::filesystem::read_symlink(
      "/proc/self/exe");
  return self.parent_path().parent_path().string();
}

auto RunAgent() -> int {
  auto takt_dir = ResolveTaktDir();
  auto script = takt_dir + "/bin/takt_agent.py";
  auto venv_py = takt_dir + "/.venv/bin/python3";

  pid_t pid = ::fork();
  if (pid < 0) return 1;
  if (pid == 0) {
    int max_fd = static_cast<int>(
        ::sysconf(_SC_OPEN_MAX));
    if (max_fd < 0) max_fd = 1024;
    for (int fd = STDERR_FILENO + 1;
         fd < max_fd; ++fd)
      ::close(fd);
    ::chdir(takt_dir.c_str());
    ::execlp(venv_py.c_str(), venv_py.c_str(),
             script.c_str(), nullptr);
    ::_exit(127);
  }
  int status = 0;
  ::waitpid(pid, &status, 0);
  return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}

}  // namespace

auto main(int argc, char **argv) -> int {
  CLI::App app{"takt — pipeline orchestration CLI"};
  std::string color = "auto";
  bool ascii = false;
  int width = 0;
  bool force_dark = false;
  bool force_light = false;
  std::string takt_url = "http://127.0.0.1:7433";

  app.add_option("--color", color, "always|never|auto");
  app.add_flag("--ascii", ascii, "Force ASCII borders");
  app.add_option("--width", width, "Override width");
  app.add_flag("--dark", force_dark, "Force dark palette");
  app.add_flag("--light", force_light, "Force light palette");
  app.add_option("--takt-url", takt_url,
    "takt REST API base URL");

  try {
    app.parse(argc, argv);
  } catch (const CLI::ParseError &e) {
    return app.exit(e);
  }

  using namespace einheit::cli;

  render::CapOverrides ov;
  ov.color = (color == "always" ? 1
              : color == "never" ? 0 : -1);
  ov.force_ascii = ascii;
  ov.width = static_cast<std::uint16_t>(width);
  const auto caps = render::ApplyOverrides(
      render::DetectTerminal(), ov);

  einheit::adapters::takt::TaktCliConfig cfg;
  cfg.api_url = takt_url;
  auto adapter =
      einheit::adapters::takt::NewTaktAdapter(
          std::move(cfg));

  // takt commands don't go over ZMQ — they call the REST
  // API directly. Use learning mode with a stub daemon so
  // the shell's dispatch loop has a transport to talk to.
  std::shared_ptr<const schema::Schema> schema(
      &adapter->GetSchema(),
      [](const schema::Schema *) {});
  auto learn_daemon =
      std::make_unique<learning::LearningDaemon>(
          nullptr, schema);

  transport::ZmqLocalConfig tcfg;
  tcfg.control_endpoint =
      learn_daemon->ControlEndpoint();
  tcfg.event_endpoint =
      learn_daemon->EventEndpoint();
  auto tx = transport::NewZmqLocalTransport(tcfg);
  if (!tx || !(*tx)->Connect()) {
    std::cerr << "transport setup failed\n";
    return 1;
  }

  const bool prefer_light =
      force_light ||
      (!force_dark && render::DetectLightTerminal());

  ::setenv("EINHEIT_BRAND", "takt", 0);
  auto logo_path = ResolveTaktDir() + "/assets/banner.txt";
  ::setenv("EINHEIT_LOGO_PATH", logo_path.c_str(), 0);

  shell::Shell s;
  s.tx = std::move(*tx);
  s.caps = caps;
  s.learning_mode = false;
  s.theme = render::PickTheme(caps, prefer_light);

  (void)RegisterGlobals(s.tree);
  for (auto &spec : adapter->Commands()) {
    (void)Register(s.tree, std::move(spec));
  }
  s.adapter = std::move(adapter);
  s.caller.user = "operator";
  s.caller.role = RoleGate::AdminOnly;

  const auto leftovers = app.remaining();
  if (!leftovers.empty()) {
    if (leftovers[0] == "agent") {
      return RunAgent();
    }
    auto r = shell::RunOneshot(s, leftovers);
    if (!r) {
      std::cerr << std::format("oneshot: {}\n",
                               r.error().message);
      return 1;
    }
    return 0;
  }

  auto rc = RunShell(s);
  if (!rc) {
    std::cerr << std::format("shell: {}\n",
                             rc.error().message);
    return 1;
  }
  return 0;
}
