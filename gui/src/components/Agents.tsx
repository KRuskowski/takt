import { Box, Button, Flex, Table, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import { type Agent, cancelAgent, listAgents } from "../api";
import AgentOutput from "./AgentOutput";
import StatusBadge from "./StatusBadge";

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selected, setSelected] = useState<Agent | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listAgents();
      setAgents(data.agents);
    } catch { /* */ }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const handleCancel = async (agentId: string) => {
    await cancelAgent(agentId);
    refresh();
  };

  return (
    <Flex gap={2} h="100%">
      <Box flex="0 0 50%" bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2} overflow="auto">
        <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
          Agents
        </Text>
        {agents.length === 0 ? (
          <Text textAlign="center" py={4} color="#737373" fontSize="11px">No agents</Text>
        ) : (
          <Table.Root size="sm" variant="line">
            <Table.Header>
              <Table.Row bg="transparent">
                <Th>Role</Th>
                <Th>Workspace</Th>
                <Th>Model</Th>
                <Th>Status</Th>
                <Th textAlign="right">Turns</Th>
                <Th textAlign="right">Cost</Th>
                <Th w="1px">{""}</Th>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {agents.map((a) => (
                <Table.Row
                  key={a.agent_id}
                  cursor="pointer"
                  bg={selected?.agent_id === a.agent_id ? "#2a2a2a" : undefined}
                  _hover={{ bg: "#2a2a2a" }}
                  onClick={() => setSelected(a)}
                >
                  <Td>{a.role}</Td>
                  <Td>{a.workspace}</Td>
                  <Td>{a.model.replace("claude-", "").split("-")[0]}</Td>
                  <Td><StatusBadge status={a.state} /></Td>
                  <Td textAlign="right">{a.num_turns}</Td>
                  <Td textAlign="right">${a.total_cost_usd.toFixed(2)}</Td>
                  <Td>
                    {a.state === "running" && (
                      <Button
                        size="2xs"
                        variant="outline"
                        colorPalette="red"
                        onClick={(e) => { e.stopPropagation(); handleCancel(a.agent_id); }}
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

      <Box flex={1} bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2} overflow="hidden" display="flex" flexDirection="column">
        <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
          Output
        </Text>
        {selected ? (
          <AgentOutput runId={selected.run_id} stepId={selected.step_id} />
        ) : (
          <Text textAlign="center" py={4} color="#737373" fontSize="11px">Select an agent to view output</Text>
        )}
      </Box>
    </Flex>
  );
}

function Th({ children, ...props }: { children: React.ReactNode; textAlign?: string; w?: string }) {
  return <Table.ColumnHeader fontSize="11px" color="#737373" fontWeight={500} py={1} px={1.5} borderColor="#2e2e2e" {...props}>{children}</Table.ColumnHeader>;
}

function Td({ children, ...props }: { children: React.ReactNode; textAlign?: string }) {
  return <Table.Cell fontSize="11px" py={1} px={1.5} borderColor="#2e2e2e" {...props}>{children}</Table.Cell>;
}
