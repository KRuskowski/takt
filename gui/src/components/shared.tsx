import { Box, Table, Text } from "@chakra-ui/react";

export function Th({
  children, ...props
}: {
  children: React.ReactNode;
  textAlign?: string;
  w?: string;
}) {
  return (
    <Table.ColumnHeader
      fontSize="11px"
      color="#737373"
      fontWeight={500}
      py={1}
      px={1.5}
      borderColor="#2e2e2e"
      {...props}
    >
      {children}
    </Table.ColumnHeader>
  );
}

export function Td({
  children, ...props
}: {
  children: React.ReactNode;
  textAlign?: string;
  fontFamily?: string;
}) {
  return (
    <Table.Cell
      fontSize="11px"
      py={1}
      px={1.5}
      borderColor="#2e2e2e"
      {...props}
    >
      {children}
    </Table.Cell>
  );
}

export function Panel({
  title, children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <Box
      bg="#1c1c1c"
      border="1px solid #2e2e2e"
      borderRadius="4px"
      p={2}
    >
      <PanelHeader>{title}</PanelHeader>
      {children}
    </Box>
  );
}

export function PanelHeader({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <Text
      fontSize="10px"
      fontWeight={600}
      color="#737373"
      textTransform="uppercase"
      letterSpacing="0.5px"
      mb={1.5}
    >
      {children}
    </Text>
  );
}

export function Empty({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <Text
      textAlign="center"
      py={4}
      color="#737373"
      fontSize="11px"
    >
      {children}
    </Text>
  );
}
