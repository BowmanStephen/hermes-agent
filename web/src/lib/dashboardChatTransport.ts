import { HERMES_BASE_PATH } from "@/lib/api";

export interface DashboardChatSession {
  channel: string;
  resume: string | null;
}

function wsProtocol(): "ws:" | "wss:" {
  return window.location.protocol === "https:" ? "wss:" : "ws:";
}

function dashboardWsUrl(path: "/api/events" | "/api/pty", qs: URLSearchParams) {
  return `${wsProtocol()}//${window.location.host}${HERMES_BASE_PATH}${path}?${qs.toString()}`;
}

export function buildChatPtyUrl({
  token,
  resume,
  channel,
}: {
  token: string;
  resume: string | null;
  channel: string;
}): string {
  const qs = new URLSearchParams({ token, channel });
  if (resume) qs.set("resume", resume);
  return dashboardWsUrl("/api/pty", qs);
}

export function createDashboardChatSession({
  resume,
}: {
  resume: string | null;
}): DashboardChatSession {
  return {
    channel: generateChannelId(),
    resume,
  };
}

export function buildChatEventsUrl({
  token,
  channel,
}: {
  token: string;
  channel: string;
}): string {
  const qs = new URLSearchParams({ token, channel });
  return dashboardWsUrl("/api/events", qs);
}

export function encodePtyResizeFrame(cols: number, rows: number): string {
  return `\x1b[RESIZE:${Math.max(1, cols)};${Math.max(1, rows)}]`;
}

// eslint-disable-next-line no-control-regex -- intentional ESC byte in xterm SGR mouse report parser
const SGR_MOUSE_RE = /^\x1b\[<(\d+);(\d+);(\d+)([Mm])$/;

export function isSgrMouseReport(data: string): boolean {
  return SGR_MOUSE_RE.test(data);
}

function generateChannelId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `chat-${Math.random().toString(36).slice(2)}-${Date.now().toString(36)}`;
}
