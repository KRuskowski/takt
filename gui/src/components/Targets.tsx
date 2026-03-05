import {
  Box, Button, Flex, Table, Text,
} from "@chakra-ui/react";
import { useCallback, useState } from "react";
import {
  type Target,
  listTargets,
  releaseTarget,
  startTarget,
  stopTarget,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError, showSuccess } from "../toast";
import { Empty, PanelHeader, Td, Th } from "./shared";

const VM_STATE_COLOR: Record<string, string> = {
  running: "#22c55e",
  "shut off": "#737373",
  paused: "#eab308",
};

export default function Targets() {
  const [targets, setTargets] = useState<Target[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await listTargets();
      setTargets(data.targets);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Refresh failed",
      );
    }
  }, []);

  useSSERefresh(["workspace.event"], refresh);

  const handleUp = async (name: string) => {
    try {
      await startTarget(name);
      showSuccess(`${name} starting`);
      refresh();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Start failed",
      );
    }
  };

  const handleDown = async (name: string) => {
    try {
      await stopTarget(name);
      showSuccess(`${name} stopping`);
      refresh();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Stop failed",
      );
    }
  };

  const handleRelease = async (name: string) => {
    try {
      await releaseTarget(name);
      showSuccess(`${name} released`);
      refresh();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Release failed",
      );
    }
  };

  return (
    <Box
      bg="#1c1c1c"
      border="1px solid #2e2e2e"
      borderRadius="4px"
      p={2}
    >
      <PanelHeader>Targets</PanelHeader>
      {targets.length === 0 ? (
        <Empty>No targets configured</Empty>
      ) : (
        <Table.Root size="sm" variant="line">
          <Table.Header>
            <Table.Row bg="transparent">
              <Th>Name</Th>
              <Th>Type</Th>
              <Th>Host</Th>
              <Th>State</Th>
              <Th>Description</Th>
              <Th>Claimed By</Th>
              <Th w="1px">Actions</Th>
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
                <Td fontFamily="monospace">{t.host}</Td>
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
                <Td>{t.description}</Td>
                <Td>
                  {t.lock ? t.lock.workspace : "—"}
                </Td>
                <Td>
                  {!t.template && (
                    <Flex gap={1}>
                      {t.type === "vm" && (
                        <>
                          <Button
                            size="2xs"
                            variant="outline"
                            onClick={() =>
                              handleUp(t.name)
                            }
                          >
                            Up
                          </Button>
                          <Button
                            size="2xs"
                            variant="outline"
                            onClick={() =>
                              handleDown(t.name)
                            }
                          >
                            Down
                          </Button>
                        </>
                      )}
                      {t.lock && (
                        <Button
                          size="2xs"
                          variant="outline"
                          colorPalette="red"
                          onClick={() =>
                            handleRelease(t.name)
                          }
                        >
                          Release
                        </Button>
                      )}
                    </Flex>
                  )}
                </Td>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Root>
      )}
    </Box>
  );
}
