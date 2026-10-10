"""Our own acoustic analysis: decode the recording, then track pitch and loudness.

No model is involved here. The same audio bytes always give the same numbers.
"""
from __future__ import annotations

import shutil
import subprocess
import warnings
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
HOP_SECONDS = 0.010
WINDOW_SECONDS = 0.040


@dataclass
class VoiceTrack:
    times: np.ndarray        # centre time of each frame, seconds
    f0: np.ndarray           # pitch in Hz, NaN where unvoiced
    semitones: np.ndarray    # pitch relative to the speaker's own median, NaN where unvoiced
    loudness_db: np.ndarray  # frame level in dB
    voiced: np.ndarray       # bool
    median_f0: float
    duration: float


def decode(path: str) -> np.ndarray:
    """Any browser/phone recording -> mono 16 kHz float samples."""
    if shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path),
               "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
        raw = subprocess.run(cmd, capture_output=True, check=True).stdout
        return np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    # Fallback without ffmpeg: WAV files only.
    from scipy.io import wavfile
    from scipy.signal import resample_poly
    sr, data = wavfile.read(path)
    data = data.astype(np.float64)
    if data.ndim > 1:
        data = data.mean(axis=1)
    peak = np.abs(data).max() or 1.0
    data = data / (32768.0 if peak > 1.5 else 1.0)
    if sr != SAMPLE_RATE:
        data = resample_poly(data, SAMPLE_RATE, sr)
    return data


def track_voice(samples: np.ndarray, floor_hz: float = 70, ceiling_hz: float = 400) -> VoiceTrack:
    """Autocorrelation pitch tracker (Boersma-style window correction) plus loudness."""
    sr = SAMPLE_RATE
    n, hop = int(WINDOW_SECONDS * sr), int(HOP_SECONDS * sr)
    duration = len(samples) / sr
    if len(samples) < n:
        empty = np.array([])
        return VoiceTrack(empty, empty, empty, empty, empty.astype(bool), float("nan"), duration)

    count = 1 + (len(samples) - n) // hop
    index = np.arange(n)[None, :] + hop * np.arange(count)[:, None]
    frames = samples[index]
    frames = frames - frames.mean(axis=1, keepdims=True)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    loudness = 20 * np.log10(np.maximum(rms, 1e-10))

    window = np.hanning(n)
    nfft = 1 << int(np.ceil(np.log2(2 * n)))

    def autocorr(block: np.ndarray) -> np.ndarray:
        spectrum = np.fft.rfft(block, nfft, axis=-1)
        return np.fft.irfft(np.abs(spectrum) ** 2, nfft, axis=-1)[..., :n]

    r = autocorr(frames * window)
    rw = autocorr(window[None, :])
    nacf = (r / np.maximum(r[:, :1], 1e-12)) / (rw / rw[:, :1])

    lo = max(2, int(sr / ceiling_hz))
    hi = min(int(sr / floor_hz), n // 2)
    seg = nacf[:, lo:hi + 1]
    best = seg.max(axis=1)

    # Choose the SHORTEST lag that is a local peak close to the best one. Picking the
    # global maximum instead produces octave-too-low errors.
    is_peak = np.zeros_like(seg, dtype=bool)
    is_peak[:, 1:-1] = (seg[:, 1:-1] > seg[:, :-2]) & (seg[:, 1:-1] >= seg[:, 2:])
    candidate = is_peak & (seg >= 0.92 * best[:, None])
    choice = np.where(candidate.any(axis=1), candidate.argmax(axis=1), seg.argmax(axis=1))

    # Parabolic interpolation around the chosen lag for sub-sample precision.
    rows = np.arange(count)
    inner = np.clip(choice, 1, seg.shape[1] - 2)
    a, b, c = seg[rows, inner - 1], seg[rows, inner], seg[rows, inner + 1]
    denom = a - 2 * b + c
    shift = np.where(np.abs(denom) > 1e-12, 0.5 * (a - c) / np.where(denom == 0, 1, denom), 0.0)
    lag = lo + inner + np.clip(shift, -1, 1)
    f0 = sr / lag
    strength = seg[rows, choice]

    voiced = (strength >= 0.5) & (loudness > loudness.max() - 35)
    f0 = np.where(voiced, f0, np.nan)

    # 5-frame median smoothing over voiced neighbours.
    padded = np.pad(f0, 2, constant_values=np.nan)
    stacked = np.stack([padded[i:i + count] for i in range(5)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)    # all-NaN columns are expected
        smoothed = np.nanmedian(stacked, axis=0)
    f0 = np.where(voiced, smoothed, np.nan)

    median = float(np.nanmedian(f0)) if voiced.any() else float("nan")
    with np.errstate(all="ignore"):
        semitones = 12 * np.log2(f0 / median)
    # Anything more than an octave from the speaker's median is a tracking error.
    with np.errstate(invalid="ignore"):
        voiced = voiced & ~(np.abs(semitones) > 12)
    f0 = np.where(voiced, f0, np.nan)
    semitones = np.where(voiced, semitones, np.nan)

    times = (np.arange(count) * hop + n / 2) / sr
    return VoiceTrack(times, f0, semitones, loudness, voiced, median, duration)
