import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import type { LocationResult } from "../types";

// Same limits the backend applies in /api/location; shown here so the user sees
// why a sample will not count before it is even sent.
const STALE_AFTER_S = 120;
const LOW_ACCURACY_M = 200;
const POST_EVERY_MS = 10_000;

type Fix = { latitude: number; longitude: number; accuracy: number; at: number };

function readToken(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get("token");
}

export function PhonePage() {
  const [token] = useState(readToken);
  const [watching, setWatching] = useState(false);
  const [fix, setFix] = useState<Fix | null>(null);
  const [server, setServer] = useState<LocationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const watchId = useRef<number | null>(null);
  const lastPost = useRef(0);
  const wakeLock = useRef<{ release(): Promise<void> } | null>(null);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => () => stop(), []);

  async function send(next: Fix) {
    if (!token) return;
    lastPost.current = Date.now();
    try {
      setServer(
        await api.postLocation(token, {
          latitude: next.latitude,
          longitude: next.longitude,
          accuracy_meters: next.accuracy,
          captured_at: new Date(next.at).toISOString(),
        }),
      );
      setError(null);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) {
        stop();
        setError("Pairing expired or replaced. Scan a new QR code on the desktop.");
      } else {
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    }
  }

  function start() {
    if (!navigator.geolocation) {
      setError("This browser has no location support.");
      return;
    }
    setError(null);
    watchId.current = navigator.geolocation.watchPosition(
      (position) => {
        const next = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude,
          accuracy: position.coords.accuracy,
          at: position.timestamp,
        };
        setFix(next);
        if (Date.now() - lastPost.current >= POST_EVERY_MS) void send(next);
      },
      (exc) => setError(exc.code === exc.PERMISSION_DENIED ? "Location permission denied." : exc.message),
      { enableHighAccuracy: true, maximumAge: 0, timeout: 20_000 },
    );
    setWatching(true);
    const nav = navigator as Navigator & { wakeLock?: { request(type: "screen"): Promise<{ release(): Promise<void> }> } };
    nav.wakeLock?.request("screen").then((lock) => (wakeLock.current = lock)).catch(() => undefined);
  }

  function stop() {
    if (watchId.current !== null) navigator.geolocation.clearWatch(watchId.current);
    watchId.current = null;
    void wakeLock.current?.release();
    wakeLock.current = null;
    setWatching(false);
  }

  const age = fix ? Math.max(0, Math.round((now - fix.at) / 1000)) : null;
  const quality = !fix
    ? { tone: "", text: "No fix yet" }
    : age! > STALE_AFTER_S
      ? { tone: "danger", text: `Stale: last fix ${age}s ago` }
      : fix.accuracy > LOW_ACCURACY_M
        ? { tone: "warn", text: `Low accuracy: ±${Math.round(fix.accuracy)} m` }
        : { tone: "ok", text: `Fresh: ${age}s ago, ±${Math.round(fix.accuracy)} m` };

  return (
    <main className="phone">
      <h1>Heads Up location</h1>
      {!token ? (
        <div className="notice danger">Not paired. Open this page by scanning the QR code on the desktop.</div>
      ) : (
        <>
          <div className={`phone-status ${quality.tone}`}>{quality.text}</div>
          <dl className="facts">
            <dt>Server</dt>
            <dd className={server?.status === "accepted" ? "ok-text" : server ? "warn-text" : "muted"}>
              {server ? server.status : "nothing sent yet"}
              {server?.error_code ? ` (${server.error_code})` : ""}
            </dd>
            <dt>Position</dt>
            <dd>{fix ? `${fix.latitude.toFixed(5)}, ${fix.longitude.toFixed(5)}` : "—"}</dd>
            <dt>Sharing</dt>
            <dd>{watching ? `on, every ${POST_EVERY_MS / 1000}s while this page is open` : "off"}</dd>
          </dl>
          {error && <div className="notice danger">{error}</div>}
          <div className="buttons">
            {watching ? (
              <button className="danger" onClick={stop}>Stop sharing</button>
            ) : (
              <button className="primary" onClick={start}>Share location</button>
            )}
          </div>
          <p className="muted small">Keep this page open and the screen on. Location is only sent while sharing is on.</p>
        </>
      )}
    </main>
  );
}
