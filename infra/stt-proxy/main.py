import os
import json
import subprocess
import tempfile
import logging
from fastapi import FastAPI, Request, Response, HTTPException
from tinfoil import AsyncTinfoilAI

app = FastAPI(title="STT Proxy")
logger = logging.getLogger("stt-proxy")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

TINFOIL_API_KEY = os.environ.get("TINFOIL_API_KEY", "")
VOXTRAL_MODEL = os.environ.get("VOXTRAL_MODEL", "voxtral-small-24b")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "whisper-large-v3-turbo")
DURATION_THRESHOLD = float(os.environ.get("DURATION_THRESHOLD", "1800"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "60"))

SUPPORTED_FORMATS = ("mp3", "mov", "mp4", "m4a")

tinfoil_client = AsyncTinfoilAI(api_key=TINFOIL_API_KEY, timeout=REQUEST_TIMEOUT)


def get_audio_info(file_path: str) -> tuple[float | None, str | None]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet",
             "-show_entries", "format=duration,format_name",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            fmt = data.get("format", {})
            duration = float(fmt["duration"]) if "duration" in fmt else None
            format_name = fmt.get("format_name")
            return duration, format_name
        return None, None
    except Exception:
        return None, None


def needs_conversion(format_name: str | None) -> bool:
    if format_name is None:
        return True
    fmt_lower = format_name.lower()
    return not any(f in fmt_lower for f in SUPPORTED_FORMATS)


def convert_to_mp3(input_path: str, output_path: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", input_path,
             "-codec:a", "libmp3lame", "-b:a", "128k",
             "-nostdin", "-nostats", "-loglevel", "error",
             "-y", output_path],
            capture_output=True, text=True, timeout=300,
        )
        return result.returncode == 0
    except Exception:
        return False


def select_model(duration: float | None) -> str:
    if duration is None:
        return WHISPER_MODEL
    if duration < DURATION_THRESHOLD:
        return VOXTRAL_MODEL
    return WHISPER_MODEL


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def root():
    return {
        "service": "stt-proxy",
        "voxtral_model": VOXTRAL_MODEL,
        "whisper_model": WHISPER_MODEL,
        "duration_threshold": DURATION_THRESHOLD,
        "request_timeout": REQUEST_TIMEOUT,
    }


@app.get("/v1/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": VOXTRAL_MODEL, "object": "model", "created": 0, "owned_by": "tinfoil"},
            {"id": WHISPER_MODEL, "object": "model", "created": 0, "owned_by": "tinfoil"},
        ],
    }


@app.api_route("/v1/models/{model_name}", methods=["POST", "GET"])
async def model_info(model_name: str):
    return {"status": "loaded", "model": model_name}


@app.post("/v1/audio/transcriptions")
@app.post("/audio/transcriptions")
async def transcribe(request: Request):
    temp_files: list[str] = []
    try:
        form = await request.form()

        file = None
        extra_data = {}
        for key, value in form.multi_items():
            if hasattr(value, "read") or hasattr(value, "filename"):
                file = value
            else:
                extra_data[key] = str(value)

        if file is None:
            raise HTTPException(status_code=422, detail="No file field found in form data")

        extra_data.pop("model", None)
        await file.seek(0)

        suffix = os.path.splitext(file.filename or "audio.m4a")[1] or ".m4a"

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            while chunk := await file.read(1024 * 1024):
                tmp.write(chunk)
            tmp_path = tmp.name
        temp_files.append(tmp_path)

        file_size = os.path.getsize(tmp_path)
        if file_size == 0:
            raise HTTPException(status_code=422, detail="Uploaded file is empty (0 bytes)")

        duration, format_name = get_audio_info(tmp_path)

        forward_path = tmp_path
        forward_name = file.filename or "audio.m4a"
        forward_ct = file.content_type or "application/octet-stream"

        if needs_conversion(format_name):
            mp3_path = tmp_path + ".mp3"
            if convert_to_mp3(tmp_path, mp3_path):
                forward_path = mp3_path
                base_name = (file.filename or "audio").rsplit(".", 1)[0]
                forward_name = base_name + ".mp3"
                forward_ct = "audio/mpeg"
                temp_files.append(mp3_path)

        selected_model = select_model(duration)

        # Voxtral no soporta verbose_json. Strip para Voxtral.
        # Whisper tampoco — devolvemos solo text para evitar Pydantic warnings.
        sdk_kwargs = {"model": selected_model}
        for key in ("language", "prompt", "temperature"):
            if key in extra_data:
                sdk_kwargs[key] = extra_data[key]
        # NO pasar response_format a ningún modelo.

        logger.info(f"Forwarding via SDK: model={selected_model}, file={forward_name}, "
                     f"size={os.path.getsize(forward_path)}B")

        try:
            with open(forward_path, "rb") as f:
                transcription = await tinfoil_client.audio.transcriptions.create(
                    file=(forward_name, f, forward_ct),
                    **sdk_kwargs,
                )
        except Exception as primary_error:
            if selected_model == VOXTRAL_MODEL:
                err_status = getattr(primary_error, 'status_code', None) or 502
                logger.warning(f"Voxtral failed (status={err_status}): {primary_error}. Falling back to Whisper.")

                fallback_kwargs = {"model": WHISPER_MODEL}
                for key in ("language", "prompt", "temperature"):
                    if key in extra_data:
                        fallback_kwargs[key] = extra_data[key]

                logger.info(f"Forwarding via SDK (fallback): model={WHISPER_MODEL}, file={forward_name}, "
                             f"size={os.path.getsize(forward_path)}B")

                with open(forward_path, "rb") as f:
                    transcription = await tinfoil_client.audio.transcriptions.create(
                        file=(forward_name, f, forward_ct),
                        **fallback_kwargs,
                    )
                logger.info(f"Whisper fallback succeeded: text_len={len(getattr(transcription, 'text', '') or '')}")
            else:
                raise

        # Serialización simple: siempre devolver {"text": "..."}
        text = getattr(transcription, 'text', '') or ''
        content = json.dumps({"text": text})

        logger.info(f"Tinfoil response: status=200, text_len={len(text)}")

        return Response(content=content, status_code=200, media_type="application/json")

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        if "timeout" in error_msg.lower():
            logger.error(f"Timeout after {REQUEST_TIMEOUT:.0f}s")
            raise HTTPException(status_code=504, detail=f"Tinfoil timeout after {REQUEST_TIMEOUT:.0f}s")
        status_code = getattr(e, 'status_code', None) or 502
        logger.error(f"SDK error: status={status_code}, {error_msg}")
        raise HTTPException(status_code=status_code, detail=f"Tinfoil error: {error_msg}")
    finally:
        for f in temp_files:
            try:
                os.unlink(f)
            except OSError:
                pass

