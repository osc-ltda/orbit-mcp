"""Prueba por el protocolo MCP real (cliente en memoria): inyección de Context y elicitation."""
import asyncio

import httpx
from fastmcp import Client
from test_admin import URL, FakeOrbits

from orbits_mcp import server
from orbits_mcp.client import OrbitsClient


def test_tools_via_mcp_protocol_with_and_without_elicitation(monkeypatch):
    fake = FakeOrbits()
    monkeypatch.setattr(server, "_api", OrbitsClient(url=URL, pat="orb_test", transport=httpx.MockTransport(fake)))
    preguntas = []

    async def acepta(message, response_type, params, context):
        preguntas.append(message)
        return {"confirmar": True}

    async def main():
        async with Client(server.mcp) as sin_elicit:  # cliente sin elicitation: token en dos pasos
            r = await sin_elicit.call_tool("set_user_password", {"user_id": 10, "new_password": "clave-1"})
            assert r.structured_content["status"] == "confirmation_required"
            assert fake.writes == []
            token = r.structured_content["confirmToken"]
            r = await sin_elicit.call_tool("set_user_password", {"user_id": 10, "new_password": "clave-1", "confirm_token": token})
            assert r.structured_content["ok"] is True
        async with Client(server.mcp, elicitation_handler=acepta) as con_elicit:
            r = await con_elicit.call_tool("set_user_password", {"user_id": 10, "new_password": "clave-2"})
            assert r.structured_content["ok"] is True

    asyncio.run(main())
    assert len(preguntas) == 1 and "repite@alumnos.utalca.cl" in preguntas[0]
    assert [p for _, p, _ in fake.writes] == ["/admin/users/10/password"] * 2


def test_irreversible_tools_are_annotated_destructive():
    async def main():
        async with Client(server.mcp) as c:
            return {t.name: t.annotations for t in await c.list_tools()}

    ann = asyncio.run(main())
    for name in ["create_users", "set_user_password", "delete_tag", "delete_course", "retroactive_awards_execute"]:
        assert ann[name].destructive_hint is True, name
    for name in ["find_users", "get_tag", "list_tags", "course_access", "retroactive_awards_preview"]:
        assert ann[name].read_only_hint is True, name


def test_user_declining_in_the_client_writes_nothing(monkeypatch):
    from fastmcp.client.elicitation import ElicitResult

    fake = FakeOrbits()
    monkeypatch.setattr(server, "_api", OrbitsClient(url=URL, pat="orb_test", transport=httpx.MockTransport(fake)))

    async def rechaza(message, response_type, params, context):
        return ElicitResult(action="decline")

    async def main():
        async with Client(server.mcp, elicitation_handler=rechaza) as c:
            return (await c.call_tool("delete_tag", {"tag_id": 30})).structured_content

    assert asyncio.run(main())["status"] == "cancelled"
    assert fake.writes == [] and 30 in fake.tags
