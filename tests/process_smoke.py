"""Real PostgreSQL/Redis smoke test with API and Runtime in separate OS processes.

Run only against the dedicated, empty modeldock_process test database.
"""

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="modeldock-process-") as scratch:
        env = {
            **os.environ,
            "MODELDock_DATABASE_URL": os.environ["PROCESS_DATABASE_URL"],
            "MODELDock_REDIS_URL": os.environ["PROCESS_REDIS_URL"],
            "MODELDock_DATA_DIR": scratch,
            "MODELDock_ADMIN_USERNAME": "process-admin",
            "MODELDock_ADMIN_PASSWORD": secrets.token_urlsafe(24),
            "MODELDock_PORT": "8011",
            "MODELDock_WORKER_LEASE_SECONDS": "4",
        }
        subprocess.run([str(root / ".venv/bin/alembic"), "upgrade", "head"], env=env, cwd=root, check=True)
        subprocess.run([str(root / ".venv/bin/modeldock-init")], env=env, cwd=root, check=True)
        processes = []
        output = open(Path(scratch) / "service.log", "w")

        def launch(command):
            process = subprocess.Popen(
                [str(root / ".venv/bin" / command)], cwd=root, env=env, stdout=output, stderr=output
            )
            processes.append(process)
            return process

        def ready(client):
            for _ in range(100):
                try:
                    if client.get("/health/ready").status_code == 200:
                        return
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            raise AssertionError("Services did not become ready")

        try:
            worker = launch("modeldock-worker")
            launch("modeldock-api")
            with httpx.Client(base_url="http://127.0.0.1:8011", timeout=15, trust_env=False) as client:
                ready(client)
                response = client.post(
                    "/api/v1/auth/login",
                    json={"username": "process-admin", "password": env["MODELDock_ADMIN_PASSWORD"]},
                )
                response.raise_for_status()
                client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
                assert client.get("/").headers["content-type"].startswith("text/html")
                assert client.get("/swagger/swagger-ui-bundle.js").status_code == 200
                schema = client.get("/openapi.json").json()
                assert "TaskOutput" in schema["components"]["schemas"]
                chat = client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "mock-chat",
                        "messages": [{"role": "user", "content": "separate processes"}],
                        "stream": True,
                    },
                )
                chat.raise_for_status()
                assert "[DONE]" in chat.text
                worker.kill()
                worker.wait(timeout=5)
                queued = client.post(
                    "/v1/tasks",
                    json={
                        "model": "mock-image",
                        "capability": "image",
                        "parameters": {"prompt": "recover after kill"},
                    },
                )
                queued.raise_for_status()
                task_id = queued.json()["id"]
                time.sleep(4.5)
                launch("modeldock-worker")
                ready(client)
                for _ in range(100):
                    task = client.get("/v1/tasks/" + task_id).json()
                    if task["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.1)
                assert task["status"] == "succeeded", task
                file = task["result"]["files"][0]
                assert client.get(file["url"]).content.startswith(b"\x89PNG")
                print(
                    "PASS: separate API/Worker, offline Swagger, SSE, abrupt Worker restart and queued recovery"
                )
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
            output.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(type(error).__name__, str(error), file=sys.stderr)
        raise
