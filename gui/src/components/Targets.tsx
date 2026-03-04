import { Box, Button, Flex, Table, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type Target,
  listTargets,
  releaseTarget,
  startTarget,
  stopTarget,
} from "../api";

export default function Targets() {
  const [targets, setTargets] = useState<Target[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await listTargets();
      setTargets(data.targets);
    } catch { /* */ }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <Box bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2}>
      <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
        Targets
      </Text>
      {targets.length === 0 ? (
        <Text textAlign="center" py={4} color="#737373" fontSize="11px">No targets configured</Text>
      ) : (
        <Table.Root size="sm" variant="line">
          <Table.Header>
            <Table.Row bg="transparent">
              <Th>Name</Th>
              <Th>Type</Th>
              <Th>Host</Th>
              <Th>Description</Th>
              <Th>Claimed By</Th>
              <Th w="1px">Actions</Th>
            </Table.Row>
          </Table.Header>
          <Table.Body>
            {targets.map((t) => (
              <Table.Row key={t.name} _hover={{ bg: "#2a2a2a" }}>
                <Td>
                  {t.name}
                  {t.template && (
                    <Text as="span" fontSize="9px" color="#737373" ml={1}>[tpl]</Text>
                  )}
                </Td>
                <Td>{t.type}</Td>
                <Td fontFamily="monospace">{t.host}</Td>
                <Td>{t.description}</Td>
                <Td>{t.lock ? t.lock.workspace : "—"}</Td>
                <Td>
                  {!t.template && (
                    <Flex gap={1}>
                      {t.type === "vm" && (
                        <>
                          <Button size="2xs" variant="outline" onClick={() => { startTarget(t.name).then(refresh); }}>
                            Up
                          </Button>
                          <Button size="2xs" variant="outline" onClick={() => { stopTarget(t.name).then(refresh); }}>
                            Down
                          </Button>
                        </>
                      )}
                      {t.lock && (
                        <Button
                          size="2xs"
                          variant="outline"
                          colorPalette="red"
                          onClick={() => { releaseTarget(t.name).then(refresh); }}
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

function Th({ children, ...props }: { children: React.ReactNode; textAlign?: string; w?: string }) {
  return <Table.ColumnHeader fontSize="11px" color="#737373" fontWeight={500} py={1} px={1.5} borderColor="#2e2e2e" {...props}>{children}</Table.ColumnHeader>;
}

function Td({ children, ...props }: { children: React.ReactNode; textAlign?: string; fontFamily?: string }) {
  return <Table.Cell fontSize="11px" py={1} px={1.5} borderColor="#2e2e2e" {...props}>{children}</Table.Cell>;
}
