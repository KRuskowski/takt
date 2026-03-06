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
import {
  commonPrefix,
  getCompletions,
} from "../commands";

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
  const compRef = useRef<{
    matches: string[];
    idx: number;
    base: string;
  } | null>(null);

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

  const handleTab = async (shift: boolean) => {
    const comp = compRef.current;

    // Already cycling — advance index.
    if (comp && comp.matches.length > 1) {
      const dir = shift ? -1 : 1;
      comp.idx =
        (comp.idx + dir + comp.matches.length)
        % comp.matches.length;
      const parts = comp.base.split(/\s+/);
      parts[parts.length - 1] =
        comp.matches[comp.idx];
      setValue(parts.join(" "));
      setOutput({
        msg: comp.matches
          .map((m, i) =>
            i === comp.idx ? `[${m}]` : m,
          )
          .join("  "),
        error: false,
        multi: true,
      });
      return;
    }

    const matches = await getCompletions(value);
    if (matches.length === 0) return;

    const parts = value.split(/\s+/);
    if (matches.length === 1) {
      parts[parts.length - 1] = matches[0];
      setValue(parts.join(" ") + " ");
      setOutput(null);
      compRef.current = null;
    } else {
      // Complete common prefix first.
      const cp = commonPrefix(matches);
      if (
        cp.length
        > (parts[parts.length - 1] ?? "").length
      ) {
        parts[parts.length - 1] = cp;
        setValue(parts.join(" "));
      }
      // Start cycling from first match.
      compRef.current = {
        matches,
        idx: -1,
        base: parts.join(" "),
      };
      setOutput({
        msg: matches.join("  "),
        error: false,
        multi: true,
      });
    }
  };

  const handleKey = (
    e: KeyboardEvent<HTMLInputElement>,
  ) => {
    if (e.key === "Tab") {
      e.preventDefault();
      handleTab(e.shiftKey);
    } else if (e.key === "Enter") {
      if (compRef.current
        && compRef.current.idx >= 0) {
        // Accept current completion.
        const c = compRef.current;
        const parts = c.base.split(/\s+/);
        parts[parts.length - 1] =
          c.matches[c.idx];
        setValue(parts.join(" ") + " ");
        compRef.current = null;
        setOutput(null);
        return;
      }
      compRef.current = null;
      submit();
    } else if (e.key === "Escape") {
      compRef.current = null;
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
        h="40px"
        bg="bg.muted"
        borderTop="1px solid"
        borderTopColor="border"
        px={2.5}
        gap={2}
        onClick={() =>
          inputRef.current?.focus()
        }
        cursor="text"
      >
        <Box color="fg.muted" flexShrink={0}>
          <RiTerminalLine size={16} />
        </Box>
        <Box flex={1}>
          <Input
            ref={inputRef}
            value={value}
            onChange={(e) => {
              setValue(e.target.value);
              setHistIdx(-1);
              compRef.current = null;
            }}
            onKeyDown={handleKey}
            variant="flushed"
            fontSize="14px"
            color="fg"
            h="40px"
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
            fontSize="13px"
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
