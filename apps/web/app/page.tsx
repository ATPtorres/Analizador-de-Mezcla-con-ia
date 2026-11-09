"use client";

import { useEffect, useState, type FormEvent } from "react";

type Value = number | null;
type Report = {
  duration_s: number;
  engine: string;
  loudness: { integrated_lufs: Value; true_peak_dbtp: Value; lra_lu: Value; short_term: { end_s: number; lufs: Value }[] };
  sample_peak_dbfs: Value;
  rms_dbfs: Value;
  crest_db: Value;
  stereo: { mode: string; correlation: Value; width_pct: Value; mono_delta_db: Value; mono_cancellation?: boolean };
  bands_pct: Record<string, Value>;
  spectrum: { hz: number; dbfs: Value }[];
  notes: { code: string; text: string }[];
};
type Entry = { id: string; report: Report };
type Item = { id: string; created_at: string; format: string; duration_s: number };
const path = "/mixapi/v1/local/reports";
const bandNames: Record<string, string> = { subgrave: "Subgrave", grave: "Grave", medio: "Medio", medio_alto: "Medio-alto", aire: "Aire" };
const fmt = (value: Value) => value === null ? "—" : new Intl.NumberFormat("es", { maximumFractionDigits: 2 }).format(value);

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : "Error de conexión o procesamiento.");
  }
  return response.status === 204 ? undefined as T : response.json();
}

function Trace({ title, yUnit, series, ticks }: { title: string; yUnit: string; series: { x: number; y: Value }[]; ticks: { x: number; label: string }[] }) {
  const valid = series.filter((p): p is { x: number; y: number } => p.y !== null);
  if (!valid.length) return <p>{title} · medición no disponible.</p>;
  const start = ticks[0].x;
  const span = (ticks.at(-1)?.x ?? start) - start || 1;
  const top = Math.max(...valid.map(p => p.y));
  const bottom = Math.min(...valid.map(p => p.y));
  let next = "M";
  const drawing = series.map(p => {
    if (p.y === null) { next = "M"; return ""; }
    const point = next + (56 + 480 * (p.x - start) / span) + "," + (120 - 102 * (p.y - bottom) / (top - bottom || 1));
    next = "L";
    return point;
  }).join(" ");
  return <figure><figcaption>{title}</figcaption>
    <svg role="img" aria-label={title + ". Rango " + fmt(bottom) + " a " + fmt(top) + " " + yUnit} viewBox="0 0 550 155">
      <path d="M56 16V121H536" stroke="#596986" fill="none"/>
      <text x="4" y="21">{fmt(top)}</text><text x="4" y="123">{fmt(bottom)}</text>
      {ticks.map(tick => <text key={tick.x} x={56 + 480 * (tick.x - start) / span} y="142" textAnchor={tick.x === start ? "start" : tick.x === start + span ? "end" : "middle"}>{tick.label}</text>)}
      <path d={drawing} stroke="#72f1c9" strokeWidth="2" fill="none"/>
      {valid.length === 1 && <circle cx={56 + 480 * (valid[0].x - start) / span} cy="120" r="4" fill="#72f1c9"/>}
    </svg><small>{yUnit}</small>
  </figure>;
}

function metrics(report: Report): [string, Value, string][] {
  return [
    ["Loudness integrado", report.loudness.integrated_lufs, "LUFS"],
    ["True peak", report.loudness.true_peak_dbtp, "dBTP"],
    ["Pico de muestra", report.sample_peak_dbfs, "dBFS"],
    ["RMS", report.rms_dbfs, "dBFS"],
    ["Crest factor", report.crest_db, "dB"],
    ["Loudness range", report.loudness.lra_lu, "LU"],
    ["Correlación", report.stereo.correlation, ""],
    ["Ancho estéreo", report.stereo.width_pct, "%"],
    ["Pérdida mono", report.stereo.mono_delta_db, "dB"]
  ];
}

