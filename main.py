from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import uvicorn
import json
import time
import hmac
import hashlib
import httpx
from pathlib import Path

app = FastAPI()

RESULTS = {}
PENDING_TRANSCRIPTIONS: dict[str, int] = {}
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()

VOTE_FILE = Path("topgg_votes.json")
VOTE_DURATION_SECONDS = 60 * 60 * 12


def load_votes():
    if not VOTE_FILE.exists():
        return {}
    try:
        with VOTE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_votes(data):
    with VOTE_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f)


async def send_discord_message(channel_id: int, content: str):
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }
    chunks = []
    while content:
        if len(content) <= 2000:
            chunks.append(content)
            break
        split_at = content.rfind("\n", 0, 2000)
        if split_at <= 0:
            split_at = content.rfind(" ", 0, 2000)
        if split_at <= 0:
            split_at = 2000
        chunks.append(content[:split_at])
        content = content[split_at:].lstrip()

    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            await client.post(url, headers=headers, json={"content": chunk})


@app.post("/register-transcription")
async def register_transcription(req: Request):
    body = await req.json()
    request_id = body.get("request_id")
    channel_id = body.get("channel_id")
    if request_id and channel_id:
        PENDING_TRANSCRIPTIONS[request_id] = int(channel_id)
        print(f"[Register] request_id={request_id} → channel_id={channel_id}")
    return {"status": "ok"}


@app.post("/webhook")
async def deapi_webhook(req: Request):
    payload = await req.json()
    event_type = payload.get("event", "unknown")
    data = payload.get("data", {})
    request_id = data.get("job_request_id")
    job_type = data.get("job_type", "")

    print(f"[Webhook] {event_type} | job_type={job_type} | request_id={request_id}")

    if event_type == "job.processing":
        return JSONResponse(status_code=200, content={"status": "ack"})

    if event_type == "job.completed":
        if request_id:
            if job_type == "vid2txt":
                result_url = data.get("result_url")
                if result_url:
                    try:
                        async with httpx.AsyncClient() as client:
                            r = await client.get(result_url, timeout=30)
                            if r.status_code == 200:
                                data["transcription"] = r.text
                                print(f"[Webhook] Transcript downloaded for {request_id}")
                    except Exception as e:
                        print(f"[Webhook] Failed to download transcript: {e}")

            RESULTS[request_id] = data
            print(f"[Webhook] Completed: {request_id}")

            channel_id = PENDING_TRANSCRIPTIONS.pop(request_id, None)
            if channel_id and DISCORD_BOT_TOKEN:
                transcript = data.get("transcription") or data.get("transcript") or data.get("text")
                if transcript:
                    await send_discord_message(channel_id, f"✅ **Transcription complete:**\n{transcript}")
                else:
                    await send_discord_message(channel_id, "⚠️ Transcription completed but returned empty text.")

        return JSONResponse(status_code=200, content={"status": "ok"})

    if event_type == "job.failed":
        if request_id:
            channel_id = PENDING_TRANSCRIPTIONS.pop(request_id, None)
            if channel_id and DISCORD_BOT_TOKEN:
                await send_discord_message(channel_id, "❌ Transcription failed. Please try again.")
        return JSONResponse(status_code=200, content={"status": "ok"})

    return JSONResponse(status_code=200, content={"status": "ack"})


@app.post("/topgg-webhook")
async def topgg_webhook(req: Request):
    secret = os.getenv("TOPGG_WEBHOOK_AUTH")
    signature_header = req.headers.get("x-topgg-signature")
    if not secret:
        return JSONResponse(status_code=500, content={"error": "Webhook secret not configured"})
    if not signature_header:
        return JSONResponse(status_code=401, content={"error": "Missing signature header"})
    try:
        parts = dict(item.split("=") for item in signature_header.split(","))
        timestamp = parts.get("t")
        signature = parts.get("v1")
    except Exception:
        return JSONResponse(status_code=400, content={"error": "Invalid signature format"})
    if not timestamp or not signature:
        return JSONResponse(status_code=400, content={"error": "Malformed signature header"})

    raw_body = await req.body()
    message = f"{timestamp}.".encode() + raw_body
    expected_signature = hmac.new(
        secret.encode(),
        message,
        hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return JSONResponse(status_code=401, content={"error": "Invalid signature"})

    payload = json.loads(raw_body)
    event_type = payload.get("type")
    if event_type == "vote.create":
        user_id = payload["data"]["user"]["platform_id"]
        votes = load_votes()
        votes[str(user_id)] = int(time.time() + VOTE_DURATION_SECONDS)
        save_votes(votes)
        print(f"[Top.gg] Vote received for user {user_id}")
    elif event_type == "webhook.test":
        print("[Top.gg] Webhook test received")

    return {"status": "ok"}


@app.get("/result/{request_id}")
async def get_result(request_id: str):
    if request_id in RESULTS:
        return {"status": "done", "data": RESULTS[request_id]}
    return {"status": "pending"}


@app.get("/")
async def root():
    return {"status": "running"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
