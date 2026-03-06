import {
  Box, Flex, IconButton, Table,
} from "@chakra-ui/react";
import { useCallback, useState } from "react";
import {
  RiRobotLine,
  RiStopCircleLine,
  RiTerminalBoxLine,
} from "@remixicon/react";
import {
  type Agent, cancelAgent, listAgents,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError } from "../toast";
import AgentOutput from "./AgentOutput";
import StatusBadge from "./StatusBadge";
import { Empty, PanelHeader, Td, Th } from "./shared";

export default function Agents() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selected, setSelected] = useState<Agent | null>(
    null,
  );

  const refresh = useCallback(async () => {
    try {
      const data = await listAgents();
      setAgents(data.agents);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Refresh failed",
      );
    }
  }, []);

  useSSERefresh(["step.update"], refresh);

  const handleCancel = async (agentId: string) => {
    try {
      await cancelAgent(agentId);
      refresh();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Cancel failed",
      );
    }
  };

  return (
    <Flex gap={2} h="100%">
      <Box
        flex="0 0 50%"
        bg="bg.muted"
        border="1px solid"
        borderColor="border.muted"
        borderRadius="md"
        p={2}
        overflow="auto"
      >
        <PanelHeader icon={<RiRobotLine size={14} />}>
          Agents
        </PanelHeader>
        {agents.length === 0 ? (
          <Empty>No agents</Empty>
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
                  bg={
                    selected?.agent_id === a.agent_id
                      ? "bg.emphasized"
                      : undefined
                  }
                  _hover={{ bg: "bg.emphasized" }}
                  onClick={() => setSelected(a)}
                >
                  <Td>{a.role}</Td>
                  <Td>{a.workspace}</Td>
                  <Td>
                    {a.model
                      .replace("claude-", "")
                      .split("-")[0]}
                  </Td>
                  <Td>
                    <StatusBadge status={a.state} />
                  </Td>
                  <Td textAlign="right">
                    {a.num_turns}
                  </Td>
                  <Td textAlign="right">
                    ${a.total_cost_usd.toFixed(2)}
                  </Td>
                  <Td>
                    {a.state === "running" && (
                      <IconButton
                        aria-label="Cancel"
                        size="2xs"
                        variant="outline"
                        colorPalette="red"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleCancel(a.agent_id);
                        }}
                      >
                        <RiStopCircleLine />
                      </IconButton>
                    )}
                  </Td>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        )}
      </Box>

      <Box
        flex={1}
        bg="bg.muted"
        border="1px solid"
        borderColor="border.muted"
        borderRadius="md"
        p={2}
        overflow="hidden"
        display="flex"
        flexDirection="column"
      >
        <PanelHeader
          icon={<RiTerminalBoxLine size={14} />}
        >
          Output
        </PanelHeader>
        {selected ? (
          <AgentOutput
            runId={selected.run_id}
            stepId={selected.step_id}
          />
        ) : (
          <Empty>
            Select an agent to view output
          </Empty>
        )}
      </Box>
    </Flex>
  );
}
