import { Box, Flex } from "@chakra-ui/react";
import { useCallback, useEffect, useState } from "react";
import { ping } from "./api";
import { type Tab, dispatch } from "./commands";
import Agents from "./components/Agents";
import CommandBar from "./components/CommandBar";
import Dashboard from "./components/Dashboard";
import MetaAgents from "./components/MetaAgents";
import Pipeline from "./components/Pipeline";
import Targets from "./components/Targets";
import Workspaces from "./components/Workspaces";
import { showError, showSuccess } from "./toast";

const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "agents", label: "Agents" },
  { id: "pipeline", label: "Pipeline" },
  { id: "workspaces", label: "Workspaces" },
  { id: "targets", label: "Targets" },
  { id: "meta", label: "Meta" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [connected, setConnected] = useState(false);

  const checkConnection = useCallback(async () => {
    try {
      await ping();
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, []);

  useEffect(() => {
    checkConnection();
    const iv = setInterval(checkConnection, 10000);
    return () => clearInterval(iv);
  }, [checkConnection]);

  const handleCommand = useCallback(
    async (cmd: string) => {
      const result = await dispatch(cmd);
      if (result.tab) {
        setTab(result.tab);
      }
      if (result.message) {
        if (result.error) {
          showError(result.message);
        } else {
          showSuccess(result.message);
        }
      }
    },
    [],
  );

  return (
    <Flex direction="column" h="100vh" bg="#141414">
      {/* Custom titlebar */}
      <div className="titlebar">
        <div className="titlebar-controls">
          <button
            className="close"
            onClick={() => window.close()}
          />
          <button className="minimize" />
          <button className="maximize" />
        </div>
        <h1>takt</h1>
        <div className="titlebar-status">
          <span
            className={
              `status-dot ${connected ? "connected" : "disconnected"}`
            }
          />
          {connected ? "connected" : "disconnected"}
        </div>
      </div>

      {/* Tab nav */}
      <Flex
        gap={0}
        px={1}
        bg="#1c1c1c"
        borderBottom="1px solid #2e2e2e"
        flexShrink={0}
      >
        {TABS.map((t) => (
          <Box
            key={t.id}
            as="button"
            px={2.5}
            py={1}
            fontSize="11px"
            bg="transparent"
            border="none"
            borderBottom="2px solid"
            borderBottomColor={
              tab === t.id
                ? "#dc2626"
                : "transparent"
            }
            color={
              tab === t.id ? "#d4d4d4" : "#737373"
            }
            cursor="pointer"
            _hover={{ color: "#d4d4d4" }}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </Box>
        ))}
      </Flex>

      {/* Content */}
      <Box flex={1} overflow="auto" p={2}>
        {tab === "dashboard" && <Dashboard />}
        {tab === "agents" && <Agents />}
        {tab === "pipeline" && <Pipeline />}
        {tab === "workspaces" && <Workspaces />}
        {tab === "targets" && <Targets />}
        {tab === "meta" && <MetaAgents />}
      </Box>

      {/* Command bar */}
      <CommandBar onCommand={handleCommand} />
    </Flex>
  );
}
