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
#include <fstream>
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

// Close all fds above `keep` in a forked child so the
// Crow listen socket (and anything else) isn't leaked to
// spawned processes. Without this, takt-cli and tmux
// inherit the port-7542 listener and steal connections.
void CloseInheritedFds(int keep = STDERR_FILENO) {
  int max_fd = static_cast<int>(
      ::sysconf(_SC_OPEN_MAX));
  if (max_fd < 0) max_fd = 1024;
  for (int fd = keep + 1; fd < max_fd; ++fd)
    ::close(fd);
}

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
             const std::string &cwd = {},
             const std::map<std::string, std::string>
                 &env = {}) -> bool {
    if (running_) return true;
    int master = -1;
    struct winsize ws{};
    ws.ws_row = 24;
    ws.ws_col = 120;
    pid_t pid = ::forkpty(
        &master, nullptr, nullptr, &ws);
    if (pid < 0) return false;
    if (pid == 0) {
      CloseInheritedFds();
      if (!cwd.empty()) ::chdir(cwd.c_str());
      ::setenv("TERM", "xterm-256color", 1);
      ::setenv("LANG", "C.UTF-8", 1);
      for (auto &[k, v] : env)
        ::setenv(k.c_str(), v.c_str(), 1);
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
        if (p.revents & (POLLERR | POLLHUP)) {
          int st = 0;
          auto wr = ::waitpid(child_, &st, WNOHANG);
          ::dprintf(STDERR_FILENO,
              "PTY: POLLHUP child=%d waitpid=%d "
              "status=%d exit=%d signal=%d\n",
              child_, (int)wr, st,
              WIFEXITED(st) ? WEXITSTATUS(st) : -1,
              WIFSIGNALED(st) ? WTERMSIG(st) : 0);
          break;
        }
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
            if (sink_) {
              try { sink_(chunk); } catch (...) {}
            }
          }
        } else if (n == 0) {
          ::dprintf(STDERR_FILENO, "PTY: read EOF\n");
          break;
        } else {
          if (errno != EINTR && errno != EAGAIN) {
            ::dprintf(STDERR_FILENO,
                "PTY: read error %s\n",
                strerror(errno));
            break;
          }
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
        if (sink_) {
          try { sink_(hint); } catch (...) {}
        }
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
    for (auto &chunk : replay) {
      try { sink(chunk); } catch (...) {}
    }
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
    // Null the sink first so the reader thread
    // won't call a dead connection during teardown.
    {
      std::lock_guard lk(sink_mu_);
      sink_ = nullptr;
    }
    if (master_ >= 0) { ::close(master_); master_ = -1; }
    if (reader_.joinable()) reader_.join();
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

/// Bridge to a tmux session via control mode (-C).
/// No PTY needed — uses pipes. The tmux session is
/// fully independent; this just observes and sends input.
class TmuxBridge {
 public:
  using Sink = std::function<void(std::string_view)>;
  static constexpr size_t kScrollback = 64 * 1024;

  ~TmuxBridge() { Close(); }

  /// Ensure a detached tmux session exists, then
  /// connect to it via control mode.
  auto Start(const std::string &session,
             const std::string &cwd,
             const std::string &cmd,
             const std::map<std::string, std::string>
                 &env = {}) -> bool {
    if (running_) return true;
    // A previous run may have exited on its own (tmux
    // died, pipe hangup) which sets running_ = false but
    // leaves the reader thread joinable and the fds/child
    // open. Reap that stale state first: move-assigning
    // onto a still-joinable std::thread (below) would call
    // std::terminate. This path is hit on reconnect.
    if (reader_.joinable()) reader_.join();
    if (write_fd_ >= 0) { ::close(write_fd_); write_fd_ = -1; }
    if (read_fd_ >= 0) { ::close(read_fd_); read_fd_ = -1; }
    if (child_ > 0) {
      ::kill(child_, SIGTERM);
      ::waitpid(child_, nullptr, 0);
      child_ = -1;
    }
    session_ = session;
    // Create detached session if it doesn't exist.
    // Use -e to pass env vars into the session so
    // claude picks up credentials.
    std::string env_flags;
    for (auto &[k, v] : env) {
      env_flags += " -e " + k + "=" + v;
    }
    auto create = "tmux has-session -t " + session
        + " 2>/dev/null || tmux new-session -d"
        + env_flags
        + " -e TERM=xterm-256color"
        + " -s " + session
        + " -c " + cwd + " " + cmd;
    // Fork instead of system() so we can close inherited
    // fds — otherwise the tmux server inherits Crow's
    // listen socket and steals browser connections.
    pid_t setup = ::fork();
    if (setup == 0) {
      CloseInheritedFds();
      ::execlp("/bin/sh", "sh", "-c",
               create.c_str(), nullptr);
      ::_exit(127);
    }
    if (setup > 0) ::waitpid(setup, nullptr, 0);
    // Open pipes for control mode.
    int to_tmux[2], from_tmux[2];
    if (::pipe(to_tmux) < 0 ||
        ::pipe(from_tmux) < 0)
      return false;
    pid_t pid = ::fork();
    if (pid < 0) return false;
    if (pid == 0) {
      ::dup2(to_tmux[0], STDIN_FILENO);
      ::dup2(from_tmux[1], STDOUT_FILENO);
      ::dup2(from_tmux[1], STDERR_FILENO);
      CloseInheritedFds();
      ::setenv("TERM", "xterm-256color", 1);
      ::execlp("tmux", "tmux", "-C", "attach",
               "-t", session.c_str(), nullptr);
      ::_exit(127);
    }
    ::close(to_tmux[0]);
    ::close(from_tmux[1]);
    write_fd_ = to_tmux[1];
    read_fd_ = from_tmux[0];
    child_ = pid;
    running_ = true;
    // Set initial size and tell tmux to use the
    // largest client's size for this session.
    Resize(24, 120);
    auto opts = "set-option -t " + session
        + " window-size largest\n"
        "set-option -g default-terminal"
        " xterm-256color\n"
        "set-option -sa terminal-overrides"
        " ',xterm-256color:Tc:RGB'\n"
        "set-option -sa terminal-features"
        " ',xterm-256color:256:RGB'\n"
        "set-option -g allow-passthrough on\n";
    auto r2 = ::write(write_fd_,
        opts.data(), opts.size());
    (void)r2;
    // Reader thread: parse control mode output.
    // Batches output and flushes every 50ms to avoid
    // overwhelming the WebSocket with rapid small writes.
    reader_ = std::thread([this] {
      char buf[8192];
      std::string line_buf;
      auto last_flush = std::chrono::steady_clock::now();
      while (running_) {
        struct pollfd p{read_fd_, POLLIN, 0};
        int rc = ::poll(&p, 1, 50);
        if (rc < 0) {
          if (errno == EINTR) continue;
          break;
        }
        if (p.revents & (POLLERR | POLLHUP)) break;
        if (rc > 0 && (p.revents & POLLIN)) {
          ssize_t n = ::read(read_fd_, buf,
                             sizeof(buf));
          if (n <= 0) break;
          line_buf.append(buf, n);
          size_t pos;
          while ((pos = line_buf.find('\n'))
                 != std::string::npos) {
            auto line = line_buf.substr(0, pos);
            line_buf.erase(0, pos + 1);
            ParseLine(line);
          }
        }
        // Flush batched output every 100ms.
        auto now = std::chrono::steady_clock::now();
        if (now - last_flush >=
            std::chrono::milliseconds(100)) {
          FlushPending();
          last_flush = now;
        }
      }
      FlushPending();
      running_ = false;
    });
    return true;
  }

  void Attach(Sink sink) {
    std::deque<std::string> replay;
    {
      std::lock_guard lk(buf_mu_);
      replay = scrollback_;
    }
    for (auto &chunk : replay) {
      try { sink(chunk); } catch (...) {}
    }
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

  void SendKeys(std::string_view input) {
    if (!running_ || write_fd_ < 0) return;
    // Escape single quotes for tmux command.
    std::string escaped;
    for (char c : input) {
      if (c == '\'') escaped += "'\\''";
      else if (c == '\\') escaped += "\\\\";
      else escaped += c;
    }
    auto cmd = "send-keys -t " + session_
        + " -l '" + escaped + "'\n";
    auto r = ::write(write_fd_, cmd.data(), cmd.size());
    (void)r;
  }

  void SendKey(const std::string &key) {
    if (!running_ || write_fd_ < 0) return;
    auto cmd = "send-keys -t " + session_
        + " " + key + "\n";
    auto r = ::write(write_fd_, cmd.data(), cmd.size());
    (void)r;
  }

  void Resize(unsigned short rows,
              unsigned short cols) {
    if (!running_ || write_fd_ < 0) return;
    auto cmd = std::format(
        "refresh-client -C {},{}\n"
        "resize-window -t {} -x {} -y {}\n",
        cols, rows, session_, cols, rows);
    auto r = ::write(write_fd_, cmd.data(), cmd.size());
    (void)r;
  }

  void Close() {
    {
      std::lock_guard lk(sink_mu_);
      sink_ = nullptr;
    }
    running_ = false;
    if (write_fd_ >= 0) {
      ::close(write_fd_); write_fd_ = -1;
    }
    if (read_fd_ >= 0) {
      ::close(read_fd_); read_fd_ = -1;
    }
    if (reader_.joinable()) reader_.join();
    if (child_ > 0) {
      ::kill(child_, SIGTERM);
      int st;
      ::waitpid(child_, &st, 0);
      child_ = -1;
    }
  }

 private:
  void ParseLine(const std::string &line) {
    if (line.starts_with("%exit")) {
      ::dprintf(STDERR_FILENO,
          "TMUX: got %%exit for %s\n",
          session_.c_str());
      return;
    }
    // %output %pane-id data
    if (line.starts_with("%output ")) {
      auto space = line.find(' ', 8);
      if (space == std::string::npos) return;
      auto data = line.substr(space + 1);
      // Unescape control mode escapes.
      auto decoded = Unescape(data);
      if (decoded.empty()) return;
      {
        std::lock_guard lk(buf_mu_);
        scrollback_.push_back(decoded);
        scrollback_size_ += decoded.size();
        while (scrollback_size_ > kScrollback
               && !scrollback_.empty()) {
          scrollback_size_ -=
              scrollback_.front().size();
          scrollback_.pop_front();
        }
      }
      {
        std::lock_guard lk(pending_mu_);
        pending_ += decoded;
      }
    }
  }

  void FlushPending() {
    std::string batch;
    {
      std::lock_guard lk(pending_mu_);
      if (pending_.empty()) return;
      batch.swap(pending_);
    }
    {
      std::lock_guard lk(sink_mu_);
      if (sink_) {
        try { sink_(batch); } catch (...) {}
      }
    }
  }

  static auto Unescape(const std::string &s)
      -> std::string {
    std::string out;
    out.reserve(s.size());
    for (size_t i = 0; i < s.size(); ++i) {
      if (s[i] == '\\' && i + 1 < s.size()) {
        char c = s[i + 1];
        if (c == '\\') { out += '\\'; ++i; }
        else if (c == 'n') { out += '\n'; ++i; }
        else if (c == 'r') { out += '\r'; ++i; }
        else if (c == 't') { out += '\t'; ++i; }
        else if (c == '0' && i + 3 < s.size()) {
          // Octal: \0xx
          char hi = s[i + 2], lo = s[i + 3];
          if (hi >= '0' && hi <= '7' &&
              lo >= '0' && lo <= '7') {
            out += static_cast<char>(
                (hi - '0') * 8 + (lo - '0'));
            i += 3;
          } else {
            out += s[i];
          }
        } else if (c == '0') {
          out += '\0'; ++i;
        } else {
          // \033 etc — octal escape
          if (c >= '0' && c <= '3' &&
              i + 3 < s.size()) {
            int val = (c - '0') * 64;
            val += (s[i + 2] - '0') * 8;
            val += (s[i + 3] - '0');
            out += static_cast<char>(val);
            i += 3;
          } else {
            out += '\\';
            out += c;
            ++i;
          }
        }
      } else {
        out += s[i];
      }
    }
    return out;
  }

  std::string session_;
  int write_fd_ = -1;
  int read_fd_ = -1;
  pid_t child_ = -1;
  std::atomic<bool> running_{false};
  std::thread reader_;
  std::mutex sink_mu_;
  Sink sink_;
  std::mutex buf_mu_;
  std::deque<std::string> scrollback_;
  size_t scrollback_size_ = 0;
  std::mutex pending_mu_;
  std::string pending_;
};

/// Safe WebSocket sender. Captures the connection by
/// pointer with a shared alive flag. The onclose handler
/// sets the flag to false so in-flight sink calls
/// from reader threads are no-ops.
struct SafeSender {
  crow::websocket::connection *conn;
  std::shared_ptr<std::atomic<bool>> alive;

  void operator()(std::string_view data) const {
    if (alive->load(std::memory_order_acquire))
      conn->send_text(std::string(data));
  }
};

auto MakeSafeSink(crow::websocket::connection &conn)
    -> std::pair<SafeSender,
                 std::shared_ptr<std::atomic<bool>>> {
  auto alive = std::make_shared<
      std::atomic<bool>>(true);
  return {SafeSender{&conn, alive}, alive};
}

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
    namespace fs = std::filesystem;
    auto self = fs::read_symlink("/proc/self/exe");
    auto takt_dir = self.parent_path().parent_path();
    cli_path_ =
        (self.parent_path() / "takt-cli").string();
    // Read active account's claude_home from takt.yaml.
    auto yaml_path = takt_dir / "config/takt.yaml";
    if (fs::exists(yaml_path)) {
      std::ifstream f(yaml_path);
      std::string line;
      std::string active;
      std::map<std::string, std::string> homes;
      std::string cur_account;
      while (std::getline(f, line)) {
        if (line.find("active_account:") !=
            std::string::npos) {
          auto pos = line.find(':');
          active = line.substr(pos + 1);
          while (!active.empty() &&
                 active[0] == ' ')
            active.erase(0, 1);
        }
        if (line.find("    ") == 0 &&
            line.find("claude_home:") ==
                std::string::npos &&
            line.find(':') != std::string::npos &&
            line.find("label:") == std::string::npos) {
          auto pos = line.find(':');
          cur_account = line.substr(4, pos - 4);
        }
        if (line.find("claude_home:") !=
            std::string::npos) {
          auto pos = line.find(':');
          auto val = line.substr(pos + 1);
          while (!val.empty() && val[0] == ' ')
            val.erase(0, 1);
          if (!cur_account.empty())
            homes[cur_account] = val;
        }
      }
      auto it = homes.find(active);
      if (it != homes.end()) {
        auto path = it->second;
        if (path.starts_with("~/")) {
          const char *h = std::getenv("HOME");
          if (h) path = std::string(h) +
              path.substr(1);
        }
        claude_config_dir_ =
            fs::weakly_canonical(path).string();
      }
      spdlog::info("claude_config_dir = {}",
          claude_config_dir_);
    }
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

    // No htmx OOB swap bindings — the billboard polls
    // the API directly and terminal WebSockets must not
    // be interrupted by event-driven page swaps.

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
    // alive flags per connection — set false in onclose.
    auto alive_map = std::make_shared<
        std::map<crow::websocket::connection *,
                 std::shared_ptr<std::atomic<bool>>>>();
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
            [pty_map, conn_map, alive_map,
             pty_mu, this](
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
              auto [sink, alive] = MakeSafeSink(conn);
              (*alive_map)[&conn] = alive;
              auto &pty = (*pty_map)[id];
              if (!pty) pty = std::make_shared<CliPty>();
              if (!pty->IsRunning()) {
                pty->Spawn(cli_path_,
                    {cli_path_, "--dark",
                     "--color", "always"});
              }
              pty->Attach(sink);
            })
        .onmessage(
            [pty_map, conn_map, alive_map,
             pty_mu, this](
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
                s->Close();
                s = std::make_shared<CliPty>();
                s->Spawn(cli_path_,
                    {cli_path_, "--dark",
                     "--color", "always"});
                auto [sk, al] = MakeSafeSink(conn);
                (*alive_map)[&conn] = al;
                s->Attach(sk);
                return;
              }
              bool ctl = !is_binary &&
                  !data.empty() &&
                  data.front() == '{';
              if (ctl) {
                try {
                  auto j = nlohmann::json::parse(
                      data);
                  auto type = j.value("type", "");
                  if (type == "resize") {
                    s->Resize(
                        j.value<unsigned short>(
                            "rows", 24),
                        j.value<unsigned short>(
                            "cols", 80));
                  }
                } catch (...) {}
                return;
              }
              s->Write(data);
            })
        .onclose(
            [pty_map, conn_map, alive_map, pty_mu](
                crow::websocket::connection &conn,
                const std::string &, uint16_t) {
              std::lock_guard lk(*pty_mu);
              auto ai = alive_map->find(&conn);
              if (ai != alive_map->end()) {
                ai->second->store(false,
                    std::memory_order_release);
                alive_map->erase(ai);
              }
              auto ci = conn_map->find(&conn);
              if (ci == conn_map->end()) return;
              auto pi = pty_map->find(ci->second);
              if (pi != pty_map->end() && pi->second)
                pi->second->Detach();
              conn_map->erase(ci);
            });

    // Workspace tabs via tmux control mode.
    // No PTY — pipes to `tmux -C attach`. The tmux
    // session is fully independent.
    auto tmux_map = std::make_shared<
        std::map<std::string,
                 std::shared_ptr<TmuxBridge>>>();
    auto ws_conn_map = std::make_shared<
        std::map<crow::websocket::connection *,
                 std::string>>();
    auto ws_alive_map = std::make_shared<
        std::map<crow::websocket::connection *,
                 std::shared_ptr<std::atomic<bool>>>>();
    auto ws_mu = std::make_shared<std::mutex>();

    CROW_WEBSOCKET_ROUTE(app,
        "/ws/workspace/<string>")
        .onaccept(
            [ws_mu](const crow::request &req,
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
            [tmux_map, ws_conn_map, ws_alive_map,
             ws_mu, this](
                crow::websocket::connection &conn) {
              std::lock_guard lk(*ws_mu);
              auto *p = static_cast<std::string *>(
                  conn.userdata());
              std::string name;
              if (p) { name = *p; delete p;
                       conn.userdata(nullptr); }
              if (name.empty()) {
                conn.close("no name"); return;
              }
              (*ws_conn_map)[&conn] = name;
              auto [sink, alive] = MakeSafeSink(conn);
              (*ws_alive_map)[&conn] = alive;
              auto &bridge = (*tmux_map)[name];
              if (!bridge)
                bridge =
                    std::make_shared<TmuxBridge>();
              if (!bridge->IsRunning()) {
                auto session = "ws-" + name;
                auto cwd =
                    "/home/karl/dev/workspaces/"
                    + name;
                std::map<std::string, std::string>
                    env;
                bridge->Start(session, cwd,
                    "claude"
                    " --dangerously-skip-permissions",
                    env);
              }
              bridge->Attach(sink);
            })
        .onmessage(
            [tmux_map, ws_conn_map, ws_mu](
                crow::websocket::connection &conn,
                const std::string &data,
                bool is_binary) {
              std::lock_guard lk(*ws_mu);
              auto ci = ws_conn_map->find(&conn);
              if (ci == ws_conn_map->end()) return;
              auto bi = tmux_map->find(ci->second);
              if (bi == tmux_map->end()) return;
              auto &b = bi->second;
              bool ctl = !is_binary &&
                  !data.empty() &&
                  data.front() == '{';
              if (ctl) {
                try {
                  auto j = nlohmann::json::parse(
                      data);
                  auto type = j.value("type", "");
                  if (type == "resize") {
                    b->Resize(
                        j.value<unsigned short>(
                            "rows", 24),
                        j.value<unsigned short>(
                            "cols", 80));
                  }
                } catch (...) {}
                return;
              }
              b->SendKeys(data);
            })
        .onclose(
            [tmux_map, ws_conn_map, ws_alive_map,
             ws_mu](
                crow::websocket::connection &conn,
                const std::string &, uint16_t) {
              std::lock_guard lk(*ws_mu);
              auto ai = ws_alive_map->find(&conn);
              if (ai != ws_alive_map->end()) {
                ai->second->store(false,
                    std::memory_order_release);
                ws_alive_map->erase(ai);
              }
              auto ci = ws_conn_map->find(&conn);
              if (ci == ws_conn_map->end()) return;
              auto bi = tmux_map->find(ci->second);
              if (bi != tmux_map->end() && bi->second)
                bi->second->Detach();
              ws_conn_map->erase(ci);
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
  std::string claude_config_dir_;
};

}  // namespace

auto NewTaktUiAdapter(TaktClientConfig cfg)
    -> std::unique_ptr<ui::ProductUiAdapter> {
  return std::make_unique<TaktUiAdapter>(
      std::move(cfg));
}

}  // namespace einheit::adapters::takt