export default function Home() {
  const [file, setFile] = useState<File>();
  const [current, setCurrent] = useState<Entry>();
  const [compare, setCompare] = useState<Entry>();
  const [items, setItems] = useState<Item[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");

  const refresh = async () => setItems((await request<{ items: Item[] }>(path)).items);
  useEffect(() => { refresh().catch(() => setError("No se pudo conectar con la API local.")); }, []);

  async function upload(event: FormEvent) {
    event.preventDefault();
    if (!file || busy) return;
    setError("");
    setStatus("");
    if (file.size > 80 * 1024 * 1024) { setError("El archivo supera 80 MiB."); return; }
    setBusy(true);
    try {
      const entry = await request<Entry>(path, {
        method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file
      });
      setCurrent(entry);
      setCompare(undefined);
      setStatus("Reporte generado.");
      await refresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function open(id: string, comparison = false) {
    setError("");
    setBusy(true);
    try {
      const entry = id ? await request<Entry>(path + "/" + id) : undefined;
      if (comparison) setCompare(entry);
      else { setCurrent(entry); setCompare(undefined); }
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  async function remove() {
    if (!current || !window.confirm("¿Eliminar este reporte local?")) return;
    setError("");
    setBusy(true);
    try {
      await request(path + "/" + current.id, { method: "DELETE" });
      setCurrent(undefined);
      setCompare(undefined);
      setStatus("Reporte eliminado.");
      await refresh();
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  const compatible = current && compare && current.report.engine === compare.report.engine;
  return <main className="mx-auto max-w-5xl p-5 sm:p-10">
    <header><span className="eyebrow">MIXAI / PILOTO LOCAL</span><h1>Laboratorio de mezcla</h1>
      <p>Mide loudness, balance, dinámica y estéreo antes de exportar.</p></header>
    <section>
      <h2>Analizar audio</h2>
      <p>WAV PCM, MP3 o FLAC · mono o estéreo · 44,1–96 kHz · 0,5–300 s · hasta 80 MiB</p>
      <form onSubmit={upload}>
        <label htmlFor="audio">Archivo de mezcla</label>
        <input id="audio" type="file" accept=".wav,.mp3,.flac" onChange={e => setFile(e.target.files?.[0])} required disabled={busy}/>
        <button type="submit" disabled={busy || !file}>Analizar mezcla</button>
      </form>
      <p role="status">{busy ? "Procesando solicitud…" : status}</p>
      {error && <p role="alert" className="error">{error}</p>}
    </section>
    <section>
      <h2>Historial</h2>
      <label htmlFor="history">Reporte</label>
      <select id="history" value={current?.id ?? ""} onChange={e => void open(e.target.value)} disabled={busy}>
        <option value="">Seleccionar</option>
        {items.map(item => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString()} · {item.format.toUpperCase()} · {fmt(item.duration_s)} s</option>)}
      </select>
      {current && <>
        <label htmlFor="compare">Comparar con</label>
        <select id="compare" value={compare?.id ?? ""} onChange={e => void open(e.target.value, true)} disabled={busy}>
          <option value="">Sin comparación</option>
          {items.filter(i => i.id !== current.id).map(i => <option key={i.id} value={i.id}>{new Date(i.created_at).toLocaleString()} · {i.format.toUpperCase()} · {fmt(i.duration_s)} s</option>)}
        </select>
      </>}
    </section>
    {current && <section>
      <h2>Mediciones</h2>
      <p>{current.report.engine} · {fmt(current.report.duration_s)} s · {current.report.stereo.mode}</p>
      {current.report.stereo.mono_cancellation && <p className="error">La suma a mono cancela prácticamente toda la señal.</p>}
      {compare && !compatible && <p>Las mediciones usan versiones distintas del motor.</p>}
      <div className="overflow-x-auto">
        <table><thead><tr><th scope="col">Métrica</th><th scope="col">Actual</th>{compatible && <th scope="col">Comparada</th>}</tr></thead>
          <tbody>{[...metrics(current.report), ...Object.entries(current.report.bands_pct).map(([key, value]): [string, Value, string] => [bandNames[key] ?? key, value, "%"])].map(([label, value, unit], index) => {
            const other = compare ? [...metrics(compare.report), ...Object.keys(current.report.bands_pct).map(key => [key, compare.report.bands_pct[key] ?? null, "%"] as [string, Value, string])][index][1] : null;
            return <tr key={label}><th scope="row">{label}</th><td>{fmt(value)} {value !== null && unit}</td>
              {compatible && <td>{fmt(other)} {other !== null && unit}</td>}</tr>;
          })}</tbody>
        </table>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Trace title="Espectro medio · 20 Hz a 20 kHz (eje logarítmico)" yUnit="dBFS por banda" ticks={[20, 100, 1000, 10000, 20000].map(hz => ({ x: Math.log10(hz), label: hz >= 1000 ? hz / 1000 + " kHz" : hz + " Hz" }))} series={current.report.spectrum.map(p => ({ x: Math.log10(p.hz), y: p.dbfs }))}/>
        <Trace title="Loudness short-term · tiempo" yUnit="LUFS" ticks={[0, current.report.duration_s / 2, current.report.duration_s].map(s => ({ x: s, label: fmt(s) + " s" }))} series={current.report.loudness.short_term.map(p => ({ x: p.end_s, y: p.lufs }))}/>
      </div>
      <h3>Observaciones de la señal</h3>
      {current.report.notes.length ? <ul>{current.report.notes.map(note => <li key={note.code}>{note.text}</li>)}</ul> : <p>Sin alertas de las reglas activas.</p>}
      <button type="button" onClick={() => void remove()} disabled={busy}>Eliminar reporte</button>
    </section>}
  </main>;
}
