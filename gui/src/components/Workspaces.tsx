import {
  Box, Button, Checkbox, Flex, Input,
  Table, Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  type RepoInfo,
  type Workspace,
  createWorkspace,
  deleteWorkspace,
  getWorkspaceStatus,
  listRepos,
  listWorkspaces,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError, showSuccess } from "../toast";
import { Empty, PanelHeader, Td, Th } from "./shared";

interface RepoStatus {
  repo: string;
  branch: string;
  status: string;
}

export default function Workspaces() {
  const [workspaces, setWorkspaces] =
    useState<Workspace[]>([]);
  const [selected, setSelected] =
    useState<Workspace | null>(null);
  const [repoStatus, setRepoStatus] =
    useState<RepoStatus[]>([]);
  const [repos, setRepos] = useState<RepoInfo[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState("");
  const [selectedRepos, setSelectedRepos] =
    useState<string[]>([]);
  const [creating, setCreating] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await listWorkspaces();
      setWorkspaces(data.workspaces);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Refresh failed",
      );
    }
  }, []);

  useSSERefresh(["workspace.event"], refresh);

  // Load repos for the create form.
  useEffect(() => {
    if (!showForm) return;
    listRepos()
      .then((data) => setRepos(data.repos))
      .catch(() => {});
  }, [showForm]);

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
    try {
      await deleteWorkspace(name);
      setSelected(null);
      refresh();
      showSuccess(`Deleted ${name}`);
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Delete failed",
      );
    }
  };

  const handleCreate = async () => {
    if (!newName.trim()) {
      showError("Name is required");
      return;
    }
    if (selectedRepos.length === 0) {
      showError("Select at least one repo");
      return;
    }
    setCreating(true);
    try {
      await createWorkspace(
        newName.trim(), selectedRepos,
      );
      showSuccess(`Creating ${newName.trim()}...`);
      setShowForm(false);
      setNewName("");
      setSelectedRepos([]);
      refresh();
    } catch (e) {
      showError(
        e instanceof Error ? e.message : "Create failed",
      );
    } finally {
      setCreating(false);
    }
  };

  const toggleRepo = (name: string) => {
    setSelectedRepos((prev) =>
      prev.includes(name)
        ? prev.filter((r) => r !== name)
        : [...prev, name],
    );
  };

  return (
    <Flex gap={2} h="100%">
      <Box
        flex="0 0 50%"
        bg="#1c1c1c"
        border="1px solid #2e2e2e"
        borderRadius="4px"
        p={2}
        overflow="auto"
      >
        <Flex
          justify="space-between"
          align="center"
          mb={1.5}
        >
          <PanelHeader>Workspaces</PanelHeader>
          <Button
            size="2xs"
            variant="outline"
            onClick={() => setShowForm(!showForm)}
          >
            {showForm ? "Cancel" : "New"}
          </Button>
        </Flex>

        {/* Create form */}
        {showForm && (
          <Box
            mb={2}
            p={2}
            bg="#242424"
            borderRadius="4px"
            border="1px solid #2e2e2e"
          >
            <Input
              size="xs"
              fontSize="11px"
              bg="#1c1c1c"
              border="1px solid #2e2e2e"
              color="#d4d4d4"
              placeholder="Workspace name"
              mb={1.5}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              spellCheck={false}
            />
            <Text
              fontSize="10px"
              color="#737373"
              mb={1}
            >
              Repos:
            </Text>
            <Flex gap={1} flexWrap="wrap" mb={1.5}>
              {repos.map((r) => (
                <Checkbox.Root
                  key={r.name}
                  size="sm"
                  checked={selectedRepos.includes(r.name)}
                  onCheckedChange={() =>
                    toggleRepo(r.name)
                  }
                >
                  <Checkbox.HiddenInput />
                  <Checkbox.Control>
                    <Checkbox.Indicator />
                  </Checkbox.Control>
                  <Checkbox.Label
                    fontSize="10px"
                    color="#d4d4d4"
                  >
                    {r.name}
                  </Checkbox.Label>
                </Checkbox.Root>
              ))}
            </Flex>
            <Flex align="center" gap={2}>
              <Button
                size="2xs"
                variant="solid"
                colorPalette="green"
                onClick={handleCreate}
                loading={creating}
              >
                Create
              </Button>
            </Flex>
          </Box>
        )}

        {workspaces.length === 0 ? (
          <Empty>No workspaces</Empty>
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
                  bg={
                    selected?.name === ws.name
                      ? "#2a2a2a"
                      : undefined
                  }
                  _hover={{ bg: "#2a2a2a" }}
                  onClick={() => selectWs(ws)}
                >
                  <Td>{ws.name}</Td>
                  <Td>{ws.branch}</Td>
                  <Td textAlign="right">
                    {ws.repos.length}
                  </Td>
                  <Td>
                    <Button
                      size="2xs"
                      variant="outline"
                      colorPalette="red"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(ws.name);
                      }}
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

      <Box
        flex={1}
        bg="#1c1c1c"
        border="1px solid #2e2e2e"
        borderRadius="4px"
        p={2}
        overflow="auto"
      >
        <PanelHeader>
          {selected
            ? `${selected.name} — Repos`
            : "Repo Status"}
        </PanelHeader>
        {!selected ? (
          <Empty>Select a workspace</Empty>
        ) : repoStatus.length === 0 ? (
          <Empty>No repos</Empty>
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
                <Table.Row
                  key={r.repo}
                  _hover={{ bg: "#2a2a2a" }}
                >
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
