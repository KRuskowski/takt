import { Box, Flex, Input, Text } from "@chakra-ui/react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { RiTerminalLine } from "@remixicon/react";

interface Props {
  onCommand: (cmd: string) => void;
}

export interface CommandFeedback {
  msg: string;
  error: boolean;
  multi?: boolean;
}

export interface CommandBarHandle {
  setOutput: (fb: CommandFeedback) => void;
}

const CommandBar = forwardRef<
  CommandBarHandle, Props
>(({ onCommand }, ref) => {
  const [value, setValue] = useState("");
  const [history, setHistory] =
    useState<string[]>([]);
  const [histIdx, setHistIdx] = useState(-1);
  const [output, setOutput] =
    useState<CommandFeedback | null>(null);
  const inputRef =
    useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({
    setOutput,
  }));

  // Auto-clear single-line feedback.
  useEffect(() => {
    if (!output || output.multi) return;
    const id = setTimeout(
      () => setOutput(null), 3000,
    );
    return () => clearTimeout(id);
  }, [output]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setOutput(null);
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
    } else if (e.key === "Escape") {
      setOutput(null);
      setValue("");
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
    <Box flexShrink={0}>
      {output?.multi && (
        <Box
          bg="bg"
          borderTop="1px solid"
          borderTopColor="border.muted"
          px={2}
          py={1.5}
          fontSize="12px"
          color="fg.muted"
          whiteSpace="pre"
          lineHeight="1.5"
        >
          {output.msg}
        </Box>
      )}

      <Flex
        align="center"
        h="32px"
        bg="bg"
        borderTop="1px solid"
        borderTopColor="border.muted"
        px={1.5}
        gap={1.5}
        onClick={() =>
          inputRef.current?.focus()
        }
        cursor="text"
      >
        <Box color="fg.muted" flexShrink={0}>
          <RiTerminalLine size={14} />
        </Box>
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
            fontSize="13px"
            color="fg"
            h="32px"
            pl={0}
            pr={1}
            _placeholder={{
              color: "fg.subtle",
            }}
            placeholder="type help for commands"
            spellCheck={false}
            autoComplete="off"
          />
        </Box>
        {output && !output.multi && (
          <Text
            fontSize="12px"
            color={
              output.error
                ? "#dc2626"
                : "#22c55e"
            }
            flexShrink={0}
            px={1}
          >
            {output.msg}
          </Text>
        )}
      </Flex>
    </Box>
  );
});

CommandBar.displayName = "CommandBar";
export default CommandBar;
