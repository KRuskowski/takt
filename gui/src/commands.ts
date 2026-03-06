/**
 * Command dispatch for the command bar.
 *
 * Returns a result string for brief feedback, or null
 * for tab-switching (handled by App).
 */

import {
  cancelRun,
  claimTarget,
  createWorkspace,
  deleteWorkspace,
  listMetaAgents,
  listRepos,
  listTargets,
  listWorkspaces,
  releaseTarget,
  runMetaAgent,
  startTarget,
  stopTarget,
  triggerRun,
} from "./api";

export type Tab =
  | "dashboard"
  | "agents"
  | "pipeline"
  | "workspaces"
  | "targets"
  | "meta"
  | "settings";

export interface CommandResult {
  tab?: Tab;
  message?: string;
  error?: boolean;
  multi?: boolean;
}

const HELP = [
  "Navigation: dashboard (d), agents (a),",
  "  pipeline (p), workspaces (w/ws),",
  "  targets (t), meta (m), settings (s)",
  "Actions:",
  "  run <workspace>        — trigger pipeline",
  "  cancel run <id>        — cancel run",
  "  ws new <name> <repos>  — create workspace",
  "  ws del <name>          — delete workspace",
  "  claim <target> <ws>    — claim target",
  "  release <target>       — release target",
  "  up <target>            — start VM",
  "  down <target>          — stop VM",
  "  meta run <name>        — run meta agent",
  "  help                   — show this",
].join("\n");

const TAB_MAP: Record<string, Tab> = {
  dashboard: "dashboard",
  d: "dashboard",
  agents: "agents",
  a: "agents",
  pipeline: "pipeline",
  p: "pipeline",
  workspaces: "workspaces",
  ws: "workspaces",
  w: "workspaces",
  targets: "targets",
  t: "targets",
  meta: "meta",
  m: "meta",
  settings: "settings",
  s: "settings",
};

export async function dispatch(
  cmd: string,
): Promise<CommandResult> {
  const parts = cmd.trim().split(/\s+/);
  const group = parts[0];

  // Help.
  if (group === "help" || group === "?") {
    return { message: HELP, multi: true };
  }

  // Tab navigation.
  if (group in TAB_MAP) {
    return { tab: TAB_MAP[group] };
  }

  try {
    switch (group) {
      case "run": {
        const ws = parts[1];
        if (!ws) return { error: true, message: "Usage: run <workspace>" };
        const data = await triggerRun(ws);
        return { message: `Run #${data.run_id} triggered` };
      }

      case "cancel": {
        if (parts[1] === "run" && parts[2]) {
          await cancelRun(Number(parts[2]));
          return { message: `Run #${parts[2]} cancelled` };
        }
        return { error: true, message: "Usage: cancel run <id>" };
      }

      case "claim": {
        const target = parts[1];
        const ws = parts[2];
        if (!target || !ws) {
          return {
            error: true,
            message: "Usage: claim <target> <ws>",
          };
        }
        await claimTarget(target, ws);
        return { message: `${target} claimed for ${ws}` };
      }

      case "release": {
        const target = parts[1];
        if (!target) {
          return {
            error: true,
            message: "Usage: release <target>",
          };
        }
        await releaseTarget(target);
        return { message: `${target} released` };
      }

      case "up": {
        const target = parts[1];
        if (!target) {
          return {
            error: true, message: "Usage: up <target>",
          };
        }
        await startTarget(target);
        return { message: `${target} starting` };
      }

      case "down": {
        const target = parts[1];
        if (!target) {
          return {
            error: true, message: "Usage: down <target>",
          };
        }
        await stopTarget(target);
        return { message: `${target} stopping` };
      }

      default:
        break;
    }

    // Multi-word commands.
    if (group === "ws") {
      const sub = parts[1];
      if (sub === "new") {
        const name = parts[2];
        const repos = parts.slice(3);
        if (!name || repos.length === 0) {
          return {
            error: true,
            message: "Usage: ws new <name> <repos...>",
          };
        }
        await createWorkspace(name, repos);
        return { message: `Creating ${name}...` };
      }
      if (sub === "del") {
        const name = parts[2];
        if (!name) {
          return {
            error: true,
            message: "Usage: ws del <name>",
          };
        }
        await deleteWorkspace(name);
        return { message: `Deleted ${name}` };
      }
      // Fall through to tab switch.
      return { tab: "workspaces" };
    }

    if (group === "meta") {
      const sub = parts[1];
      if (sub === "run") {
        const name = parts.slice(2).join(" ");
        if (!name) {
          return {
            error: true,
            message: "Usage: meta run <name>",
          };
        }
        const agents = await listMetaAgents();
        const agent = agents.agents.find(
          (a) => a.name === name,
        );
        if (!agent) {
          return {
            error: true,
            message: `Meta agent '${name}' not found`,
          };
        }
        const data = await runMetaAgent(agent.id);
        return {
          message: `Run #${data.run_id} for ${name}`,
        };
      }
      return { tab: "meta" };
    }

    return { error: true, message: `Unknown: ${group}` };
  } catch (e) {
    return {
      error: true,
      message: e instanceof Error
        ? e.message
        : "Command failed",
    };
  }
}

