import { Badge, Flex } from "@chakra-ui/react";
import {
  RiCheckLine,
  RiCloseCircleLine,
  RiForbidLine,
  RiLoader4Line,
  RiTimeLine,
} from "@remixicon/react";

const STATUS_CFG: Record<string, {
  bg: string;
  color: string;
  Icon: React.ComponentType<{
    size?: number | string;
  }>;
}> = {
  running: {
    bg: "#14291a", color: "#22c55e",
    Icon: RiLoader4Line,
  },
  passed: {
    bg: "#14291a", color: "#22c55e",
    Icon: RiCheckLine,
  },
  completed: {
    bg: "#14291a", color: "#22c55e",
    Icon: RiCheckLine,
  },
  failed: {
    bg: "#2a1414", color: "#dc2626",
    Icon: RiCloseCircleLine,
  },
  queued: {
    bg: "#29250e", color: "#eab308",
    Icon: RiTimeLine,
  },
  pending: {
    bg: "#1e1e1e", color: "#737373",
    Icon: RiTimeLine,
  },
  cancelled: {
    bg: "#1e1e1e", color: "#737373",
    Icon: RiForbidLine,
  },
};

const DEFAULT = STATUS_CFG.pending;

export default function StatusBadge(
  { status }: { status: string },
) {
  const s = STATUS_CFG[status] ?? DEFAULT;
  return (
    <Badge
      fontSize="12px"
      px={1.5}
      py={0}
      borderRadius="3px"
      fontWeight={600}
      bg={s.bg}
      color={s.color}
    >
      <Flex align="center" gap={0.5}>
        <s.Icon size={12} />
        {status}
      </Flex>
    </Badge>
  );
}
