import { Badge } from "@chakra-ui/react";

const STATUS_COLORS: Record<string, { bg: string; color: string }> = {
  running: { bg: "#14291a", color: "#22c55e" },
  passed: { bg: "#14291a", color: "#22c55e" },
  completed: { bg: "#14291a", color: "#22c55e" },
  failed: { bg: "#2a1414", color: "#dc2626" },
  queued: { bg: "#29250e", color: "#eab308" },
  pending: { bg: "#1e1e1e", color: "#737373" },
  cancelled: { bg: "#1e1e1e", color: "#737373" },
};

export default function StatusBadge({ status }: { status: string }) {
  const s = STATUS_COLORS[status] ?? STATUS_COLORS.pending;
  return (
    <Badge
      fontSize="10px"
      px={1.5}
      py={0}
      borderRadius="3px"
      fontWeight={600}
      bg={s.bg}
      color={s.color}
    >
      {status}
    </Badge>
  );
}
