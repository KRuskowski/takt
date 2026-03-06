import { Box, Flex, Table, Text } from "@chakra-ui/react";

export function Th({
  children, ...props
}: {
  children: React.ReactNode;
  textAlign?: string;
  w?: string;
}) {
  return (
    <Table.ColumnHeader
      fontSize="13px"
      color="fg.muted"
      fontWeight={500}
      py={1}
      px={1.5}
      borderColor="border.muted"
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
      fontSize="13px"
      py={1}
      px={1.5}
      borderColor="border.muted"
      {...props}
    >
      {children}
    </Table.Cell>
  );
}

export function Panel({
  title, icon, children,
}: {
  title: string;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Box
      bg="bg.muted"
      border="1px solid"
      borderColor="border.muted"
      borderRadius="md"
      p={2}
    >
      <PanelHeader icon={icon}>{title}</PanelHeader>
      {children}
    </Box>
  );
}

export function PanelHeader({
  icon, children,
}: {
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Flex
      align="center"
      gap={1}
      fontSize="12px"
      fontWeight={600}
      color="fg.muted"
      textTransform="uppercase"
      letterSpacing="0.5px"
      mb={1.5}
    >
      {icon}
      {children}
    </Flex>
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
      color="fg.muted"
      fontSize="13px"
    >
      {children}
    </Text>
  );
}
