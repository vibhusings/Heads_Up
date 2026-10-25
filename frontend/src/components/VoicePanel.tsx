import { useEffect, useRef, useState } from "react";
import type Vapi from "@vapi-ai/web";
import type { Plan } from "../types";

const PUBLIC_KEY = import.meta.env.VITE_VAPI_PUBLIC_KEY as string | undefined;
const ASSISTANT_ID = import.meta.env.VITE_VAPI_ASSISTANT_ID as string | undefined;

type Status = "idle" | "connecting" | "live" | "ending" | "error";
type Line = { role: string; text: string };

// Voice is another way to press the same buttons: the assistant calls the backend's
// /api/voice/webhook tools, and changes show up here through normal session polling.
export function VoicePanel({ plan, announcement }: { plan: Plan | undefined; announcement?: string }) {
  const vapi = useRef<Vapi | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState<string | null>(null);
  const [muted, setMuted] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [lines, setLines] = useState<Line[]>([]);
  const announcedPlan = useRef<string | null>(null);
  const configured = Boolean(PUBLIC_KEY && ASSISTANT_ID);

  useEffect(() => () => void vapi.current?.stop(), []);

  useEffect(() => {
    if (status !== "live" || !plan || !announcement || announcedPlan.current === plan.id || !vapi.current) return;
    vapi.current.send({
      type: "add-message",
      message: { role: "system", content: `A new approved-source scheduling plan is available. plan_id=${plan.id}, version=${plan.version}. ${announcement}` },
      triggerResponseEnabled: false,
    });
    vapi.current.say(announcement);
    announcedPlan.current = plan.id;
  }, [announcement, plan, status]);

  async function start() {
    if (!PUBLIC_KEY || !ASSISTANT_ID || status !== "idle") return;
    setStatus("connecting");
    setError(null);
    setLines([]);
    try {
      // The SDK and microphone are only touched after the user clicks.
      const { default: VapiClient } = await import("@vapi-ai/web");
      const client = vapi.current ?? new VapiClient(PUBLIC_KEY);
      if (!vapi.current) {
        client.on("call-start", () => setStatus("live"));
        client.on("call-end", () => {
          setStatus("idle");
          setSpeaking(false);
          setMuted(false);
          announcedPlan.current = null;
        });
        client.on("speech-start", () => setSpeaking(true));
        client.on("speech-end", () => setSpeaking(false));
        client.on("message", (message) => {
          if (message?.type === "transcript" && message.transcriptType === "final") {
            setLines((prev) => [...prev.slice(-5), { role: message.role, text: message.transcript }]);
          }
        });
        client.on("error", (exc) => {
          setError(exc?.error?.message ?? exc?.message ?? "Voice session error");
          setStatus("error");
        });
        vapi.current = client;
      }
      await client.start(ASSISTANT_ID, {
        firstMessage: "I’m checking your current plans.",
        variableValues: plan ? { plan_id: plan.id, plan_version: plan.version } : {},
      });
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setStatus("error");
    }
  }

  async function stop() {
    setStatus("ending");
    await vapi.current?.stop();
    setStatus("idle");
  }

  function toggleMute() {
    if (!vapi.current) return;
    vapi.current.setMuted(!muted);
    setMuted(!muted);
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Voice</h2>
        <span className={status === "live" ? "pill live" : status === "error" ? "pill danger" : "pill"}>
          {status === "live" ? (speaking ? "assistant speaking" : "listening") : status}
        </span>
      </div>
      {!configured && (
        <p className="muted small">Set VITE_VAPI_PUBLIC_KEY and VITE_VAPI_ASSISTANT_ID to enable voice.</p>
      )}
      <div className="buttons">
        {status === "live" || status === "ending" ? (
          <>
            <button className="danger" onClick={stop} disabled={status === "ending"}>
              End voice
            </button>
            <button onClick={toggleMute}>{muted ? "Unmute" : "Mute"}</button>
          </>
        ) : (
          <button className="primary" onClick={status === "error" ? () => setStatus("idle") : start} disabled={!configured || status === "connecting"}>
            {status === "connecting" ? "Connecting…" : status === "error" ? "Reset voice" : "Start voice"}
          </button>
        )}
        {plan && <span className="muted small">Voice approves only the version it reads back (now v{plan.version}).</span>}
      </div>
      {error && <div className="notice danger">{error}</div>}
      {lines.length > 0 && (
        <ul className="transcript">
          {lines.map((line, index) => (
            <li key={index}>
              <span className="muted small">{line.role === "assistant" ? "Heads Up" : "You"}</span> {line.text}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
