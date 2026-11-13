import asyncio
import base64
import io
import json
import zipfile

import httpx

from orbits_mcp import server
from orbits_mcp.client import OrbitsClient, build_bundle, credentials_path, poll_device_login

URL = "http://orbits.test/api/v1"


def fake_jwt(n: int) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "7", "n": n}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


def client_with(handler) -> OrbitsClient:
    return OrbitsClient(url=URL, pat="orb_test", transport=httpx.MockTransport(handler))


def test_build_bundle_puts_test_json_at_root_and_images_at_bundle_paths(tmp_path):
    img = tmp_path / "local.png"
    img.write_bytes(b"\x89PNG fake")
    data = build_bundle({"formatVersion": 1, "name": "Ñandú"}, {"images/q1.png": str(img)})
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert sorted(z.namelist()) == ["images/q1.png", "test.json"]
        assert json.loads(z.read("test.json")) == {"formatVersion": 1, "name": "Ñandú"}
        assert z.read("images/q1.png") == b"\x89PNG fake"


def test_expired_jwt_is_refreshed_once_on_401():
    exchanges = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/auth/token"):
            assert json.loads(req.content) == {"token": "orb_test"}
            exchanges.append(1)
            return httpx.Response(200, json={"token": fake_jwt(len(exchanges)), "expiresIn": 3600})
        if req.headers["Authorization"] == f"Bearer {fake_jwt(1)}":
            return httpx.Response(401, json={"errorId": "unauthorized"})
        return httpx.Response(200, json={"courses": [], "total": 0})

    api = client_with(handler)
    assert asyncio.run(api.call("GET", "/courses")) == {"courses": [], "total": 0}
    assert len(exchanges) == 2


def test_import_validation_problems_are_returned_as_result(monkeypatch):
    problems = [{"path": "questionSets[0].questions", "message": "debe tener al menos 1 elemento"}]
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/auth/token"):
            return httpx.Response(200, json={"token": fake_jwt(1), "expiresIn": 3600})
        seen["path"], seen["body"] = req.url.path, req.read()
        return httpx.Response(400, json=problems)

    monkeypatch.setattr(server, "_api", client_with(handler))
    result = asyncio.run(server.import_test(5, {"formatVersion": 1, "name": "x", "questionSets": []}))
    assert result == {"ok": False, "problems": problems}
    assert seen["path"] == "/api/v1/test/import"
    assert b'name="courseId"\r\n\r\n5' in seen["body"]
    assert b'name="file"; filename="test.zip"' in seen["body"]


def test_device_login_polls_until_authorized_and_saves_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    token_info = {"id": 3, "name": "orbits-mcp@pc", "prefix": "orb_NewToken", "token": "orb_NewTokenXYZ",
                  "createdAt": "2026-11-13T00:00:00Z", "expiresAt": None}
    replies = iter([httpx.Response(400, json={"errorId": "authorization_pending"}), httpx.Response(200, json=token_info)])

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/auth/device/token"
        assert json.loads(req.content) == {"deviceCode": "dc-1"}
        return next(replies)

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    api = client_with(handler)
    flow = {"deviceCode": "dc-1", "interval": 5, "expiresIn": 600}
    assert asyncio.run(poll_device_login(api, flow, sleep=fake_sleep)) == token_info
    assert sleeps == [5, 5]
    assert api.pat == "orb_NewTokenXYZ"
    assert json.loads(credentials_path().read_text()) == {
        "url": URL, "token": "orb_NewTokenXYZ", "tokenId": 3, "expiresAt": None,
    }
    assert credentials_path().stat().st_mode & 0o777 == 0o600
