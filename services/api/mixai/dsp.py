"""Bounded offline analysis for mono and stereo WAV, MP3 and FLAC."""
import json
import math
import re
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from scipy.signal import welch

MAX_BYTES = 80 * 1024 * 1024
MAX_SECONDS = 300
ENGINE = "mixai-dsp/0.1.0"
BANDS = (("subgrave", 20, 60), ("grave", 60, 250), ("medio", 250, 2000),
         ("medio_alto", 2000, 8000), ("aire", 8000, 20000))
FORMATS = {"wav": "wav", "mp3": "mp3", "flac": "flac"}


class AudioError(ValueError):
    pass


def run_audio(command, *, timeout=45):
    try:
        return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AudioError("El archivo excedió el tiempo de procesamiento.") from exc
    except subprocess.CalledProcessError as exc:
        raise AudioError("El archivo no se pudo decodificar.") from exc


def probe(path):
    output = run_audio([
        "ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", "wav,mp3,flac",
        "-show_entries", "stream=codec_type,codec_name,channels,sample_rate:format=format_name,duration",
        "-of", "json", str(path),
    ], timeout=10)
    try:
        metadata = json.loads(output.stdout)
        stream, = [s for s in metadata["streams"] if s["codec_type"] == "audio"]
        container = metadata["format"]["format_name"]
        codec = stream["codec_name"]
        rate, channels = int(stream["sample_rate"]), int(stream["channels"])
        duration = float(metadata["format"].get("duration", "nan"))
    except (KeyError, ValueError, TypeError) as exc:
        raise AudioError("El archivo requiere una pista de audio reconocible.") from exc
    valid_codec = codec.startswith("pcm_") if container == "wav" else codec == FORMATS.get(container)
    if container not in FORMATS or not valid_codec:
        raise AudioError("Formato admitido: WAV PCM, MP3 o FLAC.")
    if channels not in (1, 2) or not 44100 <= rate <= 96000:
        raise AudioError("Usa audio mono o estéreo entre 44.1 y 96 kHz.")
    # Encoders such as MP3 can add a small padding to container duration.
    if math.isfinite(duration) and duration > MAX_SECONDS + 0.25:
        raise AudioError("La duración máxima es de 5 minutos.")
    return container, rate, channels


def db(value, multiplier=20):
    return round(multiplier * math.log10(value), 3) if value > 0 else None


def measure_loudness(pcm, rate, channels, seconds, sample_peak):
    raw_input = ["-f", "f32le", "-ar", str(rate), "-ac", str(channels)]
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-v", "info", *raw_input, "-i", str(pcm),
           "-af", "ebur128=metadata=1:peak=true,ametadata=mode=print:file=-",
           "-f", "null", "-"]
    frames = []
    current = None
    result = run_audio(cmd)
    for line in result.stdout.decode().splitlines():
        if line.startswith("frame:"):
            current = {"start": float(line.split("pts_time:")[1])}
            frames.append(current)
        elif current is not None and line.startswith("lavfi.r128."):
            key, value = line.removeprefix("lavfi.r128.").split("=", 1)
            current[key] = float(value)
    if not frames or "true_peak" not in frames[-1] or "I" not in frames[-1]:
        raise RuntimeError("FFmpeg no generó metadatos de loudness.")
    final = frames[-1]
    # EBU filter uses -70 as its integrated sentinel when no block passes its gate.
    integrated = final["I"] if seconds >= 0.4 and final["I"] > -70 else None
    # FFmpeg's final summary preserves dB precision at quiet levels and includes
    # the final partial frame. Exact digital silence has no finite peak in dB.
    match = re.search(r"True peak:\s+Peak:\s+(-?[0-9]+(?:\.[0-9]+)?) dBFS", result.stderr.decode())
    if sample_peak and not match:
        raise RuntimeError("FFmpeg no generó el resumen de true peak.")
    peak = float(match.group(1)) if sample_peak else None
    short_term = [
        {"end_s": round(frame["start"] + 0.1, 1), "lufs": frame["S"] if frame["S"] > -120 else None}
        for index, frame in enumerate(frames)
        if index >= 29 and (index - 29) % 10 == 0
    ]
    return {
        "integrated_lufs": integrated,
        "true_peak_dbtp": peak,
        "short_term": short_term,
        # Product guardrail for unstable estimates on very short tracks.
        "lra_lu": final.get("LRA") if integrated is not None and seconds >= 10 else None,
    }


