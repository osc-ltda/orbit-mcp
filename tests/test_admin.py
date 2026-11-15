import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from fastmcp.exceptions import ToolError

from orbits_mcp import admin, server
from orbits_mcp.client import OrbitsClient

URL = "http://orbits.test/api/v1"


def jwt(role: str, sub: int = 3) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub, "role": role}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


class FakeOrbits:
    """API mínima en memoria: usuarios, tags, cursos y asociaciones. Registra cada escritura."""

    def __init__(self, role: str = "ADMIN"):
        self.role = role
        self.users = {
            10: {"eduserId": 10, "email": "repite@alumnos.utalca.cl", "role": "STUDENT", "fullName": "Ana Repite", "tags": []},
            3: {"eduserId": 3, "email": "admin@utest.cl", "role": "ADMIN", "fullName": None, "tags": []},
        }
        self.tags = {21: {"tag_id": 21, "name": "Demo formatos", "description": "d"}, 30: {"tag_id": 30, "name": "Vacía", "description": None}}
        self.courses = {73: {"course_id": 73, "name": "Demo formatos (interno)", "description": "x"}}
        self.tag_students = {(21, 10)}
        self.tag_courses = {(21, 73)}
        self.writes: list[tuple[str, str, dict | None]] = []
        self.next_id = 100

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.removeprefix("/api/v1")
        body = json.loads(req.content) if req.content else None
        if path == "/auth/token":
            return httpx.Response(200, json={"token": jwt(self.role), "expiresIn": 3600})
        if req.method != "GET":
            self.writes.append((req.method, path, body))
        m = req.method
        if m == "GET" and path == "/users":
            return httpx.Response(200, json={"users": list(self.users.values()), "total": len(self.users)})
        if m == "GET" and path.startswith("/user/"):
            u = self.users.get(int(path.split("/")[2]))
            return httpx.Response(200, json=u) if u else httpx.Response(404, json={"errorId": "not_found"})
        if m == "POST" and path == "/user":
            if any(u["email"] == body["email"] for u in self.users.values()):
                return httpx.Response(409, json={"errorId": "duplicate"})
            self.next_id += 1
            self.users[self.next_id] = {"eduserId": self.next_id, "email": body["email"], "role": body["role"],
                                        "fullName": None, "tags": []}
            return httpx.Response(200, json={"message": "created"})
        if m == "POST" and path.startswith("/admin/users/") and path.endswith("/password"):
            return httpx.Response(204)
        if m == "GET" and path.startswith("/tag/") and path.count("/") == 2:
            t = self.tags.get(int(path.split("/")[2]))
            return httpx.Response(200, json=t) if t else httpx.Response(404, json={"errorId": "not_found"})
        if m == "GET" and path.endswith("/courses") and path.startswith("/tag/"):
            tid = int(path.split("/")[2])
            return httpx.Response(200, json={"courses": [self.courses[c] for t, c in self.tag_courses if t == tid]})
        if m == "GET" and path.endswith("/users") and path.startswith("/tag/"):
            tid = int(path.split("/")[2])
            return httpx.Response(200, json={"users": [self.users[s] for t, s in self.tag_students if t == tid]})
        if m == "GET" and path.endswith("/monitors"):
            return httpx.Response(200, json={"users": []})
        if path == "/tag/student":
            key = (body["tag_id"], body["student_id"])
            if m == "POST":
                if key in self.tag_students:
                    return httpx.Response(409, json={"errorId": "duplicate"})
                self.tag_students.add(key)
            else:
                self.tag_students.discard(key)
            return httpx.Response(200, json={})
        if path == "/tag/course" and m == "DELETE":
            self.tag_courses.discard((body["tag_id"], body["course_id"]))
            return httpx.Response(200, json={})
        if m == "DELETE" and path.startswith("/tag/"):
            tid = int(path.split("/")[2])
            if any(t == tid for t, _ in self.tag_students | self.tag_courses):
                return httpx.Response(409, json={"errorId": "tag_in_use"})
            self.tags.pop(tid)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"errorId": "unhandled", "message": f"{m} {path}"})


@pytest.fixture
def fake(monkeypatch):
    f = FakeOrbits()
    monkeypatch.setattr(server, "_api", OrbitsClient(url=URL, pat="orb_test", transport=httpx.MockTransport(f)))
    return f


def run(coro):
    return asyncio.run(coro)


def ctx_with_elicitation(answer: bool | None):
    """Contexto falso cuyo cliente soporta elicitation y responde accept/decline."""

    async def elicit(message, response_type):
        elicit.messages.append(message)
        if answer is None:
            return SimpleNamespace(action="decline", data=None)
        return SimpleNamespace(action="accept", data=answer)

    elicit.messages = []
    caps = SimpleNamespace(elicitation=object())
    return SimpleNamespace(session=SimpleNamespace(client_capabilities=caps), elicit=elicit)


NO_ELICIT = SimpleNamespace(session=SimpleNamespace(client_capabilities=SimpleNamespace(elicitation=None)))

NUEVOS = [
    {"email": "Nuevo1@alumnos.utalca.cl", "password": "22652795", "first_name": "Uno", "last_name": "N"},
    {"email": "repite@alumnos.utalca.cl", "password": "NO-DEBE-USARSE"},
]


