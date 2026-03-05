import {
  Box, Button, Flex, NativeSelect, Table,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type PipelineStep,
  type Run,
  type Step,
  type Workspace,
  cancelRun,
  getPipeline,
  getRun,
  listRuns,
  listWorkspaces,
  triggerRun,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError, showSuccess } from "../toast";
import { duration, relativeTime } from "../utils";
import AgentOutput from "./AgentOutput";
import StatusBadge from "./StatusBadge";
import { Empty, PanelHeader, Td, Th } from "./shared";

export default function Pipeline() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRun, setSelectedRun] = useState<Run | null>(
    null,
  );
  const [steps, setSteps] = useState<Step[]>([]);
  const [selectedStep, setSelectedStep] =
    useState<Step | null>(null);
  const [workspaces, setWorkspaces] =
    useState<Workspace[]>([]);
  const [wsFilter, setWsFilter] = useState("");
  const [pipeline, setPipelineSteps] =
    useState<PipelineStep[]>([]);

  const refreshRuns = useCallback(async () => {
    try {
      const [data, ws] = await Promise.all([
        listRuns(wsFilter || undefined, 50),
        listWorkspaces(),
      ]);
      setRuns(data.runs);
      setWorkspaces(ws.workspaces);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Refresh failed",
      );
    }
  }, [wsFilter]);

  useSSERefresh(
    ["step.update", "pipeline.event"], refreshRuns,
  );

  // Reload when filter changes.
  useEffect(() => { refreshRuns(); }, [refreshRuns]);

  const selectRun = async (run: Run) => {
    setSelectedRun(run);
    setSelectedStep(null);
    try {
      const [data, pl] = await Promise.all([
        getRun(run.id),
        getPipeline(run.workspace),
      ]);
      setSteps(data.steps);
      setPipelineSteps(pl.steps);
    } catch {
      setSteps([]);
      setPipelineSteps([]);
    }
  };

  const handleCancel = async (runId: number) => {
    try {
      await cancelRun(runId);
      refreshRuns();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Cancel failed",
      );
    }
  };

  const handleTrigger = async () => {
    const ws = wsFilter || workspaces[0]?.name;
    if (!ws) {
      showError("No workspace selected");
      return;
    }
    try {
      const data = await triggerRun(ws);
      showSuccess(`Run #${data.run_id} triggered`);
      refreshRuns();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Trigger failed",
      );
    }
  };

  return (
    <Flex gap={2} h="100%">
      {/* Runs list */}
      <Box
        flex="0 0 400px"
        bg="#1c1c1c"
        border="1px solid #2e2e2e"
        borderRadius="4px"
        p={2}
        overflow="auto"
        display="flex"
        flexDirection="column"
      >
        <Flex
          justify="space-between"
          align="center"
          mb={1.5}
        >
          <PanelHeader>Pipeline Runs</PanelHeader>
          <Flex gap={1} align="center">
            <NativeSelect.Root size="xs" w="140px">
              <NativeSelect.Field
                fontSize="10px"
                bg="#242424"
                border="1px solid #2e2e2e"
                color="#d4d4d4"
                h="22px"
                value={wsFilter}
                onChange={(e) =>
                  setWsFilter(e.target.value)
                }
              >
                <option value="">All workspaces</option>
                {workspaces.map((ws) => (
                  <option key={ws.name} value={ws.name}>
                    {ws.name}
                  </option>
                ))}
              </NativeSelect.Field>
            </NativeSelect.Root>
            <Button
              size="2xs"
              variant="outline"
              onClick={handleTrigger}
            >
              Run
            </Button>
          </Flex>
        </Flex>
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
                  onClick={() => selectRun(r)}
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
                  <Td>
                    {(r.status === "queued"
                      || r.status === "running") && (
                      <Button
                        size="2xs"
                        variant="outline"
                        colorPalette="red"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCancel(r.id);
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

      {/* Steps + output + config */}
      <Flex flex={1} direction="column" gap={2}>
        {selectedRun && (
          <Box
            bg="#1c1c1c"
            border="1px solid #2e2e2e"
            borderRadius="4px"
            p={2}
          >
            <PanelHeader>
              Steps — Run #{selectedRun.id}
            </PanelHeader>
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>Seq</Th>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Status</Th>
                  <Th textAlign="right">Turns</Th>
                  <Th textAlign="right">Cost</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {steps.map((s) => (
                  <Table.Row
                    key={s.id}
                    cursor="pointer"
                    bg={
                      selectedStep?.id === s.id
                        ? "#2a2a2a"
                        : undefined
                    }
                    _hover={{ bg: "#2a2a2a" }}
                    onClick={() => setSelectedStep(s)}
                  >
                    <Td>{s.seq}</Td>
                    <Td>{s.name}</Td>
                    <Td>{s.step_type}</Td>
                    <Td>
                      <StatusBadge status={s.status} />
                    </Td>
                    <Td textAlign="right">
                      {s.num_turns}
                    </Td>
                    <Td textAlign="right">
                      ${s.cost_usd.toFixed(2)}
                    </Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          </Box>
        )}

        {/* Pipeline config */}
        {selectedRun && pipeline.length > 0 && (
          <Box
            bg="#1c1c1c"
            border="1px solid #2e2e2e"
            borderRadius="4px"
            p={2}
          >
            <PanelHeader>
              Pipeline — {selectedRun.workspace}
            </PanelHeader>
            <Table.Root size="sm" variant="line">
              <Table.Header>
                <Table.Row bg="transparent">
                  <Th>#</Th>
                  <Th>Name</Th>
                  <Th>Type</Th>
                </Table.Row>
              </Table.Header>
              <Table.Body>
                {pipeline.map((ps, i) => (
                  <Table.Row
                    key={`${ps.name}-${i}`}
                    _hover={{ bg: "#2a2a2a" }}
                  >
                    <Td>{i + 1}</Td>
                    <Td>{ps.name}</Td>
                    <Td>{ps.step_type}</Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          </Box>
        )}

        {selectedStep && selectedRun && (
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
              Output — {selectedStep.name}
            </PanelHeader>
            <AgentOutput
              runId={selectedRun.id}
              stepId={selectedStep.id}
            />
          </Box>
        )}
      </Flex>
    </Flex>
  );
}
