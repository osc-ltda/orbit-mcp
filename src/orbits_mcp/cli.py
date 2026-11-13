"""CLI `orbits-mcp`: servidor MCP (stdio) y login/logout/whoami."""

import argparse
import asyncio
import os
import sys

import httpx

from .client import (
    DEFAULT_URL,
    ApiError,
    OrbitsClient,
    credentials_path,
    jwt_claims,
    load_credentials,
    poll_device_login,
    raise_for_api,
    save_credentials,
    start_device_login,
)


async def _device_login(url: str) -> dict:
    client = OrbitsClient(url=url)
    flow = await start_device_login(client)
    print(f"Abriendo el navegador… Si no se abre, entra a {flow['verificationUri']} e ingresa el código {flow['userCode']}")
    print("Esperando autorización (Ctrl-C para cancelar)…")
    return await poll_device_login(client, flow)


def login(url: str | None, token: str | None) -> None:
    url = (url or os.environ.get("ORBITS_URL") or load_credentials().get("url") or DEFAULT_URL).rstrip("/")
    if token:
        with httpx.Client(base_url=url, timeout=30) as http:
            r = http.post("/auth/token", json={"token": token})
            if r.status_code == 401:
                raise ApiError("El token es inválido o expiró.")
            raise_for_api(r)
            r = http.get("/account/tokens", headers={"Authorization": f"Bearer {r.json()['token']}"})
            raise_for_api(r)
            info = next((t for t in r.json() if token.startswith(t["prefix"])), {})
        save_credentials({"url": url, "token": token, "tokenId": info.get("id"), "expiresAt": info.get("expiresAt")})
    else:
        info = asyncio.run(_device_login(url))
    print(f"Sesión guardada en {credentials_path()} ({url}). Expira: {info.get('expiresAt') or 'nunca'}")


def logout() -> None:
    creds = load_credentials()
    if not creds:
        print("No hay sesión guardada.")
        return
    if creds.get("tokenId") is not None:
        try:
            client = OrbitsClient(url=creds["url"], pat=creds["token"])
            r = asyncio.run(client.request("DELETE", f"/account/tokens/{creds['tokenId']}"))
            if r.status_code != 404:
                raise_for_api(r)
        except (ApiError, httpx.HTTPError) as e:
            print(f"Aviso: no se pudo revocar el token en el servidor: {e}", file=sys.stderr)
    credentials_path().unlink()
    print("Sesión cerrada.")


def whoami() -> None:
    client = OrbitsClient()
    claims = jwt_claims(asyncio.run(client.authenticate()))
    creds = load_credentials()
    if creds.get("token") == client.pat:
        expires = creds.get("expiresAt") or "nunca"
    else:
        expires = "desconocida (token desde ORBITS_TOKEN)"
    print(f"Usuario: {claims.get('firstName', '')} {claims.get('lastName', '')} <{claims.get('email')}> (id {claims['sub']})")
    print(f"Rol: {claims.get('role')}")
    print(f"URL: {client.url}")
    print(f"Token expira: {expires}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="orbits-mcp", description="Servidor MCP de Orbits (stdio) y gestión de credenciales.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="ejecuta el servidor MCP por stdio (por defecto)")
    p = sub.add_parser("login", help="inicia sesión en el navegador (o con un código) y guarda un token de acceso personal")
    p.add_argument("--url", help=f"URL base de la API (por defecto ORBITS_URL o {DEFAULT_URL})")
    p.add_argument("--token", help="token de acceso personal existente (orb_...), para máquinas sin navegador o CI")
    sub.add_parser("logout", help="revoca el token guardado y borra las credenciales locales")
    sub.add_parser("whoami", help="muestra usuario, rol, URL y expiración del token")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "login":
            login(args.url, args.token)
        elif args.cmd == "logout":
            logout()
        elif args.cmd == "whoami":
            whoami()
        else:
            from .server import mcp

            mcp.run()
    except (ApiError, httpx.HTTPError) as e:
        sys.exit(f"Error: {e}")
    except (KeyboardInterrupt, EOFError):
        sys.exit(1)