def spectral_balance(pcm, rate):
    n = min(16384, len(pcm))
    accumulated = None
    frames = 0
    hop = n - n // 2
    batch = 128
    for first in range(0, (len(pcm) - n) // hop + 1, batch):
        last = min(first + batch - 1, (len(pcm) - n) // hop)
        chunk = pcm[first * hop:last * hop + n]
        freqs, psd = welch(chunk, fs=rate, nperseg=n, noverlap=n // 2, axis=0)
        count = last - first + 1
        power = psd.mean(axis=1) * count
        accumulated = power if accumulated is None else accumulated + power
        frames += count
    psd = accumulated / frames
    total = float(psd[(freqs >= 20) & (freqs <= 20000)].sum())
    bands = {}
    for name, low, high in BANDS:
        ceiling = freqs <= high if high == 20000 else freqs < high
        bands[name] = round(100 * float(psd[(freqs >= low) & ceiling].sum()) / total, 4) if total else None
    edges = np.geomspace(20, 20000, 81)
    spectrum = []
    for index, (low, high) in enumerate(zip(edges[:-1], edges[1:])):
        ceiling = freqs <= high if index == len(edges) - 2 else freqs < high
        values = psd[(freqs >= low) & ceiling]
        power = float(values.sum()) * rate / n
        spectrum.append({"hz": round(math.sqrt(low * high), 2),
                         "dbfs": db(power, 10) if values.size else None})
    return bands, spectrum


def stereo_metrics(pcm, rate):
    if pcm.shape[1] == 1:
        return {"mode": "mono", "correlation": None, "width_pct": None, "mono_delta_db": None}
    count = 0
    sums = np.zeros(2)
    products = np.zeros((2, 2))
    for index in range(0, len(pcm), rate * 5):
        chunk = np.asarray(pcm[index:index + rate * 5], dtype=np.float64)
        count += len(chunk)
        sums += chunk.sum(axis=0)
        products += chunk.T @ chunk
    left, right = products.diagonal()
    cross = products[0, 1]
    total = left + right
    covariance = products - np.outer(sums, sums) / count
    variance_product = float(covariance[0, 0] * covariance[1, 1])
    correlation = covariance[0, 1] / math.sqrt(variance_product) if variance_product > 0 else None
    return {
        "mode": "stereo",
        "correlation": round(float(np.clip(correlation, -1, 1)), 4) if correlation is not None else None,
        "width_pct": round(100 * float(np.clip((total - 2 * cross) / (2 * total), 0, 1)), 3) if total else None,
        "mono_delta_db": db((total + 2 * cross) / (2 * total), 10) if total else None,
        "mono_cancellation": bool(total and total + 2 * cross <= total * 1e-12),
    }


def analyze(source: Path):
    if not 0 < source.stat().st_size <= MAX_BYTES:
        raise AudioError("El archivo debe pesar entre 1 byte y 80 MiB.")
    container, rate, channels = probe(source)
    with tempfile.TemporaryDirectory(prefix="mixai-pcm-") as directory:
        return analyze_pcm(source, Path(directory) / "decoded.f32", container, rate, channels)


def analyze_pcm(source, pcm_path, container, rate, channels):
    run_audio([
        "ffmpeg", "-nostdin", "-v", "error", "-xerror", "-threads", "1",
        "-protocol_whitelist", "file", "-format_whitelist", "wav,mp3,flac", "-i", str(source), "-map", "0:a:0",
        "-map_metadata", "-1", "-t", str(MAX_SECONDS + 1),
        "-c:a", "pcm_f32le", "-f", "f32le", "-y", str(pcm_path),
    ])
    samples, remainder = divmod(pcm_path.stat().st_size, 4 * channels)
    if remainder or not rate // 2 <= samples <= rate * MAX_SECONDS:
        raise AudioError("La duración decodificada debe estar entre 0.5 y 300 segundos.")
    pcm = np.memmap(pcm_path, dtype="<f4", mode="r", shape=(samples, channels))
    peak = energy = 0.0
    for index in range(0, samples, rate * 5):
        chunk = np.asarray(pcm[index:index + rate * 5], dtype=np.float64)
        if not np.isfinite(chunk).all() or np.abs(chunk).max() > 64:
            raise AudioError("La señal contiene muestras fuera del rango admitido.")
        peak = max(peak, float(np.abs(chunk).max()))
        energy += float(np.square(chunk).sum())
    seconds = samples / rate
    loudness = measure_loudness(pcm_path, rate, channels, seconds, peak)
    rms = math.sqrt(energy / (samples * channels))
    stereo = stereo_metrics(pcm, rate)
    bands, spectrum = spectral_balance(pcm, rate)
    notes = []
    if peak == 0:
        notes.append({"code": "silence", "text": "El archivo contiene silencio digital."})
    if (loudness["true_peak_dbtp"] or 0) > 0:
        notes.append({"code": "true_peak_above_zero", "text": "Hay picos verdaderos por encima de 0 dBTP. Revisa el techo del limitador y vuelve a medir."})
    if stereo.get("correlation") is not None and stereo["correlation"] < -0.2:
        notes.append({"code": "negative_correlation", "text": "La correlación global es negativa. Escucha en mono y comprueba los efectos de apertura estéreo."})
    return {
        "engine": ENGINE, "format": container, "sample_rate_hz": rate, "channels": channels,
        "duration_s": round(seconds, 3), "loudness": loudness,
        "sample_peak_dbfs": db(peak), "rms_dbfs": db(rms),
        "crest_db": db(peak / rms) if rms else None,
        "stereo": stereo, "bands_pct": bands, "spectrum": spectrum, "notes": notes,
    }