def test_non_admin_session_is_rejected_before_any_write(monkeypatch):
    f = FakeOrbits(role="STAFF")
    monkeypatch.setattr(server, "_api", OrbitsClient(url=URL, pat="orb_test", transport=httpx.MockTransport(f)))
    with pytest.raises(ToolError, match="requiere rol ADMIN"):
        run(admin.create_tag("x"))
    assert f.writes == []


def test_create_users_asks_confirmation_and_writes_nothing_until_confirmed(fake):
    res = run(admin.create_users(NUEVOS, tag_id=21, ctx=NO_ELICIT))
    assert res["status"] == "confirmation_required"
    assert "nuevo1@alumnos.utalca.cl" in res["summary"] and "1 ya existen" in res["summary"]
    assert "22652795" not in json.dumps(res)  # las claves no se devuelven
    assert fake.writes == []

    done = run(admin.create_users(NUEVOS, tag_id=21, confirm_token=res["confirmToken"], ctx=NO_ELICIT))
    assert done["ok"] and done["created"] == ["nuevo1@alumnos.utalca.cl"]
    assert done["alreadyExisted"] == ["repite@alumnos.utalca.cl"]
    assert done["addedToTag"] == ["nuevo1@alumnos.utalca.cl"] and done["alreadyInTag"] == ["repite@alumnos.utalca.cl"]
    posted = [b for m, p, b in fake.writes if p == "/user"]
    assert len(posted) == 1 and posted[0]["password"] == "22652795"  # al repitente no se le toca la clave


def test_confirm_token_is_bound_to_the_exact_arguments(fake):
    res = run(admin.create_users(NUEVOS, tag_id=21, ctx=NO_ELICIT))
    with pytest.raises(ToolError, match="confirm_token inválido"):
        run(admin.create_users(NUEVOS, tag_id=30, confirm_token=res["confirmToken"], ctx=NO_ELICIT))
    with pytest.raises(ToolError, match="confirm_token inválido"):
        run(admin.create_users(NUEVOS, tag_id=21, confirm_token="9999999999.falso", ctx=NO_ELICIT))
    assert fake.writes == []


def test_confirm_token_expires():
    token = admin.make_confirm_token(3, "x", {"a": 1}, now=1000)
    assert admin.check_confirm_token(token, 3, "x", {"a": 1}, now=1000 + admin.CONFIRM_TTL - 1)
    assert not admin.check_confirm_token(token, 3, "x", {"a": 1}, now=1000 + admin.CONFIRM_TTL + 1)
    assert not admin.check_confirm_token(token, 4, "x", {"a": 1}, now=1000)  # otro usuario


def test_existing_users_only_need_no_confirmation(fake):
    res = run(admin.create_users([NUEVOS[1]], tag_id=30, ctx=NO_ELICIT))
    assert res["ok"] and res["created"] == [] and res["addedToTag"] == ["repite@alumnos.utalca.cl"]
    assert [p for _, p, _ in fake.writes] == ["/tag/student"]


def test_set_password_token_does_not_authorize_a_different_password(fake):
    res = run(admin.set_user_password(10, "clave-1", ctx=NO_ELICIT))
    assert res["status"] == "confirmation_required" and "repite@alumnos.utalca.cl" in res["summary"]
    with pytest.raises(ToolError):
        run(admin.set_user_password(10, "otra-clave", confirm_token=res["confirmToken"], ctx=NO_ELICIT))
    ok = run(admin.set_user_password(10, "clave-1", confirm_token=res["confirmToken"], ctx=NO_ELICIT))
    assert ok["ok"] and "clave-1" not in json.dumps(ok)
    assert fake.writes == [("POST", "/admin/users/10/password", {"new_password": "clave-1"})]


def test_elicitation_accept_executes_and_decline_does_nothing(fake):
    declined = run(admin.set_user_password(10, "clave-1", ctx=ctx_with_elicitation(None)))
    assert declined["status"] == "cancelled" and fake.writes == []
    ctx = ctx_with_elicitation(True)
    ok = run(admin.set_user_password(10, "clave-1", ctx=ctx))
    assert ok["ok"] and "NO se puede deshacer" in ctx.elicit.messages[0]
    assert len(fake.writes) == 1


def test_delete_tag_with_associations_requires_explicit_flag_then_confirmation(fake):
    blocked = run(admin.delete_tag(21, ctx=NO_ELICIT))
    assert blocked["ok"] is False and blocked["students"] == 1 and fake.writes == []

    res = run(admin.delete_tag(21, remove_associations=True, ctx=NO_ELICIT))
    assert res["status"] == "confirmation_required" and "1 alumno(s)" in res["summary"]
    assert fake.writes == []

    done = run(admin.delete_tag(21, remove_associations=True, confirm_token=res["confirmToken"], ctx=NO_ELICIT))
    assert done["ok"] and done["removedStudents"] == [10] and done["removedCourses"] == [73]
    assert 21 not in fake.tags
    assert fake.writes[-1] == ("DELETE", "/tag/21", None)


def test_reversible_removal_runs_directly_and_reports_undo(fake):
    res = run(admin.remove_students_from_tag(21, [10, 10]))
    assert res["ok"] and res["done"] == [10] and res["undo"] == "add_students_to_tag"
    assert (21, 10) not in fake.tag_students
