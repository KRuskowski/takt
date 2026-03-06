import {
  createSystem,
  defaultConfig,
  defineConfig,
} from "@chakra-ui/react";

const FONT =
  "'FiraMono Nerd Font', 'Fira Code', monospace";

const config = defineConfig({
  globalCss: {
    html: {
      colorScheme: "dark",
    },
    body: {
      fontSize: "13px",
      fontFamily: FONT,
      overflow: "hidden",
    },
    "*::-webkit-scrollbar": {
      width: "6px",
      height: "6px",
    },
    "*::-webkit-scrollbar-track": {
      bg: "bg",
    },
    "*::-webkit-scrollbar-thumb": {
      bg: "bg.emphasized",
      borderRadius: "3px",
    },
  },
  theme: {
    tokens: {
      fonts: {
        body: { value: FONT },
        heading: { value: FONT },
        mono: { value: FONT },
      },
      colors: {
        // Override grays for a slightly warmer dark
        gray: {
          50: { value: "#fafafa" },
          100: { value: "#f4f4f5" },
          200: { value: "#e4e4e7" },
          300: { value: "#d4d4d8" },
          400: { value: "#a1a1aa" },
          500: { value: "#71717a" },
          600: { value: "#52525b" },
          700: { value: "#3f3f46" },
          800: { value: "#27272a" },
          900: { value: "#1c1c1c" },
          950: { value: "#141414" },
        },
      },
    },
  },
});

export const system = createSystem(defaultConfig, config);
