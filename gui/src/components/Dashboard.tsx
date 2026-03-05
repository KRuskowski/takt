import { Box, Grid, Table, Text } from "@chakra-ui/react";
import { useCallback, useState } from "react";
import {
  type Agent,
  type MetaAgent,
  type Run,
  type Target,
  type Workspace,
  listAgents,
  listMetaAgents,
  listRuns,
  listTargets,
  listWorkspaces,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError } from "../toast";
import { duration, relativeTime } from "../utils";
import { Empty, Panel, Td, Th } from "./shared";
import StatusBadge from "./StatusBadge";

const VM_STATE_COLOR: Record<string, string> = {
  running: "#22c55e",
  "shut off": "#737373",
  paused: "#eab308",
};

export default function Dashboard() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>(
    [],
  );
  const [runs, setRuns] = useState<Run[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [metaAgents, setMetaAgents] = useState<MetaAgent[]>(
    [],
  );

  const refresh = useCallback(async () => {
    try {
      const [ws, r, a, t, m] = await Promise.all([
        listWorkspaces(),
        listRuns(),
        listAgents(),
        listTargets(),
        listMetaAgents(),
      ]);
      setWorkspaces(ws.workspaces);
      setRuns(r.runs);
      setAgents(a.agents);
      setTargets(t.targets);
      setMetaAgents(m.agents);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Refresh failed",
      );
    }
  }, []);

  useSSERefresh(
    [
      "step.update", "pipeline.event",
      "workspace.event",
    ],
    refresh,
  );

  const totalCost = agents.reduce(
    (sum, a) => sum + a.total_cost_usd, 0,
  );

  return (
    <Box>
      {totalCost > 0 && (
        <Text
          fontSize="10px"
          color="#737373"
          mb={1}
          textAlign="right"
        >
          Total cost: ${totalCost.toFixed(2)}
        </Text>
      )}
      <Grid
        templateColumns={
          "repeat(auto-fit, minmax(360px, 1fr))"
        }
        gap={2}
      >
        {/* Workspaces */}
        <Panel title={`Workspaces (${workspaces.length})`}>
          {workspaces.length === 0 ? (
            <Empty>No workspaces</Empty>
          ) : (
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>Name</Th>
                  <Th>Branch</Th>
                  <Th>Repos</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {workspaces.map((ws) => (
                  <Table.Row
                    key={ws.name}
                    _hover={{ bg: "#2a2a2a" }}
                  >
                    <Td>{ws.name}</Td>
                    <Td>{ws.branch}</Td>
                    <Td>{ws.repos.length}</Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          )}
        </Panel>

        {/* Recent runs */}
        <Panel title={`Recent Runs (${runs.length})`}>
          {runs.length === 0 ? (
            <Empty>No runs</Empty>
          ) : (
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>ID</Th>
                  <Th>Workspace</Th>
                  <Th>Status</Th>
                  <Th>Started</Th>
                  <Th>Duration</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {runs.slice(0, 10).map((r) => (
                  <Table.Row
                    key={r.id}
                    _hover={{ bg: "#2a2a2a" }}
                  >
                    <Td>{r.id}</Td>
                    <Td>{r.workspace}</Td>
                    <Td>
                      <StatusBadge status={r.status} />
                    </Td>
                    <Td>{relativeTime(r.started_at)}</Td>
                    <Td>
                      {duration(
                        r.started_at, r.finished_at,
                      )}
                    </Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          )}
        </Panel>

        {/* Agents */}
        <Panel
          title={
            `Agents (${agents.filter((a) => a.state === "running").length} active)`
          }
        >
          {agents.length === 0 ? (
            <Empty>No agents</Empty>
          ) : (
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>Role</Th>
                  <Th>Workspace</Th>
                  <Th>Status</Th>
                  <Th textAlign="right">Cost</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {agents.map((a) => (
                  <Table.Row
                    key={a.agent_id}
                    _hover={{ bg: "#2a2a2a" }}
                  >
                    <Td>{a.role}</Td>
                    <Td>{a.workspace}</Td>
                    <Td>
                      <StatusBadge status={a.state} />
                    </Td>
                    <Td textAlign="right">
                      ${a.total_cost_usd.toFixed(2)}
                    </Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          )}
        </Panel>

        {/* Targets */}
        <Panel title={`Targets (${targets.length})`}>
          {targets.length === 0 ? (
            <Empty>No targets</Empty>
          ) : (
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>State</Th>
                  <Th>Claimed By</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {targets.map((t) => (
                  <Table.Row
                    key={t.name}
                    _hover={{ bg: "#2a2a2a" }}
                  >
                    <Td>
                      {t.name}
                      {t.template && (
                        <Text
                          as="span"
                          fontSize="9px"
                          color="#737373"
                          ml={1}
                        >
                          [tpl]
                        </Text>
                      )}
                    </Td>
                    <Td>{t.type}</Td>
                    <Td>
                      {t.vm_state ? (
                        <Text
                          as="span"
                          color={
                            VM_STATE_COLOR[t.vm_state]
                              ?? "#737373"
                          }
                        >
                          {t.vm_state}
                        </Text>
                      ) : (
                        "—"
                      )}
                    </Td>
                    <Td>
                      {t.lock ? t.lock.workspace : "—"}
                    </Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          )}
        </Panel>

        {/* Meta Agents */}
        <Panel
          title={`Meta Agents (${metaAgents.length})`}
        >
          {metaAgents.length === 0 ? (
            <Empty>No meta agents</Empty>
          ) : (
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>Name</Th>
                  <Th>Model</Th>
                  <Th>Description</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {metaAgents.map((m) => (
                  <Table.Row
                    key={m.id}
                    _hover={{ bg: "#2a2a2a" }}
                  >
                    <Td>{m.name}</Td>
                    <Td>{m.model}</Td>
                    <Td>{m.description}</Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          )}
        </Panel>
      </Grid>
    </Box>
  );
}
