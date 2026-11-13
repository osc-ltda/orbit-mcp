"""Credenciales locales y cliente HTTP de la API de Orbits (PAT -> JWT)."""

import asyncio
import base64
import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
from fastmcp.exceptions import ToolError

DEFAULT_URL = "https://prod.orbits.cl/api/v1"
LOGIN_HINT = "Llama la tool `login` (o el usuario puede correr `orbits-mcp login`)."


class ApiError(ToolError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def credentials_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "orbits" / "credentials.json"


def load_credentials() -> dict:
    path = credentials_path()
    return json.loads(path.read_text()) if path.exists() else {}


def save_credentials(data: dict) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)  # os.open no cambia el modo de un archivo ya existente


def raise_for_api(r: httpx.Response) -> None:
    if r.is_success:
        return
    try:
        body = r.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        detail = " - ".join(str(v) for v in (body.get("errorId"), body.get("message")) if v)
    else:
        detail = r.text[:500]
    raise ApiError(
        f"Orbits API {r.status_code} en {r.request.method} {r.request.url.path}: {detail or r.reason_phrase}",
        r.status_code,
    )


def build_bundle(test: dict, image_paths: dict[str, str] | None = None, images: dict[str, str] | None = None) -> bytes:
    """ZIP en memoria: test.json en la raíz + imágenes (ruta en el bundle -> archivo local, o -> contenido base64)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("test.json", json.dumps(test, ensure_ascii=False, indent=2))
        for bundle_path, local_path in (image_paths or {}).items():
            local = Path(local_path).expanduser()
            if not local.is_file():
                raise ToolError(f"No existe la imagen local '{local}' (para '{bundle_path}')")
            z.write(local, bundle_path)
        for bundle_path, content in (images or {}).items():
            try:
                z.writestr(bundle_path, base64.b64decode(content, validate=True))
            except ValueError as e:
                raise ToolError(f"La imagen '{bundle_path}' no es base64 válido") from e
    return buf.getvalue()


def jwt_claims(jwt: str) -> dict:
    payload = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


class OrbitsClient:
    def __init__(self, url: str | None = None, pat: str | None = None, transport: httpx.AsyncBaseTransport | None = None):
        creds = load_credentials()
        self.url = (url or os.environ.get("ORBITS_URL") or creds.get("url") or DEFAULT_URL).rstrip("/")
        self.pat = pat or os.environ.get("ORBITS_TOKEN") or creds.get("token")
        self.http = httpx.AsyncClient(base_url=self.url, timeout=120, transport=transport)
        self.jwt: str | None = None
        self.jwt_expires = 0.0

    async def authenticate(self) -> str:
        if self.jwt and self.jwt_expires - time.time() > 60:
            return self.jwt
        if not self.pat:
            raise ApiError(f"No hay sesión de Orbits. {LOGIN_HINT}")
        r = await self.http.post("/auth/token", json={"token": self.pat})
        if r.status_code == 401:
            raise ApiError(f"El token de Orbits es inválido o expiró. {LOGIN_HINT}", 401)
        raise_for_api(r)
        body = r.json()
        self.jwt, self.jwt_expires = body["token"], time.time() + body["expiresIn"]
        return self.jwt

    async def request(self, method: str, path: str, params: dict | None = None, **kw) -> httpx.Response:
        """Request autenticado; ante un 401 renueva el JWT y reintenta una vez."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for retry in (False, True):
            headers = {"Authorization": f"Bearer {await self.authenticate()}"}
            r = await self.http.request(method, path, params=params, headers=headers, **kw)
            if r.status_code != 401 or retry:
                return r
            self.jwt = None

    async def call(self, method: str, path: str, params: dict | None = None, **kw):
        r = await self.request(method, path, params, **kw)
        raise_for_api(r)
        return r.json() if r.content else None


async def start_device_login(api: OrbitsClient) -> dict:
    """Inicia el device flow (RFC 8628) e intenta abrir el navegador en la URL de verificación."""
    r = await api.http.post("/auth/device", json={"name": f"orbits-mcp@{socket.gethostname()}"[:100]})
    raise_for_api(r)
    flow = r.json()
    # En un proceso aparte con stdout a /dev/null: el navegador escribe en stdout y rompería el stdio MCP.
    subprocess.Popen(
        [sys.executable, "-m", "webbrowser", "-t", flow["verificationUriComplete"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    return flow


async def poll_device_login(api: OrbitsClient, flow: dict, sleep=asyncio.sleep) -> dict:
    """Espera la autorización del usuario y guarda el token resultante."""
    interval = flow["interval"]
    deadline = time.monotonic() + flow["expiresIn"]
    while time.monotonic() < deadline:
        await sleep(interval)
        r = await api.http.post("/auth/device/token", json={"deviceCode": flow["deviceCode"]})
        if r.status_code == 200:
            info = r.json()
            save_credentials({"url": api.url, "token": info["token"], "tokenId": info["id"], "expiresAt": info["expiresAt"]})
            api.pat, api.jwt = info["token"], None
            return info
        if r.status_code == 429:
            interval *= 2
        elif r.status_code == 401:
            break
        elif not (r.status_code == 400 and r.json().get("errorId") == "authorization_pending"):
            raise_for_api(r)
    raise ApiError("El código de inicio de sesión expiró sin ser autorizado. Vuelve a iniciar sesión.", 401)
