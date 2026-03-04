import { useEffect, useRef, useState } from "react";

const BASE = "http://localhost:7433";

export interface SSEEvent {
  topic: string;
  data: unknown;
}

/**
 * Hook to subscribe to takt SSE events.
 *
 * @param topics - Topics to subscribe to (comma-separated).
 * @param onEvent - Callback for each event.
 * @returns connected state.
 */
export function useSSE(
  topics: string[],
  onEvent: (event: SSEEvent) => void,
): boolean {
  const [connected, setConnected] = useState(false);
  const callbackRef = useRef(onEvent);
  callbackRef.current = onEvent;

  useEffect(() => {
    const topicStr = topics.join(",");
    const url = `${BASE}/api/events?topics=${topicStr}`;
    const source = new EventSource(url);

    source.onopen = () => setConnected(true);
    source.onerror = () => setConnected(false);

    // Listen for all event types by using addEventListener
    // for each topic prefix.
    const handler = (e: MessageEvent) => {
      try {
        const data = JSON.parse(e.data);
        callbackRef.current({ topic: e.type, data });
      } catch {
        // Ignore parse errors.
      }
    };

    // SSE sends named events matching ZMQ topics.
    for (const topic of topics) {
      source.addEventListener(topic, handler);
    }

    // Also handle generic message events.
    source.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        callbackRef.current({ topic: "message", data });
      } catch {
        // Ignore.
      }
    };

    return () => {
      source.close();
      setConnected(false);
    };
  }, [topics.join(",")]);

  return connected;
}
