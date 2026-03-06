import { Box, Text } from "@chakra-ui/react";
import {
  useCallback, useEffect, useRef, useState,
} from "react";
import {
  type OutputLine, getStepOutput,
} from "../api";
import { useSSE } from "../hooks/useSSE";

const KIND_COLORS: Record<string, string> = {
  tool_use: "#7ec8e3",
  tool_result: "#737373",
  thinking: "#525252",
  error: "#dc2626",
  result: "#22c55e",
  text: "#d4d4d4",
};

interface Props {
  /** Fetch initial lines. Defaults to step output. */
  fetchLines?: () => Promise<{ lines: OutputLine[] }>;
  /** SSE topic for live updates. */
  sseTopic?: string;
  /** Legacy: run ID for step output. */
  runId?: number;
  /** Legacy: step ID for step output. */
  stepId?: number;
}

export default function AgentOutput({
  fetchLines, sseTopic, runId, stepId,
}: Props) {
  const [lines, setLines] = useState<OutputLine[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  // Resolve the fetch function.
  const fetcher = fetchLines ?? (
    runId != null && stepId != null
      ? () => getStepOutput(runId, stepId)
      : null
  );

  // Resolve the SSE topic.
  const topic = sseTopic ?? (
    stepId != null
      ? `agent.output.step-${stepId}`
      : undefined
  );

  useEffect(() => {
    if (!fetcher) return;
    let cancelled = false;
    (async () => {
      try {
        const data = await fetcher();
        if (!cancelled) setLines(data.lines);
      } catch { /* service unavailable */ }
    })();
    return () => { cancelled = true; };
  }, [runId, stepId, fetchLines]);

  const handleSSE = useCallback(
    (event: { topic: string; data: unknown }) => {
      const line = event.data as OutputLine;
      setLines((prev) => [...prev, line]);
    },
    [],
  );

  const topics = topic ? [topic] : [];
  useSSE(topics, handleSSE);

  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <Box
      ref={containerRef}
      flex={1}
      fontFamily={
        "'FiraCode Nerd Font', 'Fira Code', monospace"
      }
      fontSize="13px"
      lineHeight="1.4"
      bg="#0a0a0a"
      borderRadius="3px"
      p={1.5}
      overflow="auto"
    >
      {lines.length === 0 ? (
        <Text
          textAlign="center"
          py={4}
          color="fg.muted"
          fontSize="13px"
        >
          No output yet
        </Text>
      ) : (
        lines.map((line) => (
          <Box
            key={line.line_no}
            whiteSpace="pre-wrap"
            wordBreak="break-all"
            color={KIND_COLORS[line.kind] ?? "#d4d4d4"}
            fontStyle={
              line.kind === "thinking"
                ? "italic"
                : undefined
            }
          >
            {line.content}
          </Box>
        ))
      )}
    </Box>
  );
}