// -- Tab completion --

const COMMANDS = [
  "dashboard", "d", "agents", "a", "pipeline",
  "p", "workspaces", "ws", "w", "targets", "t",
  "meta", "m", "settings", "s", "run", "cancel",
  "claim", "release", "up", "down", "help",
];

function prefix(strs: string[]): string {
  if (strs.length === 0) return "";
  let p = strs[0];
  for (let i = 1; i < strs.length; i++) {
    while (!strs[i].startsWith(p)) {
      p = p.slice(0, -1);
    }
  }
  return p;
}

export async function getCompletions(
  input: string,
): Promise<string[]> {
  const parts = input.split(/\s+/);
  const cur = parts[parts.length - 1] ?? "";
  const cmd = parts[0] ?? "";
  const match = (items: string[]) =>
    items.filter((s) => s.startsWith(cur));

  if (parts.length <= 1) {
    return match(COMMANDS);
  }

  try {
    switch (cmd) {
      case "run": {
        if (parts.length === 2) {
          const d = await listWorkspaces();
          return match(
            d.workspaces.map((w) => w.name),
          );
        }
        break;
      }
      case "cancel":
        if (parts.length === 2) {
          return match(["run"]);
        }
        break;
      case "up":
      case "down":
        if (parts.length === 2) {
          const d = await listTargets();
          return match(
            d.targets
              .filter(
                (x) =>
                  x.type === "vm"
                  && !x.template,
              )
              .map((x) => x.name),
          );
        }
        break;
      case "claim":
        if (parts.length === 2) {
          const d = await listTargets();
          return match(
            d.targets
              .filter((x) => !x.template)
              .map((x) => x.name),
          );
        }
        if (parts.length === 3) {
          const d = await listWorkspaces();
          return match(
            d.workspaces.map((w) => w.name),
          );
        }
        break;
      case "release":
        if (parts.length === 2) {
          const d = await listTargets();
          return match(
            d.targets
              .filter(
                (x) => !x.template && x.lock,
              )
              .map((x) => x.name),
          );
        }
        break;
      case "ws":
        if (parts.length === 2) {
          return match(["new", "del"]);
        }
        if (
          parts[1] === "del" && parts.length === 3
        ) {
          const d = await listWorkspaces();
          return match(
            d.workspaces.map((w) => w.name),
          );
        }
        if (
          parts[1] === "new" && parts.length >= 4
        ) {
          const d = await listRepos();
          const used = new Set(parts.slice(3));
          return match(
            d.repos
              .map((r) => r.name)
              .filter((n) => !used.has(n)),
          );
        }
        break;
      case "meta":
        if (parts.length === 2) {
          return match(["run"]);
        }
        if (
          parts[1] === "run" && parts.length === 3
        ) {
          const d = await listMetaAgents();
          return match(
            d.agents.map((a) => a.name),
          );
        }
        break;
    }
  } catch {
    // API unavailable — no completions.
  }

  return [];
}

export { prefix as commonPrefix };
