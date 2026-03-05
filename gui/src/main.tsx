import {
  ChakraProvider,
  Toaster,
  ToastCloseTrigger,
  ToastDescription,
  ToastRoot,
  ToastTitle,
} from "@chakra-ui/react";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./style.css";
import { system } from "./theme";
import { toaster } from "./toast";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ChakraProvider value={system}>
      <App />
      <Toaster toaster={toaster}>
        {(toast) => (
          <ToastRoot>
            <ToastTitle>{toast.title}</ToastTitle>
            <ToastDescription>
              {toast.description}
            </ToastDescription>
            <ToastCloseTrigger />
          </ToastRoot>
        )}
      </Toaster>
    </ChakraProvider>
  </React.StrictMode>,
);
