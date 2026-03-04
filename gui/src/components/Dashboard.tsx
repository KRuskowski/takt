import { Box, Grid, Table, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type Agent,
  type Run,
  type Workspace,
  listAgents,
  listRuns,
  listWorkspaces,
} from "../api";
import StatusBadge from "./StatusBadge";

export default function Dashboard() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);

  const refresh = useCallback(async () => {
    try {
      const [ws, r, a] = await Promise.all([
        listWorkspaces(),
        listRuns(),
        listAgents(),
      ]);
      setWorkspaces(ws.workspaces);
      setRuns(r.runs);
      setAgents(a.agents);
    } catch { /* service unavailable */ }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  return (
    <Grid templateColumns="repeat(auto-fit, minmax(360px, 1fr))" gap={2}>
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
                <Table.Row key={ws.name} _hover={{ bg: "#2a2a2a" }}>
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
                <Th>Trigger</Th>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {runs.slice(0, 10).map((r) => (
                <Table.Row key={r.id} _hover={{ bg: "#2a2a2a" }}>
                  <Td>{r.id}</Td>
                  <Td>{r.workspace}</Td>
                  <Td><StatusBadge status={r.status} /></Td>
                  <Td>{r.trigger}</Td>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        )}
      </Panel>

      {/* Agents */}
      <Panel title={`Agents (${agents.filter((a) => a.state === "running").length} active)`}>
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
                <Table.Row key={a.agent_id} _hover={{ bg: "#2a2a2a" }}>
                  <Td>{a.role}</Td>
                  <Td>{a.workspace}</Td>
                  <Td><StatusBadge status={a.state} /></Td>
                  <Td textAlign="right">${a.total_cost_usd.toFixed(2)}</Td>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        )}
      </Panel>
    </Grid>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2}>
      <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
        {title}
      </Text>
      {children}
    </Box>
  );
}

function Th({ children, ...props }: { children: React.ReactNode; textAlign?: string }) {
  return (
    <Table.ColumnHeader
      fontSize="11px"
      color="#737373"
      fontWeight={500}
      py={1}
      px={1.5}
      borderColor="#2e2e2e"
      {...props}
    >
      {children}
    </Table.ColumnHeader>
  );
}

function Td({ children, ...props }: { children: React.ReactNode; textAlign?: string }) {
  return (
    <Table.Cell
      fontSize="11px"
      py={1}
      px={1.5}
      borderColor="#2e2e2e"
      {...props}
    >
      {children}
    </Table.Cell>
  );
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <Text textAlign="center" py={4} color="#737373" fontSize="11px">
      {children}
    </Text>
  );
}
