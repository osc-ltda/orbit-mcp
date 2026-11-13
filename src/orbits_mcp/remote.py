"""Modo remoto: MCP por HTTP con OAuth 2.1 sobre el login por navegador de Orbits.

Flujo: el cliente MCP (Claude Code, Codex, omp) se registra (DCR) y abre /authorize en el
navegador. Este servidor inicia el device flow de la API y redirige a orbits.cl/dispositivo
con `return`; tras Autorizar, la web vuelve a /oauth/callback, donde se canjea el device code
por un PAT y se entrega un authorization code al cliente. El access token del cliente ES el PAT
(90 días): se valida contra la API en cada uso, así que revocarlo en la web corta el acceso.

Estado: los client_id son autocontenidos (metadata firmada con HMAC) para sobrevivir reinicios;
logins pendientes y authorization codes viven en memoria (minutos).
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.parse import quote

import httpx
from fastmcp.server.auth.auth import ClientRegistrationOptions, OAuthProvider
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from .client import ApiError, OrbitsClient

PENDING_TTL = 600  # igual que el device code de la API
CODE_TTL = 300
CALLBACK_PATH = "/oauth/callback"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


class OrbitsOAuthProvider(OAuthProvider):
    def __init__(self, *, base_url: str, api_url: str, secret: str):
        super().__init__(base_url=base_url, client_registration_options=ClientRegistrationOptions(enabled=True))
        self.api_url = api_url.rstrip("/")
        self.secret = secret.encode()
        self.http = httpx.AsyncClient(base_url=self.api_url, timeout=30)
        self.pending: dict[str, dict] = {}
        self.codes: dict[str, tuple[AuthorizationCode, str, str | None]] = {}  # code -> (code, PAT, expiresAt)
        # shortcut: un cliente por PAT sin límite; pasar a LRU si crecen mucho los usuarios simultáneos
        self.clients: dict[str, OrbitsClient] = {}

    def _sign(self, msg: str) -> str:
        return _b64(hmac.new(self.secret, msg.encode(), hashlib.sha256).digest())

    # --- Clientes (DCR sin estado) ---

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        meta = client_info.model_dump(
            mode="json", exclude_none=True,
            exclude={"client_id", "client_secret", "client_id_issued_at", "client_secret_expires_at"},
        )
        payload = _b64(json.dumps(meta, separators=(",", ":")).encode())
        # El handler del SDK responde con este mismo objeto: el id firmado y el secreto derivado reemplazan los aleatorios.
        client_info.client_id = f"{payload}.{self._sign(payload)}"
        if client_info.client_secret:
            client_info.client_secret = self._sign("secret:" + client_info.client_id)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        payload, _, sig = client_id.partition(".")
        if not sig or not hmac.compare_digest(sig, self._sign(payload)):
            return None
        meta = json.loads(_unb64(payload))
        secret = None if meta.get("token_endpoint_auth_method") == "none" else self._sign("secret:" + client_id)
        return OAuthClientInformationFull(**meta, client_id=client_id, client_secret=secret)

    # --- Autorización: delega en /auth/device + orbits.cl/dispositivo ---

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
        name = f"{client.client_name or 'Cliente MCP'} (MCP)"[:100]
        r = await self.http.post("/auth/device", json={"name": name})
        if not r.is_success:
            raise AuthorizeError(error="temporarily_unavailable", error_description=f"Orbits respondió {r.status_code}")
        flow = r.json()
        txn = secrets.token_urlsafe(32)
        self.pending[txn] = {"device_code": flow["deviceCode"], "client_id": client.client_id, "params": params, "expires": now + PENDING_TTL}
        back = f"{str(self.base_url).rstrip('/')}{CALLBACK_PATH}?txn={txn}"
        return f"{flow['verificationUriComplete']}&return={quote(back, safe='')}"

    async def callback(self, request: Request) -> Response:
        """La web vuelve aquí tras Autorizar (o Cancelar con error=access_denied)."""
        pending = self.pending.pop(request.query_params.get("txn", ""), None)
        if not pending or pending["expires"] < time.time():
            return HTMLResponse("<p>El inicio de sesión expiró. Vuelve a conectar desde tu agente.</p>", status_code=400)
        params: AuthorizationParams = pending["params"]
        redirect = str(params.redirect_uri)
        if request.query_params.get("error"):
            return RedirectResponse(construct_redirect_uri(redirect, error="access_denied", state=params.state), 302)
        r = await self.http.post("/auth/device/token", json={"deviceCode": pending["device_code"]})
        if not r.is_success:  # pendiente (no autorizó), expirado o API caída
            return RedirectResponse(construct_redirect_uri(redirect, error="access_denied", state=params.state), 302)
        pat = r.json()
        code = secrets.token_urlsafe(32)
        self.codes[code] = (
            AuthorizationCode(
                code=code, client_id=pending["client_id"], scopes=params.scopes or [], expires_at=time.time() + CODE_TTL,
                code_challenge=params.code_challenge, redirect_uri=params.redirect_uri,
                redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly, resource=params.resource,
            ),
            pat["token"],
            pat.get("expiresAt"),
        )
        return RedirectResponse(construct_redirect_uri(redirect, code=code, state=params.state), 302)

    async def load_authorization_code(self, client: OAuthClientInformationFull, authorization_code: str) -> AuthorizationCode | None:
        entry = self.codes.get(authorization_code)
        if not entry or entry[0].client_id != client.client_id or entry[0].expires_at < time.time():
            return None
        return entry[0]

    async def exchange_authorization_code(self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode) -> OAuthToken:
        entry = self.codes.pop(authorization_code.code, None)
        if not entry:
            raise TokenError("invalid_grant", "Código ya usado o expirado.")
        _, pat, expires_at = entry
        expires_in = None
        if expires_at:
            expires_in = max(0, int(_parse_iso(expires_at) - time.time()))
        return OAuthToken(access_token=pat, token_type="Bearer", expires_in=expires_in, scope=" ".join(authorization_code.scopes) or None)

    # Sin refresh tokens: al vencer el PAT (90 días) el cliente vuelve a pasar por el navegador.
    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return None

    async def exchange_refresh_token(self, client, refresh_token, scopes) -> OAuthToken:
        raise TokenError("invalid_grant", "Refresh tokens no soportados.")

    async def revoke_token(self, token) -> None:
        return None

    # --- Validación del access token (= PAT) ---

    def client_for(self, pat: str) -> OrbitsClient:
        if pat not in self.clients:
            self.clients[pat] = OrbitsClient(url=self.api_url, pat=pat)
        return self.clients[pat]

    async def load_access_token(self, token: str) -> AccessToken | None:
        if not token.startswith("orb_"):
            return None
        try:
            await self.client_for(token).authenticate()  # JWT en caché hasta 1 h; un PAT revocado deja de canjearse
        except ApiError:
            self.clients.pop(token, None)
            return None
        return AccessToken(token=token, client_id="orbits", scopes=[])


def _parse_iso(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def serve() -> None:
    from .server import mcp

    base_url = os.environ.get("MCP_PUBLIC_URL", "https://prod.orbits.cl")
    secret = os.environ["MCP_SECRET"]  # firma los client_id; cambiarlo invalida los clientes registrados
    provider = OrbitsOAuthProvider(base_url=base_url, api_url=os.environ.get("ORBITS_URL", "https://prod.orbits.cl/api/v1"), secret=secret)
    mcp.auth = provider
    mcp.disable(names={"login"})  # en remoto el login lo hace el cliente MCP vía OAuth
    mcp.custom_route(CALLBACK_PATH, methods=["GET"])(provider.callback)

    @mcp.custom_route("/mcp-health", methods=["GET"])
    async def health(_: Request) -> Response:
        return Response("ok")

    mcp.run(transport="http", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), path="/mcp")

