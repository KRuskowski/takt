/** Toast notification helpers using Chakra v3 toaster. */

import { createToaster } from "@chakra-ui/react";

export const toaster = createToaster({
  placement: "bottom-end",
  pauseOnPageIdle: true,
});

export function showError(msg: string) {
  toaster.create({
    title: "Error",
    description: msg,
    type: "error",
    duration: 4000,
  });
}

export function showSuccess(msg: string) {
  toaster.create({
    title: "Success",
    description: msg,
    type: "success",
    duration: 3000,
  });
}
