"""Call an administrator-configured provider model through ModelDock; no provider key needed."""

import os
import time
from pathlib import Path

import httpx

base_url = os.environ.get("MODELDock_BASE_URL", "http://localhost:8000")
with httpx.Client(
    base_url=base_url, headers={"Authorization": "Bearer " + os.environ["MODELDock_API_KEY"]}, timeout=30
) as client:
    with Path(os.environ["MODELDock_INPUT_IMAGE"]).open("rb") as image:
        response = client.post("/v1/files", files={"file": ("input.png", image, "image/png")})
    response.raise_for_status()
    response = client.post(
        "/v1/tasks",
        json={
            "model": os.environ["MODELDock_VIDEO_MODEL"],
            "capability": "video",
            "parameters": {
                "operation": "image_to_video",
                "image_file_id": response.json()["id"],
                "prompt": "Slow camera movement",
            },
        },
    )
    response.raise_for_status()
    task = response.json()
    while task["status"] in {"queued", "running"}:
        time.sleep(2)
        response = client.get("/v1/tasks/" + task["id"])
        response.raise_for_status()
        task = response.json()
    print({"id": task["id"], "status": task["status"], "error": task["error"]})
    # Do not automatically retry: a retry can incur another upstream charge.
