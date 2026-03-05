import { Box, Flex, Input, Text } from "@chakra-ui/react";
import {
  useEffect, useRef, useState,
  type KeyboardEvent,
} from "react";

interface Props {
  onCommand: (cmd: string) => void;
}

export default function CommandBar({ onCommand }: Props) {
  const [value, setValue] = useState("");
  const [history, setHistory] = useState<string[]>([]);
  const [histIdx, setHistIdx] = useState(-1);
  const [feedback, setFeedback] = useState<{
    msg: string;
    error: boolean;
  } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-clear feedback after 3 seconds.
  useEffect(() => {
    if (!feedback) return;
    const id = setTimeout(() => setFeedback(null), 3000);
    return () => clearTimeout(id);
  }, [feedback]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onCommand(trimmed);
    setHistory((prev) => [trimmed, ...prev]);
    setHistIdx(-1);
    setValue("");
  };

  const handleKey = (
    e: KeyboardEvent<HTMLInputElement>,
  ) => {
    if (e.key === "Enter") {
      submit();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      const next = Math.min(
        histIdx + 1, history.length - 1,
      );
      setHistIdx(next);
      if (history[next] !== undefined) {
        setValue(history[next]);
      }
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      const next = histIdx - 1;
      if (next < 0) {
        setHistIdx(-1);
        setValue("");
      } else {
        setHistIdx(next);
        setValue(history[next]);
      }
    }
  };

  return (
    <Flex
      align="center"
      h="24px"
      bg="#0f0f0f"
      borderTop="1px solid #2e2e2e"
      flexShrink={0}
      px={1}
      gap={0}
      onClick={() => inputRef.current?.focus()}
      cursor="text"
    >
      <Text
        color="#737373"
        fontSize="11px"
        fontFamily={
          "'FiraCode Nerd Font', 'Fira Code', monospace"
        }
        flexShrink={0}
        userSelect="none"
      >
        :
      </Text>
      <Box flex={1}>
        <Input
          ref={inputRef}
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            setHistIdx(-1);
          }}
          onKeyDown={handleKey}
          variant="flushed"
          fontSize="11px"
          fontFamily={
            "'FiraMono Nerd Font', 'Fira Code', monospace"
          }
          color="#d4d4d4"
          h="24px"
          pl={1}
          pr={1}
          _placeholder={{ color: "#3a3a3a" }}
          placeholder="command"
          spellCheck={false}
          autoComplete="off"
        />
      </Box>
      {feedback && (
        <Text
          fontSize="10px"
          color={feedback.error ? "#dc2626" : "#22c55e"}
          flexShrink={0}
          px={2}
        >
          {feedback.msg}
        </Text>
      )}
    </Flex>
  );
}

/**
 * Show brief feedback in the command bar.
 * Exposed so App can call it after dispatch.
 */
export type SetFeedback = (
  msg: string, error: boolean,
) => void;
