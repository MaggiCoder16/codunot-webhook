from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
import httpx

app = FastAPI()

RESULTS = {}
PENDING_TRANSCRIPTIONS = {}

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

@app.post("/register-transcription")
async def register_transcription(req: Request):
    body = await req.json()
    request_id = body.get("request_id")
    channel_id = body.get("channel_id")

    if request_id and channel_id:
        PENDING_TRANSCRIPTIONS[request_id] = int(channel_id)

    return {"status": "ok"}

@app.post("/webhook")
async def deapi_webhook(req: Request):
    payload = await req.json()
    event_type = payload.get("event")
    data = payload.get("data", {})
    request_id = data.get("job_request_id")
    job_type = data.get("job_type")

    if event_type != "job.completed" or not request_id:
        return {"status": "ack"}

    if job_type == "vid2txt":
        result_url = data.get("result_url")
        if result_url:
            async with httpx.AsyncClient() as client:
                r = await client.get(result_url)
                if r.status_code == 200:
                    data["transcription"] = r.text

    RESULTS[request_id] = {
        "status": "done",
        "data": data,
        "result_url": data.get("result_url"),
    }

    channel_id = PENDING_TRANSCRIPTIONS.pop(request_id, None)

    if channel_id and DISCORD_BOT_TOKEN:
        transcript = (
            data.get("transcription")
            or data.get("text")
            or "Transcription completed."
        )

        url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
        headers = {
            "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient() as client:
            await client.post(
                url,
                headers=headers,
                json={"content": f"✅ **Transcription complete:**\n{transcript[:2000]}"},
            )

    return {"status": "ok"}

@app.get("/result/{request_id}")
async def get_result(request_id: str):
    result = RESULTS.get(request_id)

    if not result:
        return {"status": "pending"}

    return result


@app.get("/")
async def root():
    return {"status": "running"}
