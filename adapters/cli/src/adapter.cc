/// @file adapter.cc
/// @brief takt CLI adapter. Provides workspace, target, pipeline,
/// and agent management commands via the takt REST API.
// Copyright (c) 2026 Einheit Networks

#include "adapters/takt/adapter.h"

#include <filesystem>
#include <format>
#include <fstream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include "einheit/cli/adapter.h"
#include "einheit/cli/command_tree.h"
#include "einheit/cli/protocol/envelope.h"
#include "einheit/cli/render/table.h"
#include "einheit/cli/schema.h"

namespace einheit::adapters::takt {
namespace {

using cli::ArgSpec;
using cli::CommandSpec;
using cli::ProductAdapter;
using cli::ProductMetadata;
using cli::protocol::Event;
using cli::protocol::Response;
using cli::render::Renderer;
using cli::schema::Schema;

constexpr const char *kSchemaYaml = R"(
version: 1
product: takt

config:
  api_url:
    type: string
    default: "http://127.0.0.1:7433"
    help: "takt REST API base URL"
)";

auto LoadSchema() -> std::shared_ptr<Schema> {
  namespace fs = std::filesystem;
  auto path = fs::temp_directory_path() / "takt-schema.yaml";
  {
    std::ofstream f(path);
    f << kSchemaYaml;
  }
  auto s = cli::schema::LoadSchema(path.string());
  if (!s) return std::make_shared<Schema>();
  return *s;
}

class TaktAdapter : public ProductAdapter {
 public:
  explicit TaktAdapter(TaktCliConfig cfg)
      : cfg_(std::move(cfg)),
        schema_(LoadSchema()) {}

  auto Metadata() const -> ProductMetadata override {
    return {
        .id = "takt",
        .display_name = "takt pipeline orchestrator",
        .version = "0.0.1",
        .banner = {},
        .prompt = "takt",
    };
  }

  auto GetSchema() const -> const Schema & override {
    return *schema_;
  }

  auto ControlSocketPath() const
      -> std::string override {
    return {};
  }

  auto EventSocketPath() const
      -> std::string override {
    return {};
  }

  auto Commands() const
      -> std::vector<CommandSpec> override {
    std::vector<CommandSpec> out;

    // -- Workspace commands --
    {
      CommandSpec c;
      c.path = "show workspaces";
      c.wire_command = "list_workspaces";
      c.help = "List all workspaces";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "show workspace";
      c.wire_command = "workspace_status";
      c.args = {{.name = "name", .help = "Workspace name"}};
      c.help = "Show repo status for a workspace";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "create workspace";
      c.wire_command = "create_workspace";
      c.args = {
          {.name = "name", .help = "Workspace name"},
          {.name = "repos",
           .help = "Repos to clone (space-separated)",
           .required = false},
      };
      c.help = "Create a new workspace";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "delete workspace";
      c.wire_command = "delete_workspace";
      c.args = {{.name = "name", .help = "Workspace name"}};
      c.help = "Delete a workspace";
      out.push_back(std::move(c));
    }

    // -- Target commands --
    {
      CommandSpec c;
      c.path = "show targets";
      c.wire_command = "list_targets";
      c.help = "List build/test targets";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "claim target";
      c.wire_command = "claim_target";
      c.args = {
          {.name = "name", .help = "Target name"},
          {.name = "workspace",
           .help = "Workspace to claim for"},
      };
      c.help = "Claim a target for a workspace";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "release target";
      c.wire_command = "release_target";
      c.args = {{.name = "name", .help = "Target name"}};
      c.help = "Release a claimed target";
      out.push_back(std::move(c));
    }

    // -- Pipeline commands --
    {
      CommandSpec c;
      c.path = "show pipeline";
      c.wire_command = "get_pipeline";
      c.args = {
          {.name = "workspace",
           .help = "Workspace name"},
      };
      c.help = "Show pipeline steps for a workspace";
      out.push_back(std::move(c));
    }

    // -- Run commands --
    {
      CommandSpec c;
      c.path = "show runs";
      c.wire_command = "list_runs";
      c.help = "List pipeline runs";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "trigger run";
      c.wire_command = "trigger_run";
      c.args = {
          {.name = "workspace",
           .help = "Workspace to run"},
      };
      c.help = "Trigger a pipeline run";
      out.push_back(std::move(c));
    }

    // -- Agent commands --
    {
      CommandSpec c;
      c.path = "agent";
      c.wire_command = "";
      c.help = "Enter the takt AI agent REPL";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "show agents";
      c.wire_command = "list_agents";
      c.help = "List running agents";
      out.push_back(std::move(c));
    }

    // -- Repo commands --
    {
      CommandSpec c;
      c.path = "show repos";
      c.wire_command = "list_repos";
      c.help = "List configured repositories";
      out.push_back(std::move(c));
    }

    // -- Account commands --
    {
      CommandSpec c;
      c.path = "show accounts";
      c.wire_command = "list_accounts";
      c.help = "List Claude accounts";
      out.push_back(std::move(c));
    }
    {
      CommandSpec c;
      c.path = "show usage";
      c.wire_command = "get_usage";
      c.help = "Show token usage across accounts";
      out.push_back(std::move(c));
    }

    return out;
  }

  auto RenderResponse(const CommandSpec &cmd,
                      const Response &response,
                      Renderer &renderer) const
      -> void override {
    using cli::render::AddColumn;
    using cli::render::AddRow;
    using cli::render::Align;
    using cli::render::Cell;
    using cli::render::Priority;
    using cli::render::RenderFormatted;
    using cli::render::Semantic;

    if (response.error) {
      cli::render::RenderError(
          response.error->code,
          response.error->message,
          response.error->hint, renderer);
      return;
    }

    if (!response.data.empty()) {
      std::string body(response.data.begin(),
                       response.data.end());
      renderer.Out() << body;
      if (!body.empty() && body.back() != '\n') {
        renderer.Out() << '\n';
      }
    }
  }

  auto EventTopicsFor(const CommandSpec &) const
      -> std::vector<std::string> override {
    return {};
  }

  auto RenderEvent(const std::string &,
                   const Event &,
                   Renderer &) const
      -> void override {}

 private:
  TaktCliConfig cfg_;
  std::shared_ptr<Schema> schema_;
};

}  // namespace

auto NewTaktAdapter(TaktCliConfig cfg)
    -> std::unique_ptr<cli::ProductAdapter> {
  return std::make_unique<TaktAdapter>(std::move(cfg));
}

}  // namespace einheit::adapters::takt
