import {
  Box, Flex, IconButton, Table, Text,
} from "@chakra-ui/react";
import { useCallback, useState } from "react";
import {
  RiLockUnlockLine,
  RiPlayLine,
  RiServerLine,
  RiShutDownLine,
} from "@remixicon/react";
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
  "shut off": "fg.muted",
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
        e instanceof Error
          ? e.message
          : "Release failed",
      );
    }
  };

  return (
    <Box
      bg="bg.muted"
      border="1px solid"
      borderColor="border.muted"
      borderRadius="md"
      p={2}
    >
      <PanelHeader icon={<RiServerLine size={14} />}>
        Targets
      </PanelHeader>
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
              <Th w="1px">{""}</Th>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {targets.map((t) => (
              <Table.Row
                key={t.name}
                _hover={{ bg: "bg.emphasized" }}
              >
                <Td>
                  {t.name}
                  {t.template && (
                    <Text
                      as="span"
                      fontSize="11px"
                      color="fg.muted"
                      ml={1}
                    >
                      [tpl]
                    </Text>
                  )}
                </Td>
                <Td>{t.type}</Td>
                <Td fontFamily="monospace">
                  {t.host}
                </Td>
                <Td>
                  {t.vm_state ? (
                    <Text
                      as="span"
                      color={
                        VM_STATE_COLOR[t.vm_state]
                          ?? "fg.muted"
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
                  {t.lock
                    ? t.lock.workspace
                    : "—"}
                </Td>
                <Td>
                  {!t.template && (
                    <Flex gap={1}>
                      {t.type === "vm" && (
                        <>
                          <IconButton
                            aria-label="Start VM"
                            size="2xs"
                            variant="outline"
                            onClick={() =>
                              handleUp(t.name)
                            }
                          >
                            <RiPlayLine />
                          </IconButton>
                          <IconButton
                            aria-label="Stop VM"
                            size="2xs"
                            variant="outline"
                            onClick={() =>
                              handleDown(t.name)
                            }
                          >
                            <RiShutDownLine />
                          </IconButton>
                        </>
                      )}
                      {t.lock && (
                        <IconButton
                          aria-label="Release"
                          size="2xs"
                          variant="outline"
                          colorPalette="red"
                          onClick={() =>
                            handleRelease(t.name)
                          }
                        >
                          <RiLockUnlockLine />
                        </IconButton>
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
