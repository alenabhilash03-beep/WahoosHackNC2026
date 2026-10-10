"use client";
/**
 * Voice check (Phase 1, step 9). Copy to web/app/dev/voice-check/page.tsx, open /dev/voice-check,
 * press Start, talk for about a minute (interrupt the persona once), press Stop, then Copy summary
 * and paste it at the top of server/app/speech_engine.py.
 *
 * It logs every @elevenlabs/react callback with its time and payload, and answers the first-hour
 * questions from what actually happened on your account:
 *   - which conversation id the browser sees (onConnect, getId) - compare with the server's
 *     "engine session started <id>" log line;
 *   - which event types arrive (a missing type means client_events lacks it; run
 *     python tools/checks/check_engine.py --fix). On WebRTC the SDK makes the "audio" events itself
 *     from the incoming track, so their count matches the onAudio chunks;
 *   - the payload keys of onMessage, onModeChange, onInterruption, onAgentResponseCorrection;
 *   - how late onModeChange("listening") arrives after the persona's audio actually stops;
 *   - whether the persona's text arrives before or after its audio starts.
 */
import { ConversationProvider, useConversation } from "@elevenlabs/react";
import { useRef, useState } from "react";
import { api } from "@/lib/api";

type Row = { t: number; name: string; data: string };
type Burst = { start: number; end: number };
const BURST_GAP_S = 0.3;

export default function VoiceCheckPage() {
  return (
    <ConversationProvider>
      <VoiceCheck />
    </ConversationProvider>
  );
}

