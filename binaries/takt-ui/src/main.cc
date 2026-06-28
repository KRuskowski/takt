/// @file main.cc
/// @brief takt web UI. Builds on einheit-ui framework with the
/// takt adapter, editor, and shell.
// Copyright (c) 2026 Einheit Networks

#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <format>
#include <iostream>
#include <string>

#include <CLI/CLI.hpp>
#include <crow.h>
#include <spdlog/spdlog.h>

#include "einheit/adapters/editor/ui_adapter.h"
#include "einheit/adapters/shell/ui_adapter.h"
#include "einheit/adapters/takt/ui_adapter.h"
#include "einheit/ui/adapter.h"
#include "einheit/ui/render/template_engine.h"
#include "einheit/ui/route.h"
#include "einheit/ui/server.h"
#include "einheit/ui/stream.h"
#include "einheit/ui/theme.h"

namespace {

auto FirstExisting(
    std::initializer_list<const char *> paths)
    -> std::string {
  const char *last = "";
  for (const char *p : paths) {
    if (!p || !*p) continue;
    last = p;
    if (std::filesystem::exists(p)) return p;
  }
  return last;
}

auto ResolveTemplatesDir(const std::string &override)
    -> std::string {
  if (!override.empty()) return override;
  return FirstExisting({
#ifdef EINHEIT_UI_TEMPLATES_DIR
      EINHEIT_UI_TEMPLATES_DIR,
#endif
#ifdef EINHEIT_UI_DEV_TEMPLATES_DIR
      EINHEIT_UI_DEV_TEMPLATES_DIR,
#endif
      "templates",
  });
}

auto ResolveAssetsDir(const std::string &override)
    -> std::string {
  if (!override.empty()) return override;
  return FirstExisting({
#ifdef EINHEIT_UI_INSTALLED_ASSETS_DIR
      EINHEIT_UI_INSTALLED_ASSETS_DIR,
#endif
#ifdef EINHEIT_UI_DEV_ASSETS_DIR
      EINHEIT_UI_DEV_ASSETS_DIR,
#endif
      "assets",
  });
}

}  // namespace

auto main(int argc, char **argv) -> int {
  CLI::App app{"takt-ui — takt web interface"};
  std::string bind_addr = "127.0.0.1";
  std::uint16_t port = 7542;
  std::string tls_cert;
  std::string tls_key;
  std::string templates_dir;
  std::string assets_dir;
  std::string theme_name = "psychotropic";
  std::string takt_url = "http://127.0.0.1:7433";
  bool enable_editor = false;

  app.add_option("--bind", bind_addr, "Bind address");
  app.add_option("--port", port, "TCP port");
  app.add_option("--tls-cert", tls_cert,
    "TLS certificate path");
  app.add_option("--tls-key", tls_key,
    "TLS private key path");
  app.add_option("--templates", templates_dir,
    "Override templates root");
  app.add_option("--assets", assets_dir,
    "Override assets directory");
  app.add_option("--theme", theme_name,
    "Theme name");
  app.add_option("--takt-url", takt_url,
    "takt REST API base URL");
  app.add_flag("--editor", enable_editor,
    "Mount the /edit CodeMirror 6 editor");

  try {
    app.parse(argc, argv);
  } catch (const CLI::ParseError &e) {
    return app.exit(e);
  }

  einheit::adapters::takt::TaktClientConfig tcfg;
  tcfg.base_url = takt_url;
  auto adapter =
      einheit::adapters::takt::NewTaktUiAdapter(
          std::move(tcfg));

  einheit::ui::render::TemplateEngineConfig ecfg;
  ecfg.search_paths.push_back(adapter->TemplatesDir());
  if (enable_editor) {
    ecfg.search_paths.push_back(
        einheit::adapters::editor::TemplatesDir());
  }
  ecfg.search_paths.push_back(
      ResolveTemplatesDir(templates_dir));
#ifdef EINHEIT_UI_TEMPLATE_HOT_RELOAD
  ecfg.hot_reload = true;
#endif
  einheit::ui::render::TemplateEngine engine(
      std::move(ecfg));

  crow::SimpleApp crow_app;
  // WebSocket connections idle during long AI responses.
  crow_app.timeout(255);
  einheit::ui::ServerConfig scfg;
  scfg.bind_addr = bind_addr;
  scfg.port = port;
  scfg.tls_cert_path = tls_cert;
  scfg.tls_key_path = tls_key;
  scfg.assets_dir = ResolveAssetsDir(assets_dir);
  if (auto r = einheit::ui::Configure(crow_app, scfg);
      !r) {
    std::cerr << std::format("server config: {}\n",
                             r.error().message);
    return 1;
  }

  einheit::ui::EventStream events(engine);
  events.Mount(crow_app);
  events.MountMetrics(crow_app);

  const auto default_theme =
      einheit::ui::NamedTheme(theme_name);
  CROW_ROUTE(crow_app, "/theme.css")
  ([&engine, default_theme](const crow::request &req) {
    auto pick_name = [&]() -> std::string {
      if (const auto *q = req.url_params.get("name");
          q && *q) {
        return q;
      }
      const auto &cookie =
          req.get_header_value("Cookie");
      if (!cookie.empty()) {
        const std::string key = "einheit_theme=";
        auto pos = cookie.find(key);
        if (pos != std::string::npos) {
          auto start = pos + key.size();
          auto end = cookie.find(';', start);
          return cookie.substr(start,
              end == std::string::npos
                  ? std::string::npos
                  : end - start);
        }
      }
      return {};
    };
    auto theme = default_theme;
    const auto requested = pick_name();
    if (!requested.empty()) {
      const auto known =
          einheit::ui::NamedThemeList();
      if (std::find(known.begin(), known.end(),
                    requested) != known.end()) {
        theme = einheit::ui::NamedTheme(requested);
      }
    }
    auto body = engine.Render("theme.css",
        einheit::ui::ToJson(theme));
    if (!body) {
      crow::response r{500, body.error().message};
      r.set_header("Content-Type",
          "text/plain; charset=utf-8");
      return r;
    }
    crow::response r{200, *body};
    r.set_header("Content-Type",
        "text/css; charset=utf-8");
    r.set_header("Cache-Control", "no-store");
    return r;
  });

  einheit::ui::SetLayoutPrimaryNav(
      einheit::ui::NavToJson(adapter->Nav()));
  einheit::ui::SetLayoutPrimaryBrand(
      adapter->DisplayName());

  einheit::ui::AdapterContext ctx{
      .app = &crow_app,
      .templates = &engine,
      .events = &events};
  adapter->Mount(ctx);

  if (enable_editor) {
    einheit::adapters::editor::EditorConfig ecfg2;
    if (auto r = einheit::adapters::editor::Mount(
            crow_app, engine, ecfg2); !r) {
      std::cerr << std::format("editor adapter: {}\n",
                               r.error().message);
      return 1;
    }
    einheit::ui::SetLayoutEditorPath("/edit");
  }

  if (auto r = einheit::ui::Run(crow_app, scfg); !r) {
    std::cerr << std::format("server: {}\n",
                             r.error().message);
    return 1;
  }
  return 0;
}
