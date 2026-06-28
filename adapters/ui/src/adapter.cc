/// @file adapter.cc
/// @brief takt UI adapter. Proxies the takt REST API,
/// renders pages via inja templates, publishes live updates
/// over WebSocket by polling the SSE stream.
// Copyright (c) 2026 Einheit Networks

#include "einheit/adapters/takt/ui_adapter.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <format>
#include <map>
#include <memory>
#include <mutex>
#include <sstream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include <crow/websocket.h>
#include <httplib.h>
#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include <fcntl.h>
#include <poll.h>
#include <pty.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/wait.h>
#include <unistd.h>

#include "einheit/ui/route.h"

namespace einheit::adapters::takt {
namespace {

/// Persistent PTY session with scrollback ring buffer.
/// Survives WebSocket disconnects — the child stays alive
/// and output is buffered. On reconnect the scrollback is
/// replayed then new output is forwarded live.
class CliPty {
 public:
  using Sink = std::function<void(std::string_view)>;
  static constexpr size_t kScrollback = 64 * 1024;

  ~CliPty() { Close(); }

  auto Spawn(const std::string &path,
             const std::vector<std::string> &args,
             const std::string &cwd = {}) -> bool {
    if (running_) return true;
    int master = -1;
    pid_t pid = ::forkpty(
        &master, nullptr, nullptr, nullptr);
    if (pid < 0) return false;
    if (pid == 0) {
      if (!cwd.empty()) ::chdir(cwd.c_str());
      ::setenv("TERM", "xterm-256color", 1);
      ::setenv("LANG", "C.UTF-8", 1);
      std::vector<char *> argv;
      for (auto &a : args)
        argv.push_back(const_cast<char *>(a.c_str()));
      argv.push_back(nullptr);
      ::execvp(path.c_str(), argv.data());
      ::_exit(127);
    }
    int fl = ::fcntl(master, F_GETFL, 0);
    ::fcntl(master, F_SETFL, fl | O_NONBLOCK);
    master_ = master;
    child_ = pid;
    running_ = true;
    reader_ = std::thread([this] {
      char buf[4096];
      while (running_) {
        struct pollfd p{master_, POLLIN, 0};
        int rc = ::poll(&p, 1, 200);
        if (rc < 0) {
          if (errno == EINTR) continue;
          break;
        }
        if (rc == 0) continue;
        if (p.revents & (POLLERR | POLLHUP)) break;
        if (!(p.revents & POLLIN)) continue;
        ssize_t n = ::read(master_, buf, sizeof(buf));
        if (n > 0) {
          auto chunk = std::string(buf, n);
          {
            std::lock_guard lk(buf_mu_);
            scrollback_.push_back(chunk);
            scrollback_size_ += chunk.size();
            while (scrollback_size_ > kScrollback
                   && !scrollback_.empty()) {
              scrollback_size_ -=
                  scrollback_.front().size();
              scrollback_.pop_front();
            }
          }
          {
            std::lock_guard lk(sink_mu_);
            if (sink_) sink_(chunk);
          }
        } else if (n == 0) {
          break;
        } else {
          if (errno != EINTR && errno != EAGAIN)
            break;
        }
      }
      running_ = false;
      auto hint = std::string(
          "\r\n\x1b[33m[session ended — press any "
          "key to restart]\x1b[0m\r\n");
      {
        std::lock_guard lk(buf_mu_);
        scrollback_.push_back(hint);
        scrollback_size_ += hint.size();
      }
      {
        std::lock_guard lk(sink_mu_);
        if (sink_) sink_(hint);
      }
    });
    return true;
  }

  void Attach(Sink sink) {
    std::deque<std::string> replay;
    {
      std::lock_guard lk(buf_mu_);
      replay = scrollback_;
    }
    for (auto &chunk : replay) sink(chunk);
    {
      std::lock_guard lk(sink_mu_);
      sink_ = std::move(sink);
    }
  }

  void Detach() {
    std::lock_guard lk(sink_mu_);
    sink_ = nullptr;
  }

  auto IsRunning() const -> bool {
    return running_.load();
  }

  void Write(std::string_view d) {
    if (!running_ || master_ < 0) return;
    size_t off = 0;
    while (off < d.size()) {
      ssize_t n = ::write(master_, d.data() + off,
                          d.size() - off);
      if (n < 0) {
        if (errno == EINTR) continue;
        return;
      }
      off += n;
    }
  }

