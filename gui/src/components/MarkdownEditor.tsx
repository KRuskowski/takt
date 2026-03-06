import {
  Box, Button, Flex, Text,
} from "@chakra-ui/react";
import { markdown } from "@codemirror/lang-markdown";
import {
  HighlightStyle,
  syntaxHighlighting,
} from "@codemirror/language";
import { tags as t } from "@lezer/highlight";
import { EditorView } from "@codemirror/view";
import { EditorState } from "@codemirror/state";
import { basicSetup } from "codemirror";
import {
  useCallback, useEffect, useRef, useState,
} from "react";
import { RiSaveLine } from "@remixicon/react";
import {
  getTemplate, putTemplate,
} from "../api";
import { showError, showSuccess } from "../toast";

interface Props {
  file: string;
  label?: string;
  load?: () => Promise<{ content: string }>;
  save?: (content: string) => Promise<unknown>;
}

// Psychotropic nvim palette.
const P = {
  bg: "#101010",
  grey7: "#121212",
  grey11: "#1c1c1c",
  grey15: "#262626",
  grey18: "#2e2e2e",
  grey27: "#444444",
  grey39: "#626262",
  grey62: "#9e9e9e",
  white: "#F6FAFA",
  khaki: "#f9cb52",
  orange: "#de935f",
  coral: "#f09479",
  lime: "#99da3d",
  turquoise: "#7fd3a5",
  lightblue: "#4DB9F4",
  sky: "#7FB7EF",
  lavender: "#adadf3",
  magenta: "#FF03F2",
  magenta2: "#B31DAB",
  sand: "#EFDB8A",
};

const theme = EditorView.theme({
  "&": {
    fontSize: "12px",
    fontFamily:
      "'FiraMono Nerd Font', 'Fira Code',"
      + " monospace",
    backgroundColor: P.bg,
    height: "100%",
  },
  ".cm-content": {
    caretColor: P.white,
    padding: "8px",
  },
  ".cm-gutters": {
    backgroundColor: P.grey7,
    borderRight: `1px solid ${P.grey18}`,
    color: P.grey27,
  },
  ".cm-activeLine": {
    backgroundColor: P.grey11,
  },
  ".cm-activeLineGutter": {
    backgroundColor: P.grey11,
  },
  "&.cm-focused .cm-cursor": {
    borderLeftColor: P.white,
  },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground":
    {
      backgroundColor: P.grey18,
    },
  ".cm-matchingBracket": {
    color: `${P.orange} !important`,
    fontWeight: "bold",
  },
  ".cm-searchMatch": {
    backgroundColor: P.grey27,
  },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: P.orange,
    color: P.bg,
  },
});

const psychotropicHL = HighlightStyle.define([
  { tag: t.comment, color: P.grey62,
    fontStyle: "italic" },
  { tag: t.keyword, color: P.lime,
    fontWeight: "bold" },
  { tag: t.string, color: P.khaki },
  { tag: t.number, color: P.coral },
  { tag: t.bool, color: P.lightblue,
    fontWeight: "bold" },
  { tag: t.variableName, color: P.lightblue },
  { tag: t.function(t.variableName),
    color: P.sand },
  { tag: t.typeName, color: P.turquoise },
  { tag: t.operator, color: P.white },
  { tag: t.punctuation, color: P.white },
  { tag: t.bracket, color: P.white },
  { tag: t.meta, color: P.orange },
  { tag: t.link, color: P.lightblue,
    textDecoration: "underline" },
  { tag: t.url, color: P.sky },
  { tag: t.heading, color: P.lime,
    fontWeight: "bold" },
  { tag: t.heading1, color: P.lime,
    fontWeight: "bold" },
  { tag: t.heading2, color: P.turquoise,
    fontWeight: "bold" },
  { tag: t.heading3, color: P.sand,
    fontWeight: "bold" },
  { tag: t.emphasis, color: P.lavender,
    fontStyle: "italic" },
  { tag: t.strong, color: P.white,
    fontWeight: "bold" },
  { tag: t.strikethrough, color: P.grey39,
    textDecoration: "line-through" },
  { tag: t.monospace, color: P.coral },
  { tag: t.processingInstruction,
    color: P.magenta2 },
  { tag: t.invalid, color: P.magenta },
]);

export default function MarkdownEditor({
  file, label, load, save: saveFn,
}: Props) {
  const containerRef =
    useRef<HTMLDivElement>(null);
  const viewRef =
    useRef<EditorView | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(true);

  const doLoad = useCallback(
    () => load
      ? load()
      : getTemplate(file),
    [file, load],
  );

  const doSave = useCallback(
    (content: string) => saveFn
      ? saveFn(content)
      : putTemplate(file, content),
    [file, saveFn],
  );

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const data = await doLoad();
        if (cancelled) return;

        const state = EditorState.create({
          doc: data.content,
          extensions: [
            basicSetup,
            markdown(),
            theme,
            syntaxHighlighting(psychotropicHL),
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
  }, [doLoad]);

  const handleSave = useCallback(async () => {
    if (!viewRef.current) return;
    const content =
      viewRef.current.state.doc.toString();
    try {
      await doSave(content);
      setDirty(false);
      showSuccess(
        `Saved ${label ?? file}`,
      );
    } catch (e) {
      showError(
        e instanceof Error
          ? e.message
          : "Save failed",
      );
    }
  }, [doSave, file, label]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        (e.ctrlKey || e.metaKey) && e.key === "s"
      ) {
        e.preventDefault();
        handleSave();
      }
    };
    window.addEventListener("keydown", handler);
    return () =>
      window.removeEventListener(
        "keydown", handler,
      );
  }, [handleSave]);

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
          onClick={handleSave}
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
        bg={P.bg}
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
