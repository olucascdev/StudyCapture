from __future__ import annotations

import io
import struct
import wave

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def wav_to_pcm(data: bytes) -> bytes:
    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != SAMPLE_RATE:
            raise ValueError("WAV deve ser mono PCM 16-bit a 16 kHz")
        return wav.readframes(wav.getnframes())


def wav_duration_samples(data: bytes) -> int:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getnframes()


def pcm_silence(samples: int) -> bytes:
    return struct.pack("<h", 0) * samples