  void Resize(unsigned short rows, unsigned short cols) {
    if (master_ < 0) return;
    struct winsize ws{};
    ws.ws_row = rows;
    ws.ws_col = cols;
    ::ioctl(master_, TIOCSWINSZ, &ws);
  }

  void Close() {
    if (!running_.exchange(false) && master_ < 0
        && child_ < 0)
      return;
    if (child_ > 0) {
      ::kill(child_, SIGTERM);
      for (int i = 0; i < 25; ++i) {
        int st;
        if (::waitpid(child_, &st, WNOHANG)
            == child_)
          goto reaped;
        std::this_thread::sleep_for(
            std::chrono::milliseconds(20));
      }
      ::kill(child_, SIGKILL);
      { int st; ::waitpid(child_, &st, 0); }
      reaped:
      child_ = -1;
    }
    if (master_ >= 0) { ::close(master_); master_ = -1; }
    if (reader_.joinable()) reader_.join();
    {
      std::lock_guard lk(sink_mu_);
      sink_ = nullptr;
    }
  }

 private:
  int master_ = -1;
  pid_t child_ = -1;
  std::atomic<bool> running_{false};
  std::thread reader_;
  std::mutex sink_mu_;
  Sink sink_;
  std::mutex buf_mu_;
  std::deque<std::string> scrollback_;
  size_t scrollback_size_ = 0;
};

/// Build a badge context for a status string.
auto StatusSemantic(const std::string &s) -> std::string {
  if (s == "passed" || s == "completed" ||
      s == "running" || s == "clean") {
    return "good";
  }
  if (s == "failed" || s == "cancelled" ||
      s == "error") {
    return "bad";
  }
  if (s == "queued" || s == "pending") {
    return "warn";
  }
  return "info";
}

/// Build status rows for the dashboard status grid.
auto DashboardSummary(
    const nlohmann::json &workspaces,
    const nlohmann::json &runs,
    const nlohmann::json &agents,
    const nlohmann::json &targets)
    -> nlohmann::json {
  nlohmann::json rows = nlohmann::json::array();
  rows.push_back({
      {"label", "workspaces"},
      {"value", std::to_string(workspaces.size())},
  });
  rows.push_back({
      {"label", "targets"},
      {"value", std::to_string(targets.size())},
  });
  std::size_t running = 0;
  for (const auto &r : runs) {
    if (r.value("status", "") == "running") ++running;
  }
  rows.push_back({
      {"label", "active runs"},
      {"value", std::to_string(running)},
      {"semantic", running > 0 ? "good" : "info"},
  });
  rows.push_back({
      {"label", "agents"},
      {"value", std::to_string(agents.size())},
  });
  return rows;
}

/// Build workspace table rows.
auto WorkspaceRows(const nlohmann::json &workspaces)
    -> nlohmann::json {
  nlohmann::json rows = nlohmann::json::array();
  for (const auto &ws : workspaces) {
    nlohmann::json row;
    row["name"] = ws.value("name", "");
    row["branch"] = ws.value("branch", "");
    auto repos = ws.value("repos", nlohmann::json::array());
    row["repo_count"] = repos.size();
    std::string repo_list;
    for (const auto &r : repos) {
      if (!repo_list.empty()) repo_list += ", ";
      repo_list += r.get<std::string>();
    }
    row["repos"] = repo_list;
    rows.push_back(std::move(row));
  }
  return rows;
}

/// Build target table rows.
auto TargetRows(const nlohmann::json &targets)
    -> nlohmann::json {
  nlohmann::json rows = nlohmann::json::array();
  for (const auto &t : targets) {
    nlohmann::json row;
    row["name"] = t.value("name", "");
    row["type"] = t.value("type", "");
    row["host"] = t.value("host", "");
    row["template"] = t.value("template", false);
    auto lock = t.value("lock", nlohmann::json{});
    if (!lock.is_null()) {
      row["claimed_by"] = lock.value("workspace", "");
      row["state_semantic"] = "warn";
    } else {
      row["claimed_by"] = "";
      row["state_semantic"] = "good";
    }
    rows.push_back(std::move(row));
  }
  return rows;
}

/// Build run table rows.
auto RunRows(const nlohmann::json &runs)
    -> nlohmann::json {
  nlohmann::json rows = nlohmann::json::array();
  for (const auto &r : runs) {
    nlohmann::json row;
    row["id"] = r.value("id", 0);
    row["workspace"] = r.value("workspace", "");
    row["status"] = r.value("status", "");
    row["status_semantic"] =
        StatusSemantic(r.value("status", ""));
    row["trigger"] = r.value("trigger", "");
    row["created_at"] = r.value("created_at", "");
    rows.push_back(std::move(row));
  }
  return rows;
}

class TaktUiAdapter final : public ui::ProductUiAdapter {
 public:
  explicit TaktUiAdapter(TaktClientConfig cfg)
      : client_cfg_(cfg), client_(std::move(cfg)) {
    // Resolve takt-cli path: same directory as our binary.
    namespace fs = std::filesystem;
    auto self = fs::read_symlink("/proc/self/exe");
    cli_path_ =
        (self.parent_path() / "takt-cli").string();
  }

