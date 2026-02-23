from fastapi import FastAPI, Request, Header
import os
import httpx
import hmac
import hashlib
import json

app = FastAPI()

RESULTS = {}
PENDING_TRANSCRIPTIONS = {}

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
WEBHOOK_SECRET = os.getenv("DEAPI_WEBHOOK_SECRET")


@app.post("/register-transcription")
async def register_transcription(req: Request):
    body = await req.json()
    request_id = body.get("request_id")
    channel_id = body.get("channel_id")

    if request_id and channel_id:
        PENDING_TRANSCRIPTIONS[request_id] = int(channel_id)
        print(f"[Register] {request_id} → {channel_id}")

    return {"status": "ok"}


@app.post("/webhook")
async def deapi_webhook(
    request: Request,
    x_deapi_signature: str = Header(None),
):
    raw_body = await request.body()

    if WEBHOOK_SECRET:
        timestamp = request.headers.get("x-deapi-timestamp", "")
        sig_header = x_deapi_signature or ""

        # remove sha256= prefix if present
        if sig_header.startswith("sha256="):
            sig_header = sig_header.replace("sha256=", "")

        signed_payload = f"{timestamp}.{raw_body.decode()}".encode()

        expected = hmac.new(
            WEBHOOK_SECRET.encode(),
            signed_payload,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, sig_header):
            print("❌ Invalid webhook signature")
            print("Received:", sig_header)
            print("Expected:", expected)
            return {"error": "invalid signature"}

    try:
        payload = json.loads(raw_body)
    except Exception as e:
        print("❌ JSON parse error:", e)
        return {"status": "error"}

    print("🔥 WEBHOOK RECEIVED:", payload)

    event_type = payload.get("event")
    data = payload.get("data") or payload

    request_id = (
        data.get("job_request_id")
        or data.get("request_id")
        or payload.get("request_id")
    )

    if not request_id:
        print("⚠️ No request_id found")
        return {"status": "ack"}

    if event_type != "job.completed":
        print("ℹ️ Ignoring event:", event_type)
        return {"status": "ack"}

    result_url = data.get("result_url")
    transcript_text = data.get("text") or data.get("transcription")

    if result_url and not transcript_text:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(result_url, timeout=60)
                if r.status_code == 200:
                    transcript_text = r.text
        except Exception as e:
            print("❌ Failed to fetch result_url:", e)

    RESULTS[request_id] = {
        "status": "done",
        "text": transcript_text,
        "raw": data,
    }

    channel_id = PENDING_TRANSCRIPTIONS.pop(request_id, None)

    if channel_id and DISCORD_BOT_TOKEN:
        message = transcript_text or "Transcription complete."

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={
                        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "content": f"✅ **Transcription complete:**\n{message[:1900]}"
                    },
                )
                print("Discord response:", resp.status_code)
        except Exception as e:
            print("❌ Discord send error:", e)

    return {"status": "ok"}


@app.get("/result/{request_id}")
async def get_result(request_id: str):
    return RESULTS.get(request_id, {"status": "pending"})


@app.get("/")
async def root():
    return {"status": "running"}
