import { useCallback, useEffect, useRef } from "react";
import { useSSE } from "./useSSE";

/**
 * Hook that calls a refresh callback on SSE events,
 * with a polling fallback interval.
 *
 * @param topics - SSE topics to subscribe to.
 * @param refresh - Callback to invoke on events.
 * @param fallbackMs - Polling interval in ms (default 30s).
 */
export function useSSERefresh(
  topics: string[],
  refresh: () => void,
  fallbackMs = 30000,
) {
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  // SSE-driven refresh.
  const onEvent = useCallback(() => {
    refreshRef.current();
  }, []);
  useSSE(topics, onEvent);

  // Fallback polling.
  useEffect(() => {
    refreshRef.current();
    const id = setInterval(
      () => refreshRef.current(), fallbackMs,
    );
    return () => clearInterval(id);
  }, [fallbackMs]);
}
