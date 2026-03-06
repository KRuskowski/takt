import { Box, Flex, Tabs } from "@chakra-ui/react";
import {
  useCallback, useEffect, useRef, useState,
} from "react";
import {
  RiBrainLine,
  RiDashboardLine,
  RiFolderLine,
  RiGitMergeLine,
  RiRobotLine,
  RiServerLine,
  RiSettings3Line,
} from "@remixicon/react";
import { ping } from "./api";
import { type Tab, dispatch } from "./commands";
import Agents from "./components/Agents";
import CommandBar, {
  type CommandBarHandle,
} from "./components/CommandBar";
import Dashboard from "./components/Dashboard";
import MetaAgents from "./components/MetaAgents";
import Pipeline from "./components/Pipeline";
import Targets from "./components/Targets";
import Settings from "./components/Settings";
import Workspaces from "./components/Workspaces";


type TabDef = {
  id: Tab;
  label: string;
  Icon: React.ComponentType<{
    size?: number | string;
  }>;
};

const TABS: TabDef[] = [
  { id: "dashboard", label: "Dashboard",
    Icon: RiDashboardLine },
  { id: "agents", label: "Agents",
    Icon: RiRobotLine },
  { id: "pipeline", label: "Pipeline",
    Icon: RiGitMergeLine },
  { id: "workspaces", label: "Workspaces",
    Icon: RiFolderLine },
  { id: "targets", label: "Deployments",
    Icon: RiServerLine },
  { id: "meta", label: "Meta",
    Icon: RiBrainLine },
  { id: "settings", label: "Settings",
    Icon: RiSettings3Line },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [connected, setConnected] = useState(false);
  const cmdRef = useRef<CommandBarHandle>(null);

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
        cmdRef.current?.setOutput({
          msg: result.message,
          error: !!result.error,
          multi: result.multi,
        });
      }
    },
    [],
  );

  return (
    <Flex direction="column" h="100vh" bg="bg">
      {/* Titlebar */}
      <Flex
        className="drag"
        align="center"
        justify="space-between"
        h="44px"
        px={2}
        bg="bg"
        borderBottom="1px solid"
        borderBottomColor="border"
        flexShrink={0}
        userSelect="none"
      >
        <img
          src="/logo.svg"
          alt="takt"
          style={{ height: "30px" }}
        />
        <Flex
          className="no-drag"
          align="center"
          gap={1}
          fontSize="12px"
          color="fg.muted"
        >
          <Box
            w="6px"
            h="6px"
            borderRadius="full"
            bg={connected ? "#22c55e" : "#ef4444"}
          />
          {connected ? "connected" : "disconnected"}
        </Flex>
      </Flex>

      {/* Tab nav */}
      <Tabs.Root
        value={tab}
        onValueChange={
          (d) => setTab(d.value as Tab)
        }
        variant="line"
        size="sm"
      >
        <Tabs.List
          bg="bg.muted"
          borderBottomColor="border.muted"
          px={1}
        >
          {TABS.map((t) => (
            <Tabs.Trigger
              key={t.id}
              value={t.id}
              fontSize="13px"
            >
              <t.Icon size={14} />
              {t.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </Tabs.Root>

      {/* Content */}
      <Box flex={1} overflow="auto" p={2}>
        {tab === "dashboard" && <Dashboard />}
        {tab === "agents" && <Agents />}
        {tab === "pipeline" && <Pipeline />}
        {tab === "workspaces" && <Workspaces />}
        {tab === "targets" && <Targets />}
        {tab === "meta" && <MetaAgents />}
        {tab === "settings" && <Settings />}
      </Box>

      <CommandBar
        ref={cmdRef}
        onCommand={handleCommand}
      />
    </Flex>
  );
}
