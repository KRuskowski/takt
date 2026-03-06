import { Box, Flex, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import {
  RiFileEditLine,
  RiFileTextLine,
} from "@remixicon/react";
import { listTemplates } from "../api";
import { showError } from "../toast";
import { PanelHeader } from "./shared";
import MarkdownEditor from "./MarkdownEditor";

const LABELS: Record<string, string> = {
  "workspace_claude.md": "Workspace CLAUDE.md",
  "root_repo_claude.md": "Root Repo CLAUDE.md",
  "pipeline_roles.md": "Pipeline Roles",
};

function label(name: string): string {
  return LABELS[name]
    ?? name.replace(/\.md$/, "");
}

export default function Settings() {
  const [templates, setTemplates] =
    useState<string[]>([]);
  const [selected, setSelected] =
    useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listTemplates();
      setTemplates(data.templates);
      if (
        data.templates.length > 0
        && !selected
      ) {
        setSelected(data.templates[0]);
      }
    } catch (e) {
      showError(
        e instanceof Error
          ? e.message
          : "Failed to list templates",
      );
    }
  }, [selected]);

  useEffect(() => { refresh(); }, [refresh]);

  return (
    <Flex gap={2} h="100%">
      <Box
        flex="0 0 200px"
        bg="bg.muted"
        border="1px solid"
        borderColor="border.muted"
        borderRadius="md"
        p={2}
        overflow="auto"
      >
        <PanelHeader
          icon={<RiFileEditLine size={14} />}
        >
          Templates
        </PanelHeader>
        {templates.map((t) => (
          <Flex
            key={t}
            align="center"
            gap={1.5}
            px={2}
            py={1}
            borderRadius="sm"
            cursor="pointer"
            fontSize="13px"
            color={
              selected === t
                ? "fg"
                : "fg.muted"
            }
            bg={
              selected === t
                ? "bg.emphasized"
                : undefined
            }
            _hover={{ bg: "bg.emphasized" }}
            onClick={() => setSelected(t)}
          >
            <RiFileTextLine size={14} />
            <Text truncate>{label(t)}</Text>
          </Flex>
        ))}
      </Box>

      <Box
        flex={1}
        bg="bg.muted"
        border="1px solid"
        borderColor="border.muted"
        borderRadius="md"
        overflow="hidden"
      >
        {selected ? (
          <MarkdownEditor
            key={selected}
            file={selected}
            label={label(selected)}
          />
        ) : (
          <Flex
            h="100%"
            align="center"
            justify="center"
          >
            <Text
              fontSize="13px"
              color="fg.muted"
            >
              Select a template
            </Text>
          </Flex>
        )}
      </Box>
    </Flex>
  );
}
