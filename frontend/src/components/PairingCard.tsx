import { useEffect, useState } from "react";
import QRCode from "qrcode";
import { api } from "../api";
import type { PairingResult, Signal } from "../types";

// The phone needs HTTPS for geolocation, so this is usually a tunnel to the Vite server.
const DEFAULT_ORIGIN = (import.meta.env.VITE_PHONE_ORIGIN as string | undefined) || window.location.origin;

export function PairingCard({ signals }: { signals: Signal[] }) {
  const [origin, setOrigin] = useState(DEFAULT_ORIGIN);
  const [pairing, setPairing] = useState<PairingResult | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const link = pairing ? `${origin.replace(/\/$/, "")}/phone.html#token=${encodeURIComponent(pairing.token)}` : null;
  const latest = signals
    .filter((signal) => signal.source === "phone")
    .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at))[0];

  useEffect(() => {
    if (!link) return setQr(null);
    QRCode.toDataURL(link, { margin: 1, width: 360 }).then(setQr, () => setQr(null));
  }, [link]);

  async function pair() {
    setError(null);
    try {
      setPairing(await api.pairPhone());
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Phone location</h2>
        {pairing && <span className="pill ok">paired until {new Date(pairing.expires_at).toLocaleTimeString()}</span>}
      </div>
      <label className="draft">
        <span className="muted small">Phone page address (HTTPS)</span>
        <input value={origin} onChange={(e) => setOrigin(e.target.value)} />
      </label>
      {!origin.startsWith("https://") && (
        <p className="warn-text small">Phones only share location with HTTPS pages; use a tunnel URL here.</p>
      )}
      <div className="buttons" style={{ margin: "10px 0" }}>
        <button onClick={pair}>{pairing ? "New pairing code" : "Pair phone"}</button>
        {pairing && <span className="muted small">A new code unpairs the previous phone.</span>}
      </div>
      {error && <div className="notice danger">{error}</div>}
      {qr && link && (
        <div>
          <img className="qr" src={qr} alt="Scan with your phone to pair" />
          <a className="small" href={link} target="_blank" rel="noreferrer">
            open phone page
          </a>
        </div>
      )}
      <p className="muted small">
        {latest
          ? `Last phone fix ${new Date(latest.observed_at).toLocaleTimeString()}, ±${Math.round(latest.position?.accuracy_meters ?? 0)} m`
          : "No phone location received yet."}
      </p>
    </section>
  );
}
