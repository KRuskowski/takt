import { Box, Button, Flex, Text } from "@chakra-ui/react";
import { markdown } from "@codemirror/lang-markdown";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView } from "@codemirror/view";
import {
  EditorState,
} from "@codemirror/state";
import { basicSetup } from "codemirror";
import {
  useCallback, useEffect, useRef, useState,
} from "react";
import { RiSaveLine } from "@remixicon/react";
import { getTemplate, putTemplate } from "../api";
import { showError, showSuccess } from "../toast";

interface Props {
  file: string;
  label?: string;
}

const theme = EditorView.theme({
  "&": {
    fontSize: "12px",
    fontFamily:
      "'FiraMono Nerd Font', 'Fira Code', monospace",
    backgroundColor: "#0a0a0a",
    height: "100%",
  },
  ".cm-content": {
    caretColor: "#d4d4d4",
    padding: "8px",
  },
  ".cm-gutters": {
    backgroundColor: "#141414",
    borderRight: "1px solid #2e2e2e",
    color: "#525252",
  },
  ".cm-activeLine": {
    backgroundColor: "#1c1c1c",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "#1c1c1c",
  },
  "&.cm-focused .cm-cursor": {
    borderLeftColor: "#d4d4d4",
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground":
    {
      backgroundColor: "#2a2a2a",
    },
});

export default function MarkdownEditor({
  file, label,
}: Props) {
  const containerRef =
    useRef<HTMLDivElement>(null);
  const viewRef =
    useRef<EditorView | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const data = await getTemplate(file);
        if (cancelled) return;

        const state = EditorState.create({
          doc: data.content,
          extensions: [
            basicSetup,
            markdown(),
            oneDark,
            theme,
            EditorView.lineWrapping,
            EditorView.updateListener.of(
              (update) => {
                if (update.docChanged)
                  setDirty(true);
              },
            ),
          ],
        });

        if (viewRef.current) {
          viewRef.current.destroy();
        }

        viewRef.current = new EditorView({
          state,
          parent: containerRef.current!,
        });
        setLoading(false);
        setDirty(false);
      } catch (e) {
        if (!cancelled) {
          showError(
            e instanceof Error
              ? e.message
              : "Failed to load",
          );
          setLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      viewRef.current?.destroy();
      viewRef.current = null;
    };
  }, [file]);

  const save = useCallback(async () => {
    if (!viewRef.current) return;
    const content =
      viewRef.current.state.doc.toString();
    try {
      await putTemplate(file, content);
      setDirty(false);
      showSuccess(`Saved ${file}`);
    } catch (e) {
      showError(
        e instanceof Error
          ? e.message
          : "Save failed",
      );
    }
  }, [file]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        (e.ctrlKey || e.metaKey) && e.key === "s"
      ) {
        e.preventDefault();
        save();
      }
    };
    window.addEventListener("keydown", handler);
    return () =>
      window.removeEventListener(
        "keydown", handler,
      );
  }, [save]);

  return (
    <Flex direction="column" h="100%">
      <Flex
        justify="space-between"
        align="center"
        px={2}
        py={1}
        bg="bg.muted"
        borderBottom="1px solid"
        borderBottomColor="border.muted"
        flexShrink={0}
      >
        <Text fontSize="13px" color="fg.muted">
          {label ?? file}
          {dirty && (
            <Text
              as="span"
              color="#eab308"
              ml={1}
            >
              (modified)
            </Text>
          )}
        </Text>
        <Button
          size="2xs"
          variant="outline"
          onClick={save}
          disabled={!dirty}
        >
          <RiSaveLine size={14} />
          Save
        </Button>
      </Flex>
      <Box
        ref={containerRef}
        flex={1}
        overflow="auto"
        bg="#0a0a0a"
      >
        {loading && (
          <Text
            textAlign="center"
            py={4}
            color="fg.muted"
            fontSize="13px"
          >
            Loading...
          </Text>
        )}
      </Box>
    </Flex>
  );
}