function VoiceCheck() {
  const t0 = useRef(0);
  const now = () => (performance.now() - t0.current) / 1000;
  const [rows, setRows] = useState<Row[]>([]);
  const [summary, setSummary] = useState("");
  const log = useRef<Row[]>([]);
  const bursts = useRef<Burst[]>([]);
  const audioChunks = useRef(0);
  const audioAfterInterrupt = useRef(0);
  const interrupted = useRef(false);
  const types = useRef(new Map<string, number>());
  const keys = useRef(new Map<string, string>());
  const ids = useRef<{ onConnect?: string; getId?: string }>({});
  const modes = useRef<{ t: number; mode: string }[]>([]);
  const agentTexts = useRef<number[]>([]);
  const sessionId = useRef<string | undefined>(undefined);

  function add(name: string, payload?: unknown) {
    if (payload && typeof payload === "object" && !keys.current.has(name)) {
      keys.current.set(name, Object.keys(payload as object).join(", "));
    }
    const data = payload === undefined ? "" : JSON.stringify(payload).slice(0, 300);
    log.current.push({ t: now(), name, data });
    setRows([...log.current]);
  }

  const conversation = useConversation({
    onConnect: (p) => { ids.current.onConnect = p.conversationId; add("onConnect", p); bind(p.conversationId); },
    onDisconnect: (d) => add("onDisconnect", d),
    onError: (message, context) => add("onError", { message, context: String(context ?? "") }),
    onStatusChange: (p) => add("onStatusChange", p),
    onModeChange: (p) => { modes.current.push({ t: now(), mode: p.mode }); add("onModeChange", p); },
    onMessage: (p) => { if (p.role === "agent") agentTexts.current.push(now()); add("onMessage", p); },
    onInterruption: (p) => { interrupted.current = true; add("onInterruption", p); },
    onAgentResponseCorrection: (p) => add("onAgentResponseCorrection", p),
    onConversationMetadata: (p) => add("onConversationMetadata", p),
    onAudio: () => {
      const t = now();
      audioChunks.current += 1;
      if (interrupted.current) audioAfterInterrupt.current += 1;
      const last = bursts.current.at(-1);
      if (last && t - last.end <= BURST_GAP_S) last.end = t;
      else { bursts.current.push({ start: t, end: t }); add("onAudio (burst starts)"); }
    },
    onIncomingEvent: (e: { type?: string }) => {
      const type = e?.type ?? "?";
      types.current.set(type, (types.current.get(type) ?? 0) + 1);
    },
  });

  async function bind(conversationId: string) {
    try {
      ids.current.getId = conversation.getId();
    } catch (e) {
      ids.current.getId = `getId() threw: ${e}`;
    }
    if (sessionId.current) {
      await api(`/api/sessions/${sessionId.current}/bind`, { method: "POST", body: JSON.stringify({ conversation_id: conversationId }) });
      add("bind ok", { conversationId });
    }
  }

  async function start() {
    t0.current = performance.now();
    const scenario = new URLSearchParams(window.location.search).get("scenario") ?? "alumni_coffee_chat";
    const s = await api<{ session_id: string; token: string; first_message: string }>("/api/sessions", {
      method: "POST", body: JSON.stringify({ scenario_id: scenario }),
    });
    sessionId.current = s.session_id;
    add("session created", { session_id: s.session_id });
    const returned = conversation.startSession({ conversationToken: s.token, overrides: { agent: { firstMessage: s.first_message } } });
    add("startSession returned", { value: returned === undefined ? "undefined" : String(returned) });
  }

  function stop() {
    conversation.endSession();
    setSummary(buildSummary());
  }

  function buildSummary(): string {
    const lags = bursts.current.map((b) => {
      const next = modes.current.find((m) => m.mode === "listening" && m.t >= b.end - 0.2);
      return next ? next.t - b.end : NaN;
    }).filter((x) => !Number.isNaN(x)).sort((a, b) => a - b);
    const textOffsets = agentTexts.current.map((t) => {
      const nearest = bursts.current.reduce<Burst | null>((best, b) =>
        best === null || Math.abs(b.start - t) < Math.abs(best.start - t) ? b : best, null);
      return nearest ? t - nearest.start : NaN;
    }).filter((x) => !Number.isNaN(x));
    const median = (xs: number[]) => (xs.length ? xs[Math.floor(xs.length / 2)].toFixed(2) + " s" : "n/a");
    const fmt = (xs: number[]) => xs.map((x) => (x >= 0 ? "+" : "") + x.toFixed(2)).join(", ") || "none";
    return [
      `Voice check, @elevenlabs/react on ${new Date().toISOString()}`,
      `conversation id: onConnect=${ids.current.onConnect ?? "never fired"} getId()=${ids.current.getId ?? "n/a"}`,
      `startSession() return value: ${log.current.find((r) => r.name === "startSession returned")?.data ?? "n/a"}`,
      `incoming event types: ${[...types.current].map(([k, v]) => `${k} x${v}`).join(", ") || "none"}`,
      ...[...keys.current].map(([name, k]) => `payload keys ${name}: ${k}`),
      `onAudio: ${audioChunks.current} chunks in ${bursts.current.length} bursts; ${audioAfterInterrupt.current} chunks after the first interruption`,
      `mode->listening lag after audio stops: median ${median(lags)} (all: ${fmt(lags)})`,
      `agent text vs its audio start (negative = text first): ${fmt(textOffsets)}`,
    ].join("\n");
  }

  return (
    <main style={{ padding: 16, fontFamily: "ui-monospace, monospace", fontSize: 13 }}>
      <p>Status: {conversation.status} | mode: {conversation.mode} | wear headphones, talk for a minute, interrupt once</p>
      <button onClick={start} disabled={conversation.status === "connected"}>Start</button>{" "}
      <button onClick={stop} disabled={conversation.status !== "connected"}>Stop</button>{" "}
      <button onClick={() => setSummary(buildSummary())}>Summary now</button>{" "}
      <button onClick={() => navigator.clipboard.writeText(summary)} disabled={!summary}>Copy summary</button>
      {summary && <pre style={{ whiteSpace: "pre-wrap", background: "#8882", padding: 8 }}>{summary}</pre>}
      <table>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td style={{ paddingRight: 12 }}>{r.t.toFixed(2)}</td>
              <td style={{ paddingRight: 12 }}>{r.name}</td>
              <td style={{ wordBreak: "break-all" }}>{r.data}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
