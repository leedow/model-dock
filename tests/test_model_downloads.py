import asyncio
import hashlib
import time

import httpx
import pytest
from modeldock.entities import ModelDownload, User
from modeldock.errors import DockError
from modeldock.services.model_downloads import CATALOG, DownloadManager
from sqlalchemy import select


@pytest.fixture
def download_hub(monkeypatch):
    options = {"bad_hash": False, "redirect": False, "hold": False, "short": False}
    requests = []
    started = asyncio.Event()

    class BlockingBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b"never"

    def handler(request):
        requests.append(str(request.url))
        repo = next(preset for preset in CATALOG.values() if preset.repo in request.url.path)
        files = {name: ("fixture:" + name).encode() for name in (*repo.files, "README.md")}
        if "/api/models/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "sha": "a" * 40,
                    "siblings": [
                        {
                            "rfilename": name,
                            "size": len(content),
                            "blobId": hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest(),
                        }
                        for name, content in files.items()
                    ],
                },
            )
        if options["hold"]:
            return httpx.Response(200, stream=BlockingBody())
        if options["redirect"]:
            return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        name = request.url.path.split("/resolve/" + "a" * 40 + "/", 1)[1]
        content = files[name]
        if options["bad_hash"]:
            content = b"x" * len(content)
        if options["short"]:
            content = content[:-1]
        return httpx.Response(200, content=content)

    monkeypatch.setattr(
        DownloadManager,
        "client",
        lambda _: httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False),
    )
    return options, requests, started


async def wait_download(client, download_id):
    for _ in range(100):
        rows = (await client.get("/api/v1/model-downloads")).json()["data"]
        row = next(row for row in rows if row["id"] == download_id)
        if row["status"] not in {"queued", "running"}:
            return row
        await asyncio.sleep(0.05)
    raise AssertionError("Download did not finish")


@pytest.mark.parametrize("preset", list(CATALOG))
async def test_download_verified_snapshot_and_register(system, download_hub, tmp_path, preset):
    client, app, _ = system
    app.state.settings.models_dir = tmp_path / "weights"
    response = await client.post("/api/v1/model-downloads", json={"preset": preset})
    assert response.status_code == 202, response.text
    row = await wait_download(client, response.json()["id"])
    assert row["status"] == "succeeded", row
    assert row["downloaded_bytes"] == row["total_bytes"] > 0
    target = app.state.settings.models_dir / CATALOG[preset].config["model_path"]
    assert (target / ".modeldock-manifest.json").is_file()
    assert not (app.state.settings.models_dir / ".downloads" / row["id"]).exists()
    for name in CATALOG[preset].files:
        assert (target / name).read_bytes() == ("fixture:" + name).encode()
    registered = await client.post(f"/api/v1/model-downloads/{row['id']}/register")
    assert registered.status_code == 201, registered.text
    model = registered.json()
    assert model["enabled"] is False and model["device"] == "cpu"
    assert model["adapter"] == CATALOG[preset].adapter
    assert (await client.post(f"/api/v1/model-downloads/{row['id']}/register")).status_code == 409


@pytest.mark.parametrize(
    "mode,code",
    [
        ("bad_hash", "download_checksum_failed"),
        ("short", "download_checksum_failed"),
        ("redirect", "download_redirect_invalid"),
    ],
)
async def test_failed_download_cleans_partial_and_preserves_existing(
    system, download_hub, tmp_path, mode, code
):
    client, app, _ = system
    app.state.settings.models_dir = tmp_path / "weights"
    download_hub[0][mode] = True
    row = (await client.post("/api/v1/model-downloads", json={"preset": "sensevoice"})).json()
    result = await wait_download(client, row["id"])
    assert result["status"] == "failed" and result["error_code"] == code
    root = app.state.settings.models_dir
    assert not (root / ".downloads" / row["id"]).exists()
    target = root / CATALOG["sensevoice"].config["model_path"]
    assert not target.exists()
    target.mkdir(parents=True)
    (target / "keep.txt").write_text("user owned weights")
    again = (await client.post("/api/v1/model-downloads", json={"preset": "sensevoice"})).json()
    assert (await wait_download(client, again["id"]))["error_code"] == "model_path_exists"
    assert (target / "keep.txt").read_text() == "user owned weights"


