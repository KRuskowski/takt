import {
  Box, Button, Checkbox, Flex, IconButton,
  Input, Table, Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  RiAddLine,
  RiDeleteBinLine,
  RiFolderLine,
  RiGitRepositoryLine,
} from "@remixicon/react";
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
        bg="bg.muted"
        border="1px solid"
        borderColor="border.muted"
        borderRadius="md"
        p={2}
        overflow="auto"
      >
        <Flex
          justify="space-between"
          align="center"
          mb={1.5}
        >
          <PanelHeader
            icon={<RiFolderLine size={14} />}
          >
            Workspaces
          </PanelHeader>
          <IconButton
            aria-label={showForm ? "Cancel" : "New"}
            size="2xs"
            variant="outline"
            onClick={() => setShowForm(!showForm)}
          >
            <RiAddLine />
          </IconButton>
        </Flex>

        {showForm && (
          <Box
            mb={2}
            p={2}
            bg="bg.subtle"
            borderRadius="md"
            border="1px solid"
            borderColor="border.muted"
          >
            <Input
              size="xs"
              fontSize="13px"
              bg="bg.muted"
              border="1px solid"
              borderColor="border.muted"
              color="fg"
              placeholder="Workspace name"
              mb={1.5}
              value={newName}
              onChange={(e) =>
                setNewName(e.target.value)
              }
              spellCheck={false}
            />
            <Text
              fontSize="12px"
              color="fg.muted"
              mb={1}
            >
              Repos:
            </Text>
            <Flex gap={1} flexWrap="wrap" mb={1.5}>
              {repos.map((r) => (
                <Checkbox.Root
                  key={r.name}
                  size="sm"
                  checked={
                    selectedRepos.includes(r.name)
                  }
                  onCheckedChange={() =>
                    toggleRepo(r.name)
                  }
                >
                  <Checkbox.HiddenInput />
                  <Checkbox.Control>
                    <Checkbox.Indicator />
                  </Checkbox.Control>
                  <Checkbox.Label
                    fontSize="12px"
                    color="fg"
                  >
                    {r.name}
                  </Checkbox.Label>
                </Checkbox.Root>
              ))}
            </Flex>
            <Button
              size="2xs"
              variant="solid"
              colorPalette="green"
              onClick={handleCreate}
              loading={creating}
            >
              <RiAddLine size={14} />
              Create
            </Button>
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
                      ? "bg.emphasized"
                      : undefined
                  }
                  _hover={{
                    bg: "bg.emphasized",
                  }}
                  onClick={() => selectWs(ws)}
                >
                  <Td>{ws.name}</Td>
                  <Td>{ws.branch}</Td>
                  <Td textAlign="right">
                    {ws.repos.length}
                  </Td>
                  <Td>
                    <IconButton
                      aria-label="Delete"
                      size="2xs"
                      variant="outline"
                      colorPalette="red"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDelete(ws.name);
                      }}
                    >
                      <RiDeleteBinLine />
                    </IconButton>
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
        overflow="auto"
      >
        <PanelHeader
          icon={<RiGitRepositoryLine size={14} />}
        >
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
                  _hover={{
                    bg: "bg.emphasized",
                  }}
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
