import asyncio

from mcp.shared.auth import OAuthClientInformationFull

from orbits_mcp.remote import OrbitsOAuthProvider


def provider(secret: str = "s3cret") -> OrbitsOAuthProvider:
    return OrbitsOAuthProvider(base_url="https://mcp.test", api_url="https://api.test/api/v1", secret=secret)


def registered(p: OrbitsOAuthProvider, auth_method: str) -> OAuthClientInformationFull:
    info = OAuthClientInformationFull(
        client_id="random-uuid", client_secret=None if auth_method == "none" else "random-secret",
        client_name="Claude Code", redirect_uris=["http://localhost:5555/callback"], token_endpoint_auth_method=auth_method,
    )
    asyncio.run(p.register_client(info))
    return info


def test_client_ids_are_self_contained_signed_and_survive_restarts():
    info = registered(provider(), "none")
    again = asyncio.run(provider().get_client(info.client_id))  # otra instancia = reinicio del pod
    assert again.client_name == "Claude Code"
    assert [str(u) for u in again.redirect_uris] == ["http://localhost:5555/callback"]
    assert again.client_secret is None


def test_tampered_or_foreign_client_ids_are_rejected():
    info = registered(provider(), "none")
    payload, _, sig = info.client_id.partition(".")
    assert asyncio.run(provider().get_client(payload + "x." + sig)) is None
    assert asyncio.run(provider(secret="otro").get_client(info.client_id)) is None


def test_confidential_client_secret_is_derived_not_embedded_in_id():
    p = provider()
    info = registered(p, "client_secret_post")
    assert info.client_secret not in ("random-secret", None)
    assert info.client_secret not in info.client_id
    assert asyncio.run(p.get_client(info.client_id)).client_secret == info.client_secret