async def test_cancel_duplicate_and_no_arbitrary_source(system, download_hub, tmp_path):
    client, app, _ = system
    app.state.settings.models_dir = tmp_path / "weights"
    download_hub[0]["hold"] = True
    response = await client.post("/api/v1/model-downloads", json={"preset": "kokoro"})
    download_id = response.json()["id"]
    await asyncio.wait_for(download_hub[2].wait(), 3)
    assert (await client.post("/api/v1/model-downloads", json={"preset": "kokoro"})).status_code == 409
    assert (
        await client.post("/api/v1/model-downloads", json={"preset": "kokoro", "url": "http://internal/"})
    ).status_code == 422
    assert (await client.post("/api/v1/model-downloads", json={"preset": "unknown"})).status_code == 422
    assert (await client.post(f"/api/v1/model-downloads/{download_id}/register")).status_code == 409
    assert (await client.post(f"/api/v1/model-downloads/{download_id}/cancel")).status_code == 202
    assert (await wait_download(client, download_id))["status"] == "cancelled"
    assert not (app.state.settings.models_dir / ".downloads" / download_id).exists()


async def test_download_management_requires_admin_session_and_owner(system, download_hub, tmp_path):
    client, app, _ = system
    app.state.settings.models_dir = tmp_path / "weights"
    download_hub[0]["hold"] = True
    row = (await client.post("/api/v1/model-downloads", json={"preset": "sensevoice"})).json()
    models = (await client.get("/api/v1/models")).json()["data"]
    key = (
        await client.post("/api/v1/keys", json={"name": "downloads", "model_ids": [models[0]["id"]]})
    ).json()
    assert (
        await client.get(
            "/api/v1/model-downloads/catalog", headers={"Authorization": "Bearer " + key["secret"]}
        )
    ).status_code == 403
    for username, role in (("download-admin", "admin"), ("download-user", "user")):
        assert (
            await client.post(
                "/api/v1/users",
                json={"username": username, "password": "download-password-123", "role": role},
            )
        ).status_code == 201
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as other:
            login = (
                await other.post(
                    "/api/v1/auth/login", json={"username": username, "password": "download-password-123"}
                )
            ).json()
            other.headers["x-csrf-token"] = login["csrf_token"]
            listing = await other.get("/api/v1/model-downloads")
            assert listing.status_code == (200 if role == "admin" else 403)
            if role == "admin":
                assert listing.json()["data"] == []
            assert (await other.post(f"/api/v1/model-downloads/{row['id']}/cancel")).status_code == (
                404 if role == "admin" else 403
            )
    await client.post(f"/api/v1/model-downloads/{row['id']}/cancel")


async def test_restart_marks_interrupted_download_failed(system, tmp_path):
    _, app, worker = system
    app.state.settings.models_dir = tmp_path / "weights"
    async with app.state.sessions() as session:
        user_id = await session.scalar(select(User.id).where(User.role == "admin"))
        row = ModelDownload(
            user_id=user_id,
            preset="kokoro",
            status="running",
            worker_id="lost-worker",
            created_at=time.time(),
        )
        session.add(row)
        await session.commit()
        download_id = row.id
    stopped = asyncio.Event()
    stopped.set()
    manager = DownloadManager(app.state.sessions, app.state.settings, worker.id, stopped)
    staging = app.state.settings.models_dir / ".downloads" / download_id
    staging.mkdir(parents=True)
    (staging / "incomplete.pt").write_bytes(b"partial")
    await manager.recover()
    assert not staging.exists()
    async with app.state.sessions() as session:
        row = await session.get(ModelDownload, download_id)
        assert row.status == "failed" and row.error_code == "worker_lost"


@pytest.mark.parametrize("cancelled", [False, True])
async def test_publication_checks_worker_ownership_and_cancellation(system, tmp_path, cancelled):
    _, app, worker = system
    async with app.state.sessions() as session:
        user_id = await session.scalar(select(User.id).where(User.role == "admin"))
        row = ModelDownload(
            user_id=user_id,
            preset="kokoro",
            status="running",
            worker_id=worker.id if cancelled else "replacement-worker",
            cancel_requested=cancelled,
        )
        session.add(row)
        await session.commit()
        download_id = row.id
    staging, target = tmp_path / "staging", tmp_path / "installed"
    staging.mkdir()
    (staging / "model.pt").write_bytes(b"verified fixture")
    manager = DownloadManager(app.state.sessions, app.state.settings, worker.id, asyncio.Event())
    if cancelled:
        await manager.publish(download_id, staging, target, 16)
        async with app.state.sessions() as session:
            row = await session.get(ModelDownload, download_id)
            assert row.status == "cancelled"
    else:
        with pytest.raises(DockError) as error:
            await manager.publish(download_id, staging, target, 16)
        assert error.value.error["code"] == "worker_lost"
    assert not target.exists()
