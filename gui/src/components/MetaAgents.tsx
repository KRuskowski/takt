import {
  Box, Button, Flex, Table,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type MetaAgent,
  type MetaAgentRun,
  cancelMetaRun,
  getMetaAgentOutput,
  listMetaAgentRuns,
  listMetaAgents,
  runMetaAgent,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError, showSuccess } from "../toast";
import { duration, relativeTime } from "../utils";
import AgentOutput from "./AgentOutput";
import StatusBadge from "./StatusBadge";
import { Empty, PanelHeader, Td, Th } from "./shared";

export default function MetaAgents() {
  const [agents, setAgents] = useState<MetaAgent[]>([]);
  const [selected, setSelected] =
    useState<MetaAgent | null>(null);
  const [runs, setRuns] = useState<MetaAgentRun[]>([]);
  const [selectedRun, setSelectedRun] =
    useState<MetaAgentRun | null>(null);

  const refreshAgents = useCallback(async () => {
    try {
      const data = await listMetaAgents();
      setAgents(data.agents);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Refresh failed",
      );
    }
  }, []);

  useSSERefresh(["step.update"], refreshAgents);

  const refreshRuns = useCallback(async () => {
    if (!selected) return;
    try {
      const data = await listMetaAgentRuns(selected.id);
      setRuns(data.runs);
    } catch {
      setRuns([]);
    }
  }, [selected]);

  useEffect(() => { refreshRuns(); }, [refreshRuns]);

  const handleRun = async (agent: MetaAgent) => {
    try {
      const data = await runMetaAgent(agent.id);
      showSuccess(
        `Run #${data.run_id} started for ${agent.name}`,
      );
      if (selected?.id === agent.id) refreshRuns();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Run failed",
      );
    }
  };

  const handleCancel = async (run: MetaAgentRun) => {
    if (!selected) return;
    try {
      await cancelMetaRun(selected.id, run.id);
      showSuccess(`Run #${run.id} cancelled`);
      refreshRuns();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Cancel failed",
      );
    }
  };

  const selectAgent = (agent: MetaAgent) => {
    setSelected(agent);
    setSelectedRun(null);
    setRuns([]);
  };

  const fetchOutput = selectedRun && selected
    ? () => getMetaAgentOutput(
        selected.id, selectedRun.id,
      )
    : undefined;

  return (
    <Flex gap={2} h="100%">
      {/* Agent list */}
      <Box
        flex="0 0 240px"
        bg="#1c1c1c"
        border="1px solid #2e2e2e"
        borderRadius="4px"
        p={2}
        overflow="auto"
      >
        <PanelHeader>Meta Agents</PanelHeader>
        {agents.length === 0 ? (
          <Empty>No meta agents</Empty>
        ) : (
          <Table.Root size="sm" variant="line">
            <Table.Header>
              <Table.Row bg="transparent">
                <Th>Name</Th>
                <Th w="1px">{""}</Th>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {agents.map((a) => (
                <Table.Row
                  key={a.id}
                  cursor="pointer"
                  bg={
                    selected?.id === a.id
                      ? "#2a2a2a"
                      : undefined
                  }
                  _hover={{ bg: "#2a2a2a" }}
                  onClick={() => selectAgent(a)}
                >
                  <Td>{a.name}</Td>
                  <Td>
                    <Button
                      size="2xs"
                      variant="outline"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleRun(a);
                      }}
                    >
                      Run
                    </Button>
                  </Td>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        )}
      </Box>

      {/* Runs + output */}
      <Flex flex={1} direction="column" gap={2}>
        {/* Runs for selected agent */}
        <Box
          bg="#1c1c1c"
          border="1px solid #2e2e2e"
          borderRadius="4px"
          p={2}
          overflow="auto"
          flex="0 0 auto"
          maxH="40%"
        >
          <PanelHeader>
            {selected
              ? `Runs — ${selected.name}`
              : "Runs"}
          </PanelHeader>
          {!selected ? (
            <Empty>Select a meta agent</Empty>
          ) : runs.length === 0 ? (
            <Empty>No runs</Empty>
          ) : (
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>ID</Th>
                  <Th>Status</Th>
                  <Th>Started</Th>
                  <Th>Duration</Th>
                  <Th textAlign="right">Cost</Th>
                  <Th w="1px">{""}</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {runs.map((r) => (
                  <Table.Row
                    key={r.id}
                    cursor="pointer"
                    bg={
                      selectedRun?.id === r.id
                        ? "#2a2a2a"
                        : undefined
                    }
                    _hover={{ bg: "#2a2a2a" }}
                    onClick={() => setSelectedRun(r)}
                  >
                    <Td>{r.id}</Td>
                    <Td>
                      <StatusBadge status={r.status} />
                    </Td>
                    <Td>
                      {relativeTime(r.started_at)}
                    </Td>
                    <Td>
                      {duration(
                        r.started_at, r.finished_at,
                      )}
                    </Td>
                    <Td textAlign="right">
                      ${r.cost_usd.toFixed(2)}
                    </Td>
                    <Td>
                      {(r.status === "queued"
                        || r.status === "running") && (
                        <Button
                          size="2xs"
                          variant="outline"
                          colorPalette="red"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCancel(r);
                          }}
                        >
                          Cancel
                        </Button>
                      )}
                    </Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          )}
        </Box>

        {/* Output */}
        <Box
          flex={1}
          bg="#1c1c1c"
          border="1px solid #2e2e2e"
          borderRadius="4px"
          p={2}
          display="flex"
          flexDirection="column"
          overflow="hidden"
        >
          <PanelHeader>
            {selectedRun
              ? `Output — Run #${selectedRun.id}`
              : "Output"}
          </PanelHeader>
          {selectedRun && fetchOutput ? (
            <AgentOutput
              fetchLines={fetchOutput}
              sseTopic={
                `meta.output.run-${selectedRun.id}`
              }
            />
          ) : (
            <Empty>Select a run to view output</Empty>
          )}
        </Box>
      </Flex>
    </Flex>
  );
}
