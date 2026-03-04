import { Box, Button, Flex, Table, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type Workspace,
  deleteWorkspace,
  getWorkspaceStatus,
  listWorkspaces,
} from "../api";

interface RepoStatus {
  repo: string;
  branch: string;
  status: string;
}

export default function Workspaces() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selected, setSelected] = useState<Workspace | null>(null);
  const [repoStatus, setRepoStatus] = useState<RepoStatus[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await listWorkspaces();
      setWorkspaces(data.workspaces);
    } catch { /* */ }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const selectWs = async (ws: Workspace) => {
    setSelected(ws);
    try {
      const data = await getWorkspaceStatus(ws.name);
      setRepoStatus(data.repos);
    } catch {
      setRepoStatus([]);
    }
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete workspace '${name}'?`)) return;
    await deleteWorkspace(name);
    setSelected(null);
    refresh();
  };

  return (
    <Flex gap={2} h="100%">
      <Box flex="0 0 50%" bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2} overflow="auto">
        <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
          Workspaces
        </Text>
        {workspaces.length === 0 ? (
          <Text textAlign="center" py={4} color="#737373" fontSize="11px">No workspaces</Text>
        ) : (
          <Table.Root size="sm" variant="line">
            <Table.Header>
              <Table.Row bg="transparent">
                <Th>Name</Th>
                <Th>Branch</Th>
                <Th textAlign="right">Repos</Th>
                <Th w="1px">{""}</Th>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {workspaces.map((ws) => (
                <Table.Row
                  key={ws.name}
                  cursor="pointer"
                  bg={selected?.name === ws.name ? "#2a2a2a" : undefined}
                  _hover={{ bg: "#2a2a2a" }}
                  onClick={() => selectWs(ws)}
                >
                  <Td>
                    {ws.name}
                    {ws.chroot && (
                      <Text as="span" fontSize="9px" color="#737373" ml={1}>[chroot]</Text>
                    )}
                  </Td>
                  <Td>{ws.branch}</Td>
                  <Td textAlign="right">{ws.repos.length}</Td>
                  <Td>
                    <Button
                      size="2xs"
                      variant="outline"
                      colorPalette="red"
                      onClick={(e) => { e.stopPropagation(); handleDelete(ws.name); }}
                    >
                      Del
                    </Button>
                  </Td>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        )}
      </Box>

      <Box flex={1} bg="#1c1c1c" border="1px solid #2e2e2e" borderRadius="4px" p={2} overflow="auto">
        <Text fontSize="10px" fontWeight={600} color="#737373" textTransform="uppercase" letterSpacing="0.5px" mb={1.5}>
          {selected ? `${selected.name} — Repos` : "Repo Status"}
        </Text>
        {!selected ? (
          <Text textAlign="center" py={4} color="#737373" fontSize="11px">Select a workspace</Text>
        ) : repoStatus.length === 0 ? (
          <Text textAlign="center" py={4} color="#737373" fontSize="11px">No repos</Text>
        ) : (
          <Table.Root size="sm" variant="line">
            <Table.Header>
              <Table.Row bg="transparent">
                <Th>Repo</Th>
                <Th>Branch</Th>
                <Th>Status</Th>
              </Table.Row>
            </Table.Header>
            <Table.Body>
              {repoStatus.map((r) => (
                <Table.Row key={r.repo} _hover={{ bg: "#2a2a2a" }}>
                  <Td>{r.repo}</Td>
                  <Td>{r.branch}</Td>
                  <Td>{r.status}</Td>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
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
