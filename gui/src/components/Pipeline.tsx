import { Box, Button, Flex, Table, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type Run,
  type Step,
  cancelRun,
  getRun,
  listRuns,
} from "../api";
import AgentOutput from "./AgentOutput";
import StatusBadge from "./StatusBadge";

export default function Pipeline() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedRun, setSelectedRun] = useState<Run | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [selectedStep, setSelectedStep] = useState<Step | null>(null);

  const refreshRuns = useCallback(async () => {
    try {
      const data = await listRuns(undefined, 50);
      setRuns(data.runs);
    } catch { /* */ }
  }, []);

  useEffect(() => {
    refreshRuns();
    const id = setInterval(refreshRuns, 5000);
    return () => clearInterval(id);
  }, [refreshRuns]);

  const selectRun = async (run: Run) => {
    setSelectedRun(run);
    setSelectedStep(null);
    try {
      const data = await getRun(run.id);
      setSteps(data.steps);
    } catch {
      setSteps([]);
    }
  };

  const handleCancel = async (runId: number) => {
    await cancelRun(runId);
    refreshRuns();
  };

  return (
    <Flex gap={2} h="100%">
      {/* Runs list */}
      <Box flex="0 0 380px" bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2} overflow="auto">
        <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
          Pipeline Runs
        </Text>
        {runs.length === 0 ? (
          <Text textAlign="center" py={4} color="#737373" fontSize="11px">No runs</Text>
        ) : (
          <Table.Root size="sm" variant="line">
            <Table.Header>
              <Table.Row bg="transparent">
                <Th>ID</Th>
                <Th>Workspace</Th>
                <Th>Status</Th>
                <Th>Trigger</Th>
                <Th w="1px">{""}</Th>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {runs.map((r) => (
                <Table.Row
                  key={r.id}
                  cursor="pointer"
                  bg={selectedRun?.id === r.id ? "#2a2a2a" : undefined}
                  _hover={{ bg: "#2a2a2a" }}
                  onClick={() => selectRun(r)}
                >
                  <Td>{r.id}</Td>
                  <Td>{r.workspace}</Td>
                  <Td><StatusBadge status={r.status} /></Td>
                  <Td>{r.trigger}</Td>
                  <Td>
                    {(r.status === "queued" || r.status === "running") && (
                      <Button
                        size="2xs"
                        variant="outline"
                        colorPalette="red"
                        onClick={(e) => { e.stopPropagation(); handleCancel(r.id); }}
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

      {/* Steps + output */}
      <Flex flex={1} direction="column" gap={2}>
        {selectedRun && (
          <Box bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2}>
            <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
              Steps — Run #{selectedRun.id}
            </Text>
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
                    bg={selectedStep?.id === s.id ? "#2a2a2a" : undefined}
                    _hover={{ bg: "#2a2a2a" }}
                    onClick={() => setSelectedStep(s)}
                  >
                    <Td>{s.seq}</Td>
                    <Td>{s.name}</Td>
                    <Td>{s.step_type}</Td>
                    <Td><StatusBadge status={s.status} /></Td>
                    <Td textAlign="right">{s.num_turns}</Td>
                    <Td textAlign="right">${s.cost_usd.toFixed(2)}</Td>
                  </Table.Row>
                ))}
              </Table.Body>
            </Table.Root>
          </Box>
        )}

        {selectedStep && selectedRun && (
          <Box flex={1} bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2} display="flex" flexDirection="column" overflow="hidden">
            <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
              Output — {selectedStep.name}
            </Text>
            <AgentOutput runId={selectedRun.id} stepId={selectedStep.id} />
          </Box>
        )}
      </Flex>
    </Flex>
  );
}

function Th({ children, ...props }: { children: React.ReactNode; textAlign?: string; w?: string }) {
  return <Table.ColumnHeader fontSize="11px" color="#737373" fontWeight={500} py={1} px={1.5} borderColor="#2e2e2e" {...props}>{children}</Table.ColumnHeader>;
}

function Td({ children, ...props }: { children: React.ReactNode; textAlign?: string }) {
  return <Table.Cell fontSize="11px" py={1} px={1.5} borderColor="#2e2e2e" {...props}>{children}</Table.Cell>;
}
