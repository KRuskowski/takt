import { Box, Text } from "@chakra-ui/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { type OutputLine, getStepOutput } from "../api";
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
  runId: number;
  stepId: number;
}

export default function AgentOutput({ runId, stepId }: Props) {
  const [lines, setLines] = useState<OutputLine[]>([]);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await getStepOutput(runId, stepId);
        if (!cancelled) setLines(data.lines);
      } catch { /* */ }
    })();
    return () => { cancelled = true; };
  }, [runId, stepId]);

  const handleSSE = useCallback(
    (event: { topic: string; data: unknown }) => {
      const line = event.data as OutputLine;
      setLines((prev) => [...prev, line]);
    },
    [],
  );

  useSSE([`agent.output.step-${stepId}`], handleSSE);

  useEffect(() => {
    const el = containerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  return (
    <Box
      ref={containerRef}
      flex={1}
      fontFamily="'FiraCode Nerd Font', 'Fira Code', monospace"
      fontSize="11px"
      lineHeight="1.4"
      bg="#0a0a0a"
      borderRadius="3px"
      p={1.5}
      overflow="auto"
    >
      {lines.length === 0 ? (
        <Text textAlign="center" py={4} color="#737373" fontSize="11px">
          No output yet
        </Text>
      ) : (
        lines.map((line) => (
          <Box
            key={line.line_no}
            whiteSpace="pre-wrap"
            wordBreak="break-all"
            color={KIND_COLORS[line.kind] ?? "#d4d4d4"}
            fontStyle={line.kind === "thinking" ? "italic" : undefined}
          >
            {line.content}
          </Box>
        ))
      )}
    </Box>
  );
}
