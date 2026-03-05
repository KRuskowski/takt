/** REST client for takt API. */

const BASE = "http://localhost:7433";

async function get<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`);
  const json = await resp.json();
  if (json.status === "error") throw new Error(json.message);
  return json.data as T;
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await resp.json();
  if (json.status === "error") throw new Error(json.message);
  return json.data as T;
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const json = await resp.json();
  if (json.status === "error") throw new Error(json.message);
  return json.data as T;
}

async function del<T>(path: string): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    method: "DELETE",
  });
  const json = await resp.json();
  if (json.status === "error") throw new Error(json.message);
  return json.data as T;
}

// -- Workspaces --

export interface Workspace {
  name: string;
  path: string;
  repos: string[];
  branch: string;
  chroot: boolean;
  last_active: number;
}

export const listWorkspaces = () =>
  get<{ workspaces: Workspace[] }>("/api/workspaces");

export const createWorkspace = (
  name: string, repos: string[], chroot = false,
) => post("/api/workspaces", { name, repos, chroot });

export const deleteWorkspace = (name: string) =>
  del(`/api/workspaces/${name}`);

export const getWorkspaceStatus = (name: string) =>
  get<{
    repos: { repo: string; branch: string; status: string }[];
  }>(`/api/workspaces/${name}/status`);

// -- Repos --

export interface RepoInfo {
  name: string;
  push_order: number;
}

export const listRepos = () =>
  get<{ repos: RepoInfo[] }>("/api/repos");

// -- Targets --

export interface Target {
  name: string;
  type: string;
  host: string;
  description: string;
  template: boolean;
  vm_state: string | null;
  lock: { workspace: string; claimed_at: string } | null;
}

export const listTargets = () =>
  get<{ targets: Target[] }>("/api/targets");

export const claimTarget = (
  name: string, workspace: string,
) => post(`/api/targets/${name}/claim`, { workspace });

export const releaseTarget = (name: string) =>
  post(`/api/targets/${name}/release`);

export const startTarget = (name: string) =>
  post(`/api/targets/${name}/up`);

export const stopTarget = (name: string) =>
  post(`/api/targets/${name}/down`);

// -- Runs --

export interface Run {
  id: number;
  workspace: string;
  status: string;
  trigger: string;
  repos_json: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface Step {
  id: number;
  run_id: number;
  seq: number;
  name: string;
  step_type: string;
  status: string;
  cost_usd: number;
  num_turns: number;
}

export const listRuns = (workspace?: string, limit = 20) => {
  const params = new URLSearchParams();
  if (workspace) params.set("workspace", workspace);
  params.set("limit", String(limit));
  return get<{ runs: Run[] }>(`/api/runs?${params}`);
};

export const getRun = (id: number) =>
  get<{ run: Run; steps: Step[] }>(`/api/runs/${id}`);

export const triggerRun = (workspace: string) =>
  post<{ run_id: number }>("/api/runs", { workspace });

export const cancelRun = (id: number) =>
  post(`/api/runs/${id}/cancel`);

export const getStepOutput = (
  runId: number, stepId: number, from = 0,
) => get<{ lines: OutputLine[] }>(
  `/api/runs/${runId}/steps/${stepId}/output?from=${from}`,
);

// -- Agents --

export interface Agent {
  agent_id: string;
  step_id: number;
  workspace: string;
  role: string;
  model: string;
  state: string;
  num_turns: number;
  total_cost_usd: number;
  run_id: number;
}

export const listAgents = () =>
  get<{ agents: Agent[] }>("/api/agents");

export const cancelAgent = (id: string) =>
  post(`/api/agents/${id}/cancel`);

// -- Pipeline --

export interface PipelineStep {
  name: string;
  step_type: string;
}

export const getPipeline = (workspace: string) =>
  get<{ steps: PipelineStep[] }>(
    `/api/pipeline/${workspace}`,
  );

export const setPipeline = (
  workspace: string, steps: unknown[],
) => put(`/api/pipeline/${workspace}`, { steps });

// -- Meta agents --

export interface MetaAgent {
  id: number;
  name: string;
  description: string;
  model: string;
}

export interface MetaAgentRun {
  id: number;
  meta_agent_id: number;
  status: string;
  error: string | null;
  cost_usd: number;
  num_turns: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export const listMetaAgents = () =>
  get<{ agents: MetaAgent[] }>("/api/meta-agents");

export const runMetaAgent = (id: number) =>
  post<{ run_id: number }>(`/api/meta-agents/${id}/run`);

export const listMetaAgentRuns = (id: number) =>
  get<{ runs: MetaAgentRun[] }>(
    `/api/meta-agents/${id}/runs`,
  );

export const getMetaAgentOutput = (
  id: number, runId: number, from = 0,
) => get<{ lines: OutputLine[] }>(
  `/api/meta-agents/${id}/runs/${runId}/output?from=${from}`,
);

export const cancelMetaRun = (
  id: number, runId: number,
) => post(`/api/meta-agents/${id}/runs/${runId}/cancel`);

// -- Ping --

export const ping = () =>
  get<{ pong: boolean }>("/api/ping");

// -- Output lines --

export interface OutputLine {
  line_no: number;
  kind: string;
  content: string;
  meta: Record<string, unknown>;
  ts: string;
}
