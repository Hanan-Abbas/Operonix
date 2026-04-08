import asyncio
import io
import os
import wave
import logging
from typing import Optional, Tuple

import numpy as np
import aiohttp

from core.config import settings


logger = logging.getLogger("CloudSTT")


def _float32_to_wav_bytes(audio_float32: np.ndarray, sample_rate: int) -> bytes:
    audio = np.asarray(audio_float32, dtype=np.float32)
    audio = np.clip(audio, -1.0, 1.0)
    int16 = (audio * 32767.0).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(int(sample_rate))
        wf.writeframes(int16.tobytes())
    return buf.getvalue()


async def _openai_transcribe_wav_bytes(wav_bytes: bytes) -> Optional[str]:
    api_key = getattr(settings, "OPENAI_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        return None

    base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    model = getattr(settings, "OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")

    url = f"{base_url}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}

    form = aiohttp.FormData()
    form.add_field("model", model)
    form.add_field("response_format", "json")
    form.add_field(
        "file",
        wav_bytes,
        filename="audio.wav",
        content_type="audio/wav",
    )

    timeout_s = float(getattr(settings, "CLOUD_STT_TIMEOUT_SECONDS", 15))
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, data=form) as resp:
            if resp.status != 200:
                body = await resp.text()
                logger.warning("OpenAI STT failed (%s): %s", resp.status, body[:300])
                return None
            data = await resp.json()
            text = (data.get("text") or "").strip()
            return text or None


def transcribe_audio_hybrid(
    audio_float32: np.ndarray,
    sample_rate: int,
    local_text: str,
    local_meta: dict,
    local_confidence: float,
) -> Tuple[str, dict, str]:
    """
    If configured for hybrid mode and confidence is low, call cloud STT.
    Returns (text, meta, provider).
    """
    provider_mode = getattr(settings, "STT_PROVIDER", "local")
    min_conf = float(getattr(settings, "STT_MIN_CONFIDENCE", 0.45))

    if provider_mode not in ("hybrid", "cloud"):
        return local_text, local_meta, "local"

    if provider_mode == "hybrid" and local_confidence >= min_conf:
        return local_text, local_meta, "local"

    cloud_provider = getattr(settings, "CLOUD_STT_PROVIDER", "openai")
    if cloud_provider != "openai":
        return local_text, local_meta, "local"

    wav_bytes = _float32_to_wav_bytes(audio_float32, sample_rate)
    try:
        cloud_text = asyncio.run(_openai_transcribe_wav_bytes(wav_bytes))
    except Exception as e:
        logger.warning("Cloud STT exception: %s", e)
        cloud_text = None

    if cloud_text:
        meta = dict(local_meta or {})
        meta["cloud_fallback_used"] = True
        meta["local_confidence"] = local_confidence
        return cloud_text, meta, "openai"

    meta = dict(local_meta or {})
    meta["cloud_fallback_used"] = False
    meta["cloud_fallback_failed"] = True
    meta["local_confidence"] = local_confidence
    return local_text, meta, "local"

