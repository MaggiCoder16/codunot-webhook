@app.post("/webhook")
async def deapi_webhook(req: Request):
    payload = await req.json()

    print("🔥 WEBHOOK RECEIVED:", payload)

    event_type = payload.get("event")

    # Support both structures
    data = payload.get("data") or payload

    request_id = (
        data.get("job_request_id")
        or data.get("request_id")
        or payload.get("request_id")
    )

    if event_type != "job.completed" or not request_id:
        print("⚠️ Ignored event:", event_type, request_id)
        return {"status": "ack"}

    print("✅ Processing completed job:", request_id)

    result_url = data.get("result_url")

    if result_url:
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get(result_url, timeout=60)
                if r.status_code == 200:
                    data["transcription"] = r.text
        except Exception as e:
            print("❌ Error fetching result_url:", e)

    RESULTS[request_id] = {
        "status": "done",
        "data": data,
        "result_url": result_url,
    }

    channel_id = PENDING_TRANSCRIPTIONS.pop(request_id, None)

    print("Channel ID found:", channel_id)

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

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    url,
                    headers=headers,
                    json={
                        "content": f"✅ **Transcription complete:**\n{transcript[:2000]}"
                    },
                )
                print("Discord response status:", resp.status_code)
        except Exception as e:
            print("❌ Error sending Discord message:", e)

    return {"status": "ok"}