  ~TaktUiAdapter() override {
    poller_stop_.store(true);
    if (poller_.joinable()) poller_.join();
  }

  auto Slug() const -> std::string override {
    return "takt";
  }
  auto DisplayName() const -> std::string override {
    return "takt";
  }
  auto TemplatesDir() const -> std::string override {
    return EINHEIT_UI_ADAPTER_TAKT_TEMPLATES_DIR;
  }
  auto Nav() const -> std::vector<ui::NavEntry> override {
    return {
        {"/", "Dashboard", "dashboard", "monitor"},
        {"/editor", "Editor", "editor", "square-pen"},
        {"/workspaces", "Workspaces", "workspaces",
         "git-branch"},
        {"/pipeline", "Pipeline", "pipeline",
         "git-commit"},
        {"/agents", "Agents", "agents", "bot"},
        {"/targets", "Targets", "targets", "server"},
        {"/runs", "Runs", "runs", "play"},
        {"/meta-agents", "Meta", "meta", "cpu"},
        {"/settings", "Settings", "settings",
         "settings"},
    };
  }

  auto Mount(ui::AdapterContext ctx) -> void override {
    auto *eng = ctx.templates;
    auto &app = *ctx.app;
    auto *events = ctx.events;

    StartPoller(events);

    // Bind WebSocket topics for live updates.
    events->Bind(ui::TopicBinding{
        .topic = "takt.runs",
        .fragment = "takt/runs_table",
        .swap_target = "runs-table",
        .swap_strategy = "outerHTML",
    });
    events->Bind(ui::TopicBinding{
        .topic = "takt.dashboard",
        .fragment = "takt/dashboard_summary",
        .swap_target = "dashboard-summary",
        .swap_strategy = "outerHTML",
    });
    events->Bind(ui::TopicBinding{
        .topic = "takt.agents",
        .fragment = "takt/agents_table",
        .swap_target = "agents-table",
        .swap_strategy = "outerHTML",
    });

    // -- Home: dashboard + CLI split pane --
    CROW_ROUTE(app, "/")
    ([eng, this](const crow::request &req) {
      auto workspaces =
          client_.Get("/api/workspaces");
      auto runs = client_.Get("/api/runs");
      auto agents = client_.Get("/api/agents");
      auto targets = client_.Get("/api/targets");
      if (!workspaces || !runs || !agents || !targets) {
        auto msg = !workspaces
            ? workspaces.error().message
            : !runs ? runs.error().message
            : !agents ? agents.error().message
                      : targets.error().message;
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable", msg,
            "is the takt API server running?");
      }
      ui::RenderArgs args;
      args.fragment = "takt/home";
      args.layout = "layout";
      args.data = {
          {"summary",
           DashboardSummary(*workspaces, *runs,
                            *agents, *targets)},
          {"workspaces", WorkspaceRows(*workspaces)},
          {"runs", RunRows(*runs)},
          {"targets", TargetRows(*targets)},
          {"agents", *agents},
          {"takt_api_url", client_cfg_.base_url},
      };
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- takt-specific assets --
    auto takt_assets_dir =
        std::filesystem::read_symlink("/proc/self/exe")
            .parent_path().parent_path().string()
        + "/assets";
    CROW_ROUTE(app, "/takt-assets/<path>")
    ([takt_assets_dir](const crow::request &,
                       std::string path) {
      if (path.find("..") != std::string::npos) {
        return crow::response(400);
      }
      auto full = takt_assets_dir + "/" + path;
      crow::response r;
      r.set_static_file_info_unsafe(full);
      if (path.ends_with(".js")) {
        r.set_header("Content-Type",
            "application/javascript; charset=utf-8");
      }
      return r;
    });

    // -- Editor --
    CROW_ROUTE(app, "/editor")
    ([eng, this](const crow::request &req) {
      ui::RenderArgs args;
      args.fragment = "takt/editor";
      args.layout = "layout";
      args.data = {
          {"takt_api_url", client_cfg_.base_url},
      };
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Workspaces --
    CROW_ROUTE(app, "/workspaces")
    ([eng, this](const crow::request &req) {
      auto workspaces =
          client_.Get("/api/workspaces");
      if (!workspaces) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            workspaces.error().message);
      }
      ui::RenderArgs args;
      args.fragment = "takt/workspaces";
      args.layout = "layout";
      args.data = {
          {"workspaces", WorkspaceRows(*workspaces)},
      };
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Workspace detail --
    CROW_ROUTE(app, "/workspaces/<string>")
    ([eng, this](const crow::request &req,
                 std::string name) {
      auto status = client_.Get(std::format(
          "/api/workspaces/{}/status", name));
      if (!status) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            status.error().message);
      }
      nlohmann::json repos = nlohmann::json::array();
      for (const auto &s : *status) {
        nlohmann::json row;
        row["repo"] = s.value("repo", "");
        row["branch"] = s.value("branch", "");
        auto st = s.value("status", "");
        row["status"] = st;
        row["status_semantic"] = StatusSemantic(st);
        repos.push_back(std::move(row));
      }
      ui::RenderArgs args;
      args.fragment = "takt/workspace_detail";
      args.layout = "layout";
      args.data = {
          {"name", name}, {"repos", repos},
      };
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Pipeline --
    CROW_ROUTE(app, "/pipeline")
    ([eng, this](const crow::request &req) {
      auto workspaces =
          client_.Get("/api/workspaces");
      if (!workspaces) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            workspaces.error().message);
      }
      nlohmann::json pipelines =
          nlohmann::json::array();
      for (const auto &ws : *workspaces) {
        auto name =
            ws.value("name", std::string{});
        auto steps = client_.Get(std::format(
            "/api/pipeline/{}", name));
        nlohmann::json entry;
        entry["workspace"] = name;
        entry["steps"] = steps ? *steps
                               : nlohmann::json::array();
        pipelines.push_back(std::move(entry));
      }
      ui::RenderArgs args;
      args.fragment = "takt/pipeline";
      args.layout = "layout";
      args.data = {{"pipelines", pipelines}};
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Targets --
    CROW_ROUTE(app, "/targets")
    ([eng, this](const crow::request &req) {
      auto targets = client_.Get("/api/targets");
      if (!targets) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            targets.error().message);
      }
      ui::RenderArgs args;
      args.fragment = "takt/targets";
      args.layout = "layout";
      args.data = {
          {"targets", TargetRows(*targets)},
      };
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Runs --
    CROW_ROUTE(app, "/runs")
    ([eng, this](const crow::request &req) {
      auto runs = client_.Get("/api/runs");
      if (!runs) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            runs.error().message);
      }
      ui::RenderArgs args;
      args.fragment = "takt/runs";
      args.layout = "layout";
      args.data = {{"runs", RunRows(*runs)}};
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Run detail --
    CROW_ROUTE(app, "/runs/<int>")
    ([eng, this](const crow::request &req, int id) {
      auto run = client_.Get(
          std::format("/api/runs/{}", id));
      if (!run) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            run.error().message);
      }
      auto data = *run;
      if (data.contains("run")) {
        auto &r2 = data["run"];
        r2["status_semantic"] = StatusSemantic(
            r2.value("status", ""));
      }
      if (data.contains("steps")) {
        for (auto &s : data["steps"]) {
          s["status_semantic"] = StatusSemantic(
              s.value("status", ""));
        }
      }
      ui::RenderArgs args;
      args.fragment = "takt/run_detail";
      args.layout = "layout";
      args.data = data;
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Step output --
    CROW_ROUTE(app, "/runs/<int>/steps/<int>")
    ([eng, this](const crow::request &req,
                 int run_id, int step_id) {
      auto output = client_.Get(std::format(
          "/api/runs/{}/steps/{}/output", run_id,
          step_id));
      auto step = client_.Get(std::format(
          "/api/runs/{}/steps/{}", run_id, step_id));
      if (!output || !step) {
        auto msg = !output
            ? output.error().message
            : step.error().message;
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable", msg);
      }
      nlohmann::json entries =
          nlohmann::json::array();
      for (const auto &line : *output) {
        std::string level = "INFO";
        auto kind = line.value("kind", "text");
        if (kind == "error") level = "ERROR";
        else if (kind == "tool_use") level = "DEBUG";
        else if (kind == "thinking") level = "DEBUG";
        entries.push_back({
            {"timestamp", line.value("ts", "")},
            {"level", level},
            {"message", line.value("content", "")},
        });
      }
      auto step_data = *step;
      step_data["status_semantic"] = StatusSemantic(
          step_data.value("status", ""));
      ui::RenderArgs args;
      args.fragment = "takt/step_output";
      args.layout = "layout";
      args.data = {
          {"step", step_data},
          {"entries", entries},
          {"run_id", run_id},
          {"next_line",
           static_cast<int>(output->size())},
      };
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Step output tail (HTMX poll) --
    CROW_ROUTE(app, "/runs/<int>/steps/<int>/tail")
    ([eng, this](const crow::request &req,
                 int run_id, int step_id) {
      auto from_str = req.url_params.get("from");
      int from_line = from_str ? std::atoi(from_str)
                               : 0;
      auto output = client_.Get(std::format(
          "/api/runs/{}/steps/{}/output?from={}",
          run_id, step_id, from_line));
      auto step = client_.Get(std::format(
          "/api/runs/{}/steps/{}", run_id, step_id));
      if (!output) {
        return crow::response(502,
            output.error().message);
      }
      nlohmann::json entries =
          nlohmann::json::array();
      for (const auto &line : *output) {
        std::string level = "INFO";
        auto kind = line.value("kind", "text");
        if (kind == "error") level = "ERROR";
        else if (kind == "tool_use") level = "DEBUG";
        else if (kind == "thinking") level = "DEBUG";
        entries.push_back({
            {"timestamp", line.value("ts", "")},
            {"level", level},
            {"message", line.value("content", "")},
        });
      }
      bool finished = false;
      if (step) {
        auto st = step->value("status", "");
        finished = st == "completed" ||
                   st == "failed" ||
                   st == "cancelled" ||
                   st == "skipped";
      }
      ui::RenderArgs args;
      args.fragment = "takt/step_output_tail";
      args.data = {
          {"entries", entries},
          {"step_finished", finished},
      };
      auto r = ui::Render(
          *eng, ui::ResponseFormat::Fragment, args);
      if (!r) {
        return crow::response(500,
            r.error().message);
      }
      return std::move(*r);
    });

    // -- Agents --
    CROW_ROUTE(app, "/agents")
    ([eng, this](const crow::request &req) {
      auto agents = client_.Get("/api/agents");
      if (!agents) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            agents.error().message);
      }
      nlohmann::json enriched = nlohmann::json::array();
      for (auto a : *agents) {
        a["status_semantic"] = StatusSemantic(
            a.value("status", ""));
        enriched.push_back(std::move(a));
      }
      ui::RenderArgs args;
      args.fragment = "takt/agents";
      args.layout = "layout";
      args.data = {{"agents", enriched}};
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- Meta agents --
    CROW_ROUTE(app, "/meta-agents")
    ([eng, this](const crow::request &req) {
      auto agents =
          client_.Get("/api/meta-agents");
      if (!agents) {
        return ui::RenderError(
            *eng, req, 502, "takt_unreachable",
            agents.error().message);
      }
      ui::RenderArgs args;
      args.fragment = "takt/meta_agents";
      args.layout = "layout";
      args.data = {{"agents", *agents}};
      auto r = ui::Render(*eng, req, args);
      if (!r) {
        return ui::RenderError(*eng, req, 500,
                               "render_failed",
                               r.error().message);
      }
      return std::move(*r);
    });

    // -- POST actions --

    // Helper: extract a field from form body or JSON.
    auto field = [](const crow::request &req,
                    const std::string &key)
        -> std::string {
      auto ct = req.get_header_value("Content-Type");
      if (ct.find("application/json") !=
          std::string::npos) {
        auto j = nlohmann::json::parse(
            req.body, nullptr, false);
        return j.value(key, std::string{});
      }
      // URL-encoded form: key=value&key2=value2
      auto pos = req.body.find(key + "=");
      if (pos == std::string::npos) return {};
      auto start = pos + key.size() + 1;
      auto end = req.body.find('&', start);
      auto raw = end == std::string::npos
          ? req.body.substr(start)
          : req.body.substr(start, end - start);
      // Decode %XX and +
      std::string out;
      for (std::size_t i = 0; i < raw.size(); ++i) {
        if (raw[i] == '+') {
          out += ' ';
        } else if (raw[i] == '%' &&
                   i + 2 < raw.size()) {
          auto hex = raw.substr(i + 1, 2);
          out += static_cast<char>(
              std::stoi(hex, nullptr, 16));
          i += 2;
        } else {
          out += raw[i];
        }
      }
      return out;
    };

    CROW_ROUTE(app, "/targets/<string>/claim")
        .methods("POST"_method)(
            [this, field](const crow::request &req,
                          std::string name) {
              auto workspace = field(req, "workspace");
              auto resp = client_.Post(
                  std::format(
                      "/api/targets/{}/claim", name),
                  nlohmann::json{
                      {"workspace", workspace}}
                      .dump());
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(200,
                  resp->dump());
            });

    CROW_ROUTE(app, "/targets/<string>/release")
        .methods("POST"_method)(
            [this](const crow::request &,
                   std::string name) {
              auto resp = client_.Post(
                  std::format(
                      "/api/targets/{}/release",
                      name));
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(200,
                  resp->dump());
            });

    CROW_ROUTE(app, "/runs/trigger")
        .methods("POST"_method)(
            [this, field](const crow::request &req) {
              auto workspace = field(req, "workspace");
              auto resp = client_.Post(
                  "/api/runs",
                  nlohmann::json{
                      {"workspace", workspace}}
                      .dump());
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(201,
                  resp->dump());
            });

    CROW_ROUTE(app, "/meta-agents/create")
        .methods("POST"_method)(
            [this, field](const crow::request &req) {
              auto name = field(req, "name");
              auto prompt = field(req, "prompt");
              auto model = field(req, "model");
              if (model.empty()) model = "sonnet";
              auto resp = client_.Post(
                  "/api/meta-agents",
                  nlohmann::json{
                      {"name", name},
                      {"prompt", prompt},
                      {"model", model}}
                      .dump());
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(201,
                  resp->dump());
            });

    CROW_ROUTE(app, "/meta-agents/<int>/run")
        .methods("POST"_method)(
            [this](const crow::request &, int id) {
              auto resp = client_.Post(
                  std::format(
                      "/api/meta-agents/{}/run", id));
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(201,
                  resp->dump());
            });

    CROW_ROUTE(app, "/meta-agents/<int>")
        .methods("DELETE"_method)(
            [this](const crow::request &, int id) {
              auto resp = client_.Delete(
                  std::format(
                      "/api/meta-agents/{}", id));
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(200,
                  resp->dump());
            });

    CROW_ROUTE(app, "/runs/<int>/cancel")
        .methods("POST"_method)(
            [this](const crow::request &, int id) {
              auto resp = client_.Post(
                  std::format(
                      "/api/runs/{}/cancel", id));
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(200,
                  resp->dump());
            });

    CROW_ROUTE(app, "/workspaces/create")
        .methods("POST"_method)(
            [this, field](const crow::request &req) {
              auto name = field(req, "name");
              auto repos_str = field(req, "repos");
              nlohmann::json repos_arr =
                  nlohmann::json::array();
              std::istringstream iss(repos_str);
              std::string tok;
              while (iss >> tok) {
                repos_arr.push_back(tok);
              }
              auto resp = client_.Post(
                  "/api/workspaces",
                  nlohmann::json{
                      {"name", name},
                      {"repos", repos_arr}}
                      .dump());
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(201,
                  resp->dump());
            });

    CROW_ROUTE(app, "/workspaces/<string>")
        .methods("DELETE"_method)(
            [this](const crow::request &,
                   std::string name) {
              auto resp = client_.Delete(
                  std::format(
                      "/api/workspaces/{}", name));
              if (!resp) {
                return crow::response(502,
                    resp.error().message);
              }
              return crow::response(200,
                  resp->dump());
            });

    // -- Persistent PTY sessions --
    // Keyed by session ID (e.g. "mngmt", workspace name).
    // Sessions survive WebSocket disconnects; scrollback
    // is replayed on reconnect.
    auto pty_map = std::make_shared<
        std::map<std::string,
                 std::shared_ptr<CliPty>>>();
    auto conn_map = std::make_shared<
        std::map<crow::websocket::connection *,
                 std::string>>();
    auto pty_mu = std::make_shared<std::mutex>();

    // CLI tabs: /cli/ws/<id>
    CROW_WEBSOCKET_ROUTE(app, "/cli/ws/<string>")
        .onaccept(
            [pty_mu](const crow::request &req,
                     void **ud) -> bool {
              const std::string pfx = "/cli/ws/";
              auto url = std::string(req.url);
              if (url.starts_with(pfx))
                *ud = new std::string(
                    url.substr(pfx.size()));
              return true;
            })
        .onopen(
            [pty_map, conn_map, pty_mu, this](
                crow::websocket::connection &conn) {
              std::lock_guard lk(*pty_mu);
              auto *p = static_cast<std::string *>(
                  conn.userdata());
              std::string id;
              if (p) { id = *p; delete p;
                       conn.userdata(nullptr); }
              if (id.empty()) {
                conn.close("no id"); return;
              }
              (*conn_map)[&conn] = id;
              auto &pty = (*pty_map)[id];
              if (!pty) pty = std::make_shared<CliPty>();
              if (!pty->IsRunning()) {
                pty->Spawn(cli_path_,
                    {cli_path_, "--dark",
                     "--color", "always"});
              }
              pty->Attach(
                  [&conn](std::string_view c) {
                    conn.send_text(std::string(c));
                  });
            })
        .onmessage(
            [pty_map, conn_map, pty_mu, this](
                crow::websocket::connection &conn,
                const std::string &data,
                bool is_binary) {
              std::lock_guard lk(*pty_mu);
              auto ci = conn_map->find(&conn);
              if (ci == conn_map->end()) return;
              auto pi = pty_map->find(ci->second);
              if (pi == pty_map->end()) return;
              auto &s = pi->second;
              if (!s->IsRunning()) {
                bool ctl = !is_binary &&
                    !data.empty() &&
                    data.front() == '{';
                if (ctl) return;
                s = std::make_shared<CliPty>();
                s->Spawn(cli_path_,
                    {cli_path_, "--dark",
                     "--color", "always"});
                s->Attach(
                    [&conn](std::string_view c) {
                      conn.send_text(
                          std::string(c));
                    });
                return;
              }
              bool ctl = !is_binary &&
                  !data.empty() &&
                  data.front() == '{';
              if (ctl) {
                try {
                  auto j = nlohmann::json::parse(
                      data);
                  if (j.value("type", "") ==
                      "resize") {
                    s->Resize(
                        j.value<unsigned short>(
                            "rows", 24),
                        j.value<unsigned short>(
                            "cols", 80));
                    return;
                  }
                } catch (...) {}
              }
              s->Write(data);
            })
        .onclose(
            [pty_map, conn_map, pty_mu](
                crow::websocket::connection &conn,
                const std::string &, uint16_t) {
              std::lock_guard lk(*pty_mu);
              auto ci = conn_map->find(&conn);
              if (ci == conn_map->end()) return;
              auto pi = pty_map->find(ci->second);
              if (pi != pty_map->end() && pi->second)
                pi->second->Detach();
              conn_map->erase(ci);
            });

    // Workspace tabs: /ws/workspace/<name>
    CROW_WEBSOCKET_ROUTE(app,
        "/ws/workspace/<string>")
        .onaccept(
            [pty_mu](const crow::request &req,
                     void **ud) -> bool {
              const std::string pfx =
                  "/ws/workspace/";
              auto url = std::string(req.url);
              if (url.starts_with(pfx))
                *ud = new std::string(
                    url.substr(pfx.size()));
              return true;
            })
        .onopen(
            [pty_map, conn_map, pty_mu](
                crow::websocket::connection &conn) {
              std::lock_guard lk(*pty_mu);
              auto *p = static_cast<std::string *>(
                  conn.userdata());
              std::string name;
              if (p) { name = *p; delete p;
                       conn.userdata(nullptr); }
              if (name.empty()) {
                conn.close("no name"); return;
              }
              (*conn_map)[&conn] = name;
              auto &pty = (*pty_map)[name];
              if (!pty) pty = std::make_shared<CliPty>();
              if (!pty->IsRunning()) {
                auto session = "ws-" + name;
                auto cwd =
                    "/home/karl/dev/workspaces/"
                    + name;
                pty->Spawn("tmux",
                    {"tmux", "new-session", "-A",
                     "-s", session, "claude",
                     "--dangerously-skip-permissions"},
                    cwd);
              }
              pty->Attach(
                  [&conn](std::string_view c) {
                    conn.send_text(std::string(c));
                  });
            })
        .onmessage(
            [pty_map, conn_map, pty_mu](
                crow::websocket::connection &conn,
                const std::string &data,
                bool is_binary) {
              std::lock_guard lk(*pty_mu);
              auto ci = conn_map->find(&conn);
              if (ci == conn_map->end()) return;
              auto pi = pty_map->find(ci->second);
              if (pi == pty_map->end()) return;
              auto &s = pi->second;
              bool ctl = !is_binary &&
                  !data.empty() &&
                  data.front() == '{';
              if (ctl) {
                try {
                  auto j = nlohmann::json::parse(
                      data);
                  if (j.value("type", "") ==
                      "resize") {
                    s->Resize(
                        j.value<unsigned short>(
                            "rows", 24),
                        j.value<unsigned short>(
                            "cols", 80));
                    return;
                  }
                } catch (...) {}
              }
              s->Write(data);
            })
        .onclose(
            [pty_map, conn_map, pty_mu](
                crow::websocket::connection &conn,
                const std::string &, uint16_t) {
              std::lock_guard lk(*pty_mu);
              auto ci = conn_map->find(&conn);
              if (ci == conn_map->end()) return;
              auto pi = pty_map->find(ci->second);
              if (pi != pty_map->end() && pi->second)
                pi->second->Detach();
              conn_map->erase(ci);
            });
  }

 private:
  /// Try SSE stream from /api/events; on any event,
  /// refresh the relevant data and push via WebSocket.
  /// Falls back to 5s polling if SSE fails.
  void StartPoller(ui::EventStream *events) {
    if (!events) return;
    poller_ = std::thread([this, events]() {
      using namespace std::chrono;
      while (!poller_stop_.load(
          std::memory_order_relaxed)) {
        if (TrySse(events)) continue;
        PollOnce(events);
        std::this_thread::sleep_for(seconds(5));
      }
    });
  }

  /// Attempt to connect to the SSE stream. Returns true
  /// if the stream ran and should be retried, false if
  /// the connection failed and we should fall back.
  auto TrySse(ui::EventStream *events) -> bool {
    using namespace std::chrono;
    auto url = client_cfg_.base_url;
    std::string host = "127.0.0.1";
    int port = 7433;
    if (url.starts_with("http://")) {
      auto rest = url.substr(7);
      auto slash = rest.find('/');
      if (slash != std::string::npos)
        rest = rest.substr(0, slash);
      auto colon = rest.find(':');
      if (colon != std::string::npos) {
        host = rest.substr(0, colon);
        try {
          port = std::stoi(rest.substr(colon + 1));
        } catch (...) {}
      } else {
        host = rest;
        port = 80;
      }
    }
    httplib::Client cli(host, port);
    cli.set_read_timeout(seconds(35));
    cli.set_connection_timeout(seconds(2));
    std::string buffer;
    std::string event_type;
    auto result = cli.Get(
        "/api/events",
        [&](const char *data, size_t len) -> bool {
          if (poller_stop_.load(
                  std::memory_order_relaxed)) {
            return false;
          }
          buffer.append(data, len);
          while (true) {
            auto nl = buffer.find('\n');
            if (nl == std::string::npos) break;
            auto line = buffer.substr(0, nl);
            buffer.erase(0, nl + 1);
            if (line.starts_with("event: ")) {
              event_type = line.substr(7);
            } else if (line.starts_with("data: ")) {
              OnSseData(events, event_type,
                        line.substr(6));
              event_type.clear();
            }
          }
          return true;
        });
    return result && result->status == 200;
  }

  void OnSseData(ui::EventStream *events,
                 const std::string &type,
                 const std::string &data) {
    if (type == "error") return;
    // Any event triggers a dashboard refresh.
    PollOnce(events);
  }

  void PollOnce(ui::EventStream *events) {
    auto runs = client_.Get("/api/runs?limit=10");
    if (runs) {
      events->Publish(
          "takt.runs", {{"runs", RunRows(*runs)}});
    }
    auto ws = client_.Get("/api/workspaces");
    auto agents = client_.Get("/api/agents");
    auto targets = client_.Get("/api/targets");
    if (agents) {
      nlohmann::json enriched = nlohmann::json::array();
      for (auto a : *agents) {
        a["status_semantic"] = StatusSemantic(
            a.value("status", ""));
        enriched.push_back(std::move(a));
      }
      events->Publish("takt.agents",
          {{"agents", enriched}});
    }
    if (ws && runs && agents && targets) {
      events->Publish("takt.dashboard",
          {{"summary", DashboardSummary(
              *ws, *runs, *agents, *targets)}});
    }
  }

  TaktClientConfig client_cfg_;
  TaktClient client_;
  std::thread poller_;
  std::atomic<bool> poller_stop_{false};
  std::string cli_path_;
};

}  // namespace

auto NewTaktUiAdapter(TaktClientConfig cfg)
    -> std::unique_ptr<ui::ProductUiAdapter> {
  return std::make_unique<TaktUiAdapter>(
      std::move(cfg));
}

}  // namespace einheit::adapters::takt
