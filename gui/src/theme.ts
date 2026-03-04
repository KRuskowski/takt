import { createSystem, defaultConfig, defineConfig } from "@chakra-ui/react";

const FONT_BODY = "'FiraMono Nerd Font', 'Fira Code', monospace";
const FONT_MONO = "'FiraCode Nerd Font', 'Fira Code', monospace";

const config = defineConfig({
  globalCss: {
    body: {
      bg: "#141414",
      color: "#d4d4d4",
      fontSize: "12px",
      fontFamily: FONT_BODY,
      overflow: "hidden",
    },
    "*::-webkit-scrollbar": {
      width: "6px",
      height: "6px",
    },
    "*::-webkit-scrollbar-track": {
      bg: "#141414",
    },
    "*::-webkit-scrollbar-thumb": {
      bg: "#3a3a3a",
      borderRadius: "3px",
    },
  },
  theme: {
    tokens: {
      fonts: {
        body: { value: FONT_BODY },
        heading: { value: FONT_BODY },
        mono: { value: FONT_MONO },
      },
      colors: {
        bg: { value: "#141414" },
        "bg.panel": { value: "#1c1c1c" },
        "bg.card": { value: "#242424" },
        "bg.hover": { value: "#2a2a2a" },
        "border.dim": { value: "#2e2e2e" },
        "text.dim": { value: "#737373" },
      },
    },
    semanticTokens: {
      colors: {
        "bg.canvas": { value: "#141414" },
        "bg.muted": { value: "#1c1c1c" },
        "bg.subtle": { value: "#242424" },
        "border.muted": { value: "#2e2e2e" },
        "fg.muted": { value: "#737373" },
      },
    },
  },
});

export const system = createSystem(defaultConfig, config);
