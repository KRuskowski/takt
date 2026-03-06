import {
  Box, Button, Checkbox, Flex,
  IconButton, Input, Table, Text,
} from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  RiAddLine,
  RiDeleteBinLine,
  RiFileTextLine,
  RiFolderLine,
  RiGitMergeLine,
  RiGitRepositoryLine,
  RiSettings3Line,
} from "@remixicon/react";
import {
  type PipelineStep,
  type RepoInfo,
  type Workspace,
  createWorkspace,
  deleteWorkspace,
  getPipeline,
  getWorkspaceClaudeMd,
  getWorkspaceStatus,
  listRepos,
  listWorkspaces,
  putWorkspaceClaudeMd,
  setPipeline,
} from "../api";
import { useSSERefresh } from "../hooks/useSSERefresh";
import { showError, showSuccess } from "../toast";
import { Empty, PanelHeader, Td, Th } from "./shared";
import MarkdownEditor from "./MarkdownEditor";

interface RepoStatus {
  repo: string;
  branch: string;
  status: string;
}

type DetailView =
  | { kind: "repos" }
  | { kind: "claude" }
  | { kind: "stage"; step: PipelineStep }
  | { kind: "settings" };

export default function Workspaces() {
  const [workspaces, setWorkspaces] =
    useState<Workspace[]>([]);
  const [selected, setSelected] =
    useState<Workspace | null>(null);
  const [repoStatus, setRepoStatus] =
    useState<RepoStatus[]>([]);
  const [pipelineSteps, setPipelineSteps] =
    useState<PipelineStep[]>([]);
  const [repos, setRepos] =
    useState<RepoInfo[]>([]);
  const [showForm, setShowForm] =
    useState(false);
  const [newName, setNewName] = useState("");
  const [selectedRepos, setSelectedRepos] =
    useState<string[]>([]);
  const [creating, setCreating] =
    useState(false);
  const [detail, setDetail] =
    useState<DetailView>({ kind: "repos" });

  const refresh = useCallback(async () => {
    try {
      const data = await listWorkspaces();
      setWorkspaces(data.workspaces);
    } catch (e) {
      showError(
        e instanceof Error
          ? e.message
          : "Refresh failed",
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
    setDetail({ kind: "repos" });
    try {
      const [statusData, plData] =
        await Promise.all([
          getWorkspaceStatus(ws.name),
          getPipeline(ws.name),
        ]);
      setRepoStatus(statusData.repos);
      setPipelineSteps(plData.steps);
    } catch {
      setRepoStatus([]);
      setPipelineSteps([]);
    }
  };

  const handleDelete = async (name: string) => {
    if (
      !confirm(`Delete workspace '${name}'?`)
    ) return;
    try {
      await deleteWorkspace(name);
      setSelected(null);
      refresh();
      showSuccess(`Deleted ${name}`);
    } catch (e) {
      showError(
        e instanceof Error
          ? e.message
          : "Delete failed",
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
      showSuccess(
        `Creating ${newName.trim()}...`,
      );
      setShowForm(false);
      setNewName("");
      setSelectedRepos([]);
      refresh();
    } catch (e) {
      showError(
        e instanceof Error
          ? e.message
          : "Create failed",
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
      {/* Left: workspace list */}
      <Box
        flex="0 0 260px"
        bg="bg.muted"
        border="1px solid"
        borderColor="border.muted"
        borderRadius="md"
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
          <PanelHeader
            icon={
              <RiFolderLine size={14} />
            }
          >
            Workspaces
          </PanelHeader>
          <IconButton
            aria-label={
              showForm ? "Cancel" : "New"
            }
            size="2xs"
            variant="outline"
            onClick={() =>
              setShowForm(!showForm)
            }
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
            <Flex
              gap={1}
              flexWrap="wrap"
              mb={1.5}
            >
              {repos.map((r) => (
                <Checkbox.Root
                  key={r.name}
                  size="sm"
                  checked={selectedRepos.includes(
                    r.name,
                  )}
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
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Root>
        )}
      </Box>

      {/* Right: detail panel */}
      <Flex
        flex={1}
        direction="column"
        gap={2}
        overflow="hidden"
      >
        {!selected ? (
          <Box
            flex={1}
            bg="bg.muted"
            border="1px solid"
            borderColor="border.muted"
            borderRadius="md"
            p={2}
          >
            <Empty>Select a workspace</Empty>
          </Box>
        ) : (
          <>
            {/* Sub-nav tabs */}
            <Flex
              gap={0.5}
              flexShrink={0}
            >
              <NavTab
                active={detail.kind === "repos"}
                icon={
                  <RiGitRepositoryLine
                    size={13}
                  />
                }
                label="Repos"
                onClick={() =>
                  setDetail({ kind: "repos" })
                }
              />
              <NavTab
                active={
                  detail.kind === "claude"
                }
                icon={
                  <RiFileTextLine size={13} />
                }
                label="CLAUDE.md"
                onClick={() =>
                  setDetail({ kind: "claude" })
                }
              />
              {pipelineSteps.map((step) => (
                <NavTab
                  key={step.seq}
                  active={
                    detail.kind === "stage"
                    && detail.step.seq
                      === step.seq
                  }
                  icon={
                    <RiGitMergeLine
                      size={13}
                    />
                  }
                  label={step.name}
                  onClick={() =>
                    setDetail({
                      kind: "stage",
                      step,
                    })
                  }
                />
              ))}
              <NavTab
                active={
                  detail.kind === "settings"
                }
                icon={
                  <RiSettings3Line size={13} />
                }
                label="Settings"
                onClick={() =>
                  setDetail({
                    kind: "settings",
                  })
                }
              />
            </Flex>

            {/* Detail content */}
            <Box
              flex={1}
              bg="bg.muted"
              border="1px solid"
              borderColor="border.muted"
              borderRadius="md"
              overflow="hidden"
            >
              {detail.kind === "repos" && (
                <Box p={2} overflow="auto">
                  <PanelHeader
                    icon={
                      <RiGitRepositoryLine
                        size={14}
                      />
                    }
                  >
                    {selected.name} — Repos
                  </PanelHeader>
                  {repoStatus.length === 0 ? (
                    <Empty>No repos</Empty>
                  ) : (
                    <Table.Root
                      size="sm"
                      variant="line"
                    >
                      <Table.Header>
                        <Table.Row
                          bg="transparent"
                        >
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
                              bg:
                                "bg.emphasized",
                            }}
                          >
                            <Td>{r.repo}</Td>
                            <Td>
                              {r.branch}
                            </Td>
                            <Td>
                              {r.status}
                            </Td>
                          </Table.Row>
                        ))}
                      </Table.Body>
                    </Table.Root>
                  )}
                </Box>
              )}
              {detail.kind === "claude" && (
                <MarkdownEditor
                  key={`ws:${selected.name}`}
                  file="CLAUDE.md"
                  label={
                    `${selected.name}`
                    + " / CLAUDE.md"
                  }
                  load={() =>
                    getWorkspaceClaudeMd(
                      selected.name,
                    )
                  }
                  save={(c) =>
                    putWorkspaceClaudeMd(
                      selected.name, c,
                    )
                  }
                />
              )}
              {detail.kind === "settings" && (
                <Box p={2}>
                  <PanelHeader
                    icon={
                      <RiSettings3Line
                        size={14}
                      />
                    }
                  >
                    {selected.name} — Settings
                  </PanelHeader>
                  <Box mt={4}>
                    <Button
                      size="sm"
                      variant="outline"
                      colorPalette="red"
                      onClick={() =>
                        handleDelete(
                          selected.name,
                        )
                      }
                    >
                      <RiDeleteBinLine
                        size={14}
                      />
                      Delete Workspace
                    </Button>
                  </Box>
                </Box>
              )}
              {detail.kind === "stage" && (
                <MarkdownEditor
                  key={
                    `stage:${selected.name}`
                    + `:${detail.step.seq}`
                  }
                  file={detail.step.name}
                  label={
                    `${selected.name}`
                    + ` / ${detail.step.name}`
                    + " prompt"
                  }
                  load={async () => {
                    const cfg = JSON.parse(
                      detail.step.config_json,
                    );
                    return {
                      content:
                        cfg.prompt ?? "",
                    };
                  }}
                  save={async (content) => {
                    const cfg = JSON.parse(
                      detail.step.config_json,
                    );
                    cfg.prompt = content;
                    const updated =
                      pipelineSteps.map((s) =>
                        s.seq
                          === detail.step.seq
                          ? {
                            name: s.name,
                            step_type:
                              s.step_type,
                            config: cfg,
                            timeout_secs:
                              s.timeout_secs,
                          }
                          : {
                            name: s.name,
                            step_type:
                              s.step_type,
                            config: JSON.parse(
                              s.config_json,
                            ),
                            timeout_secs:
                              s.timeout_secs,
                          },
                      );
                    await setPipeline(
                      selected.name,
                      updated,
                    );
                    detail.step.config_json =
                      JSON.stringify(cfg);
                  }}
                />
              )}
            </Box>
          </>
        )}
      </Flex>
    </Flex>
  );
}

function NavTab({
  active, icon, label, onClick,
}: {
  active: boolean;
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <Flex
      align="center"
      gap={1}
      px={2}
      py={1}
      borderRadius="md"
      cursor="pointer"
      fontSize="12px"
      fontWeight={active ? 600 : 400}
      color={active ? "fg" : "fg.muted"}
      bg={
        active ? "bg.muted" : undefined
      }
      border="1px solid"
      borderColor={
        active
          ? "border.muted"
          : "transparent"
      }
      _hover={{ bg: "bg.muted" }}
      onClick={onClick}
      flexShrink={0}
    >
      {icon}
      {label}
    </Flex>
  );
}
