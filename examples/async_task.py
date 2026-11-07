"""Submit a durable task and download its result with authorization."""

import os
import time
from pathlib import Path

import httpx

with httpx.Client(
    base_url=os.getenv("MODELDock_BASE_URL", "http://localhost:8000"),
    headers={"Authorization": "Bearer " + os.environ["MODELDock_API_KEY"]},
    timeout=120,
) as client:
    response = client.post(
        "/v1/tasks",
        json={
            "model": "mock-image",
            "capability": "image",
            "parameters": {"prompt": "a violet dock at night"},
        },
    )
    response.raise_for_status()
    task = response.json()
    while task["status"] in {"queued", "running"}:
        time.sleep(2)
        response = client.get(f"/v1/tasks/{task['id']}")
        response.raise_for_status()
        task = response.json()
    if task["status"] != "succeeded":
        raise RuntimeError(task["error"])
    for file in task["result"]["files"]:
        response = client.get(file["url"])
        response.raise_for_status()
        Path(file["filename"]).write_bytes(response.content)
        print("Downloaded:", file["filename"])
