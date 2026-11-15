"""Tools de administración de Orbits: usuarios, tags (inscripción), cursos, roles, claves y edubits.

Solo funcionan con una sesión de rol ADMIN (la API además valida cada endpoint).

Acciones que no se pueden deshacer desde la API (crear cuentas, cambiar claves, borrar tags o
cursos, acreditar edubits) piden confirmación humana antes de ejecutar:

1. Si el cliente MCP soporta elicitation, el servidor le pregunta al usuario directamente.
2. Si no, la tool NO ejecuta: devuelve `status: confirmation_required` con un resumen y un
   `confirmToken` firmado (5 min, ligado al usuario, a la acción y a sus argumentos exactos).
   El agente debe mostrar el resumen, esperar el sí del usuario y recién entonces repetir la
   llamada con `confirm_token`.

Las acciones reversibles (asociar o quitar tags, roles de curso, editar nombres) se ejecutan
directo y devuelven lo necesario para revertirlas.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Literal

import mcp_types
from fastmcp import Context
from fastmcp.dependencies import CurrentContext
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from .client import ApiError, jwt_claims
from .server import api, mcp

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
REVERSIBLE_REMOVE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True)
IRREVERSIBLE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False)

CONFIRM_TTL = 300
_SECRET = (os.environ.get("MCP_SECRET") or "").encode() or os.urandom(32)

Role = Literal["ADMIN", "STAFF", "MONITOR", "STUDENT"]
CourseRole = Literal["REVIEWER", "TEST_CREATOR", "COURSE_MANAGER"]


# --- Permisos y confirmación ---------------------------------------------------------------


async def _admin() -> dict:
    """Claims del usuario; error claro si no es ADMIN (antes de tocar la API)."""
    claims = jwt_claims(await api().authenticate())
    if claims.get("role") != "ADMIN":
        raise ToolError(f"Esta tool requiere rol ADMIN en Orbits; tu sesión es {claims.get('role')}.")
    return claims


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _digest(user_id, action: str, args: dict, exp: int) -> str:
    msg = json.dumps([user_id, action, args, exp], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return _b64(hmac.new(_SECRET, msg.encode(), hashlib.sha256).digest())


def make_confirm_token(user_id, action: str, args: dict, now: float | None = None) -> str:
    exp = int((now or time.time()) + CONFIRM_TTL)
    return f"{exp}.{_digest(user_id, action, args, exp)}"


def check_confirm_token(token: str, user_id, action: str, args: dict, now: float | None = None) -> bool:
    exp_s, _, sig = token.partition(".")
    if not exp_s.isdigit() or int(exp_s) < (now or time.time()):
        return False
    return hmac.compare_digest(sig, _digest(user_id, action, args, int(exp_s)))


def _supports_elicitation(ctx: Context | None) -> bool:
    try:
        caps = ctx.session.client_capabilities if ctx else None
    except Exception:  # sin sesión (tests, llamadas directas)
        return False
    return bool(caps and caps.elicitation is not None)


def _is_modern(ctx: Context) -> bool:
    """Protocolo 2026-07-28: sin elicitation iniciada por el servidor; se usa el guard de varias vueltas (SEP-2322)."""
    try:
        return bool(ctx._is_modern_protocol())
    except Exception:
        return False


CONFIRM_KEY = "confirmar"


def _confirm_request(summary: str) -> mcp_types.InputRequiredResult:
    form = mcp_types.ElicitRequestFormParams(
        message=f"{summary}\n\nEsto NO se puede deshacer. ¿Confirmas?",
        requested_schema={
            "type": "object",
            "properties": {CONFIRM_KEY: {"type": "boolean", "title": "Sí, confirmo", "default": False}},
            "required": [CONFIRM_KEY],
        },
    )
    return mcp_types.InputRequiredResult(input_requests={CONFIRM_KEY: mcp_types.ElicitRequest(params=form)})


async def _confirm(ctx: Context | None, claims: dict, action: str, args: dict, summary: str,
                   confirm_token: str | None):
    """None = se puede ejecutar. Si no, devuelve lo que la tool debe retornar sin hacer nada:
    una pregunta al usuario (InputRequiredResult), un aviso de cancelación o el token de confirmación."""
    user_id = claims.get("sub")
    if confirm_token:
        if check_confirm_token(confirm_token, user_id, action, args):
            return None
        raise ToolError("confirm_token inválido, vencido o de otra acción/argumentos. Pide confirmación de nuevo.")
    cancelled = {"status": "cancelled", "message": "El usuario no confirmó. No se hizo nada."}
    if _supports_elicitation(ctx) and _is_modern(ctx):
        answer = (ctx.input_responses or {}).get(CONFIRM_KEY)
        if answer is None:
            return _confirm_request(summary)  # el cliente le pregunta al usuario y repite la llamada con la respuesta
        if getattr(answer, "action", None) == "accept" and (getattr(answer, "content", None) or {}).get(CONFIRM_KEY) is True:
            return None
        return cancelled
    if _supports_elicitation(ctx):
        try:
            res = await ctx.elicit(f"{summary}\n\nEsto NO se puede deshacer. ¿Confirmas?", response_type=bool)
        except Exception:
            res = None  # el cliente declaró la capacidad pero falló: caer al token
        if res is not None:
            if res.action == "accept" and res.data:
                return None
            return cancelled
    return {
        "status": "confirmation_required",
        "summary": summary,
        "confirmToken": make_confirm_token(user_id, action, args),
        "expiresInSeconds": CONFIRM_TTL,
        "message": (
            "NO se ejecutó nada. Muestra este resumen al usuario y pídele confirmación explícita. "
            "Solo si responde que sí, vuelve a llamar la misma tool con los mismos argumentos y confirm_token."
        ),
    }


def _status(e: ApiError) -> int:
    return getattr(e, "status", 0)


# --- Lectura ------------------------------------------------------------------------------


async def _users_by_email() -> dict[str, dict]:
    res = await api().call("GET", "/users")
    return {u["email"].strip().lower(): u for u in res["users"]}


@mcp.tool(annotations=READ, tags={"admin"})
async def find_users(query: str | None = None, role: Role | None = None, limit: int = 50) -> dict:
    """(Admin) Busca usuarios de cualquier rol por correo o nombre (coincidencia parcial).

    Devuelve eduserId, email, role, fullName y sus tags. Sin query lista los primeros `limit`.
    """
    await _admin()
    res = await api().call("GET", "/users")
    q = (query or "").strip().lower()
    users = [
        u for u in res["users"]
        if (not q or q in u["email"].lower() or q in (u.get("fullName") or "").lower())
        and (not role or u["role"] == role)
    ]
    return {"total": len(users), "users": users[:limit]}


@mcp.tool(annotations=READ, tags={"admin"})
async def get_user(user_id: int) -> dict:
    """(Admin) Detalle de un usuario: datos, rol, tags y roles de curso."""
    await _admin()
    user = await api().call("GET", f"/user/{user_id}")
    user["studentTags"] = (await api().call("GET", f"/student/{user_id}/tags")).get("tags", []) if user["role"] == "STUDENT" else []
    user["courseRoles"] = await api().call("GET", f"/staff/{user_id}/roles") if user["role"] != "STUDENT" else []
    return user


@mcp.tool(annotations=READ, tags={"admin"})
async def list_tags() -> dict:
    """(Admin) Todas las tags. Las tags son la inscripción: un alumno ve un curso si comparte una tag con él."""
    await _admin()
    return await api().call("GET", "/tags")


@mcp.tool(annotations=READ, tags={"admin"})
async def get_tag(tag_id: int) -> dict:
    """(Admin) Una tag con sus cursos, sus usuarios y sus monitores."""
    await _admin()
    tag = await api().call("GET", f"/tag/{tag_id}")
    tag["courses"] = (await api().call("GET", f"/tag/{tag_id}/courses"))["courses"]
    tag["users"] = (await api().call("GET", f"/tag/{tag_id}/users"))["users"]
    tag["monitors"] = (await api().call("GET", f"/tag/{tag_id}/monitors"))["users"]
    return tag


@mcp.tool(annotations=READ, tags={"admin"})
async def course_access(course_id: int) -> dict:
    """(Admin) Quién accede a un curso: sus tags (con cantidad de alumnos) y los roles docentes."""
    await _admin()
    course = await api().call("GET", f"/course/{course_id}")
    tags = (await api().call("GET", f"/course/{course_id}/tags"))["tags"]
    for t in tags:
        t["users"] = len((await api().call("GET", f"/tag/{t['tag_id']}/users"))["users"])
    return {"course": course, "tags": tags, "roles": await api().call("GET", f"/course/{course_id}/roles")}


# --- Usuarios -----------------------------------------------------------------------------


@mcp.tool(annotations=IRREVERSIBLE, tags={"admin"})
async def create_users(users: list[dict], tag_id: int | None = None, confirm_token: str | None = None,
                       ctx: Context = CurrentContext()) -> dict:
    """(Admin) Crea cuentas y, opcionalmente, las inscribe en una tag. Pide confirmación (no hay borrado de usuarios).

    Los correos que ya existen NO se recrean ni se les cambia la clave (repitentes): solo se agregan a la tag.

    Args:
        users: lista de {email, password, first_name?, last_name?, role?}. role por omisión STUDENT.
            Convención UTalca: clave = RUT sin puntos ni dígito verificador.
        tag_id: tag a la que se agregan todos (nuevos y existentes). Opcional.
        confirm_token: solo en la segunda llamada, tras la confirmación del usuario.
    """
    claims = await _admin()
    clean, problems = [], []
    for i, u in enumerate(users):
        email = str(u.get("email") or "").strip().lower()
        role = u.get("role") or "STUDENT"
        if "@" not in email or not u.get("password"):
            problems.append({"index": i, "email": email, "message": "falta email válido o password"})
        elif role not in Role.__args__:
            problems.append({"index": i, "email": email, "message": f"role inválido: {role}"})
        else:
            clean.append({"email": email, "password": str(u["password"]), "first_name": u.get("first_name"),
                          "last_name": u.get("last_name"), "role": role})
    if problems:
        return {"ok": False, "problems": problems, "message": "Corrige estas filas; no se creó nada."}
    if len({u["email"] for u in clean}) != len(clean):
        return {"ok": False, "message": "Hay correos repetidos en la lista; no se creó nada."}

    index = await _users_by_email()
    nuevos = [u for u in clean if u["email"] not in index]
    existentes = [u["email"] for u in clean if u["email"] in index]
    tag_name = (await api().call("GET", f"/tag/{tag_id}"))["name"] if tag_id else None

    if nuevos:
        roles = {r: sum(1 for u in nuevos if u["role"] == r) for r in {u["role"] for u in nuevos}}
        summary = (
            f"Crear {len(nuevos)} cuenta(s) nuevas ({', '.join(f'{n} {r}' for r, n in roles.items())}): "
            + ", ".join(u["email"] for u in nuevos[:15]) + (" …" if len(nuevos) > 15 else "")
            + (f". {len(existentes)} ya existen y no se tocan." if existentes else "")
            + (f" Todos quedan en la tag {tag_id} «{tag_name}»." if tag_id else "")
        )
        # el token se liga a correos+roles+tag, no a las claves (no se firman ni se devuelven)
        firmados = {"users": sorted(f"{u['email']}:{u['role']}" for u in clean), "tag_id": tag_id}
        if pending := await _confirm(ctx, claims, "create_users", firmados, summary, confirm_token):
            return pending

    creados, errores = [], []
    for u in nuevos:
        try:
            await api().call("POST", "/user", json=u)
            creados.append(u["email"])
        except ApiError as e:
            if _status(e) == 409:
                existentes.append(u["email"])
            else:
                errores.append({"email": u["email"], "error": str(e)})

    inscritos, ya_estaban = [], []
    if tag_id:
        index = await _users_by_email()
        for email in creados + existentes:
            uid = index.get(email, {}).get("eduserId")
            if uid is None:
                errores.append({"email": email, "error": "no aparece en /users después de crearlo"})
                continue
            try:
                await api().call("POST", "/tag/student", json={"tag_id": tag_id, "student_id": uid})
                inscritos.append(email)
            except ApiError as e:
                (ya_estaban.append(email) if _status(e) == 409 else errores.append({"email": email, "error": str(e)}))
    return {"ok": not errores, "created": creados, "alreadyExisted": existentes, "addedToTag": inscritos,
            "alreadyInTag": ya_estaban, "errors": errores, "tagId": tag_id}


@mcp.tool(annotations=WRITE, tags={"admin"})
async def update_user(user_id: int, first_name: str | None = None, last_name: str | None = None,
                      nickname: str | None = None, institution_id: int | None = None) -> dict:
    """(Admin) Cambia nombre, apellido, nickname o institución de un usuario. Devuelve los valores previos.

    La API no permite cambiar email ni rol global.
    """
    await _admin()
    antes = await api().call("GET", f"/user/{user_id}")
    cambios = {k: v for k, v in {"firstName": first_name, "lastName": last_name, "nickname": nickname,
                                 "institutionId": institution_id}.items() if v is not None}
    if not cambios:
        raise ToolError("No indicaste ningún campo para cambiar.")
    await api().call("PATCH", "/user", json={"eduserId": user_id, **cambios})
    return {"ok": True, "userId": user_id, "changed": cambios, "previous": {k: antes.get(k) for k in cambios}}


@mcp.tool(annotations=IRREVERSIBLE, tags={"admin"})
async def set_user_password(user_id: int, new_password: str, confirm_token: str | None = None,
                            ctx: Context = CurrentContext()) -> dict:
    """(Admin) Fija la clave de otro usuario sin pasar por el correo. Pide confirmación (la clave anterior se pierde).

    Pisa cualquier clave que el usuario haya puesto con «olvidé mi contraseña». Si la nueva clave es un RUT,
    Orbits le pedirá cambiarla al iniciar sesión. La clave nunca se devuelve ni se registra.
    """
    claims = await _admin()
    if not new_password.strip():
        raise ToolError("La clave nueva no puede estar vacía.")
    user = await api().call("GET", f"/user/{user_id}")
    summary = f"Cambiar la clave de {user['email']} (id {user_id}, {user['role']}). La clave actual deja de funcionar."
    firmados = {"user_id": user_id, "pw": hashlib.sha256(new_password.encode()).hexdigest()}
    if pending := await _confirm(ctx, claims, "set_user_password", firmados, summary, confirm_token):
        return pending
    await api().call("POST", f"/admin/users/{user_id}/password", json={"new_password": new_password})
    return {"ok": True, "userId": user_id, "email": user["email"], "message": "Clave actualizada."}


# --- Tags e inscripción -------------------------------------------------------------------


@mcp.tool(annotations=WRITE, tags={"admin"})
async def create_tag(name: str, description: str | None = None) -> dict:
    """(Admin) Crea una tag (p. ej. «Solución Algorítmica 2027-1 - Sección A»). Revisa list_tags antes para no duplicar."""
    await _admin()
    return await api().call("POST", "/tag", json={"name": name, "description": description})


@mcp.tool(annotations=WRITE, tags={"admin"})
async def link_tag_to_course(tag_id: int, course_id: int) -> dict:
    """(Admin) Da acceso al curso a todos los alumnos de la tag. Reversible con unlink_tag_from_course."""
    await _admin()
    try:
        await api().call("POST", "/tag/course", json={"tag_id": tag_id, "course_id": course_id})
    except ApiError as e:
        if _status(e) == 409:
            return {"ok": True, "alreadyLinked": True, "tagId": tag_id, "courseId": course_id}
        raise
    return {"ok": True, "tagId": tag_id, "courseId": course_id}


@mcp.tool(annotations=REVERSIBLE_REMOVE, tags={"admin"})
async def unlink_tag_from_course(tag_id: int, course_id: int) -> dict:
    """(Admin) Quita a los alumnos de la tag el acceso al curso. Reversible con link_tag_to_course."""
    await _admin()
    await api().call("DELETE", "/tag/course", json={"tag_id": tag_id, "course_id": course_id})
    return {"ok": True, "tagId": tag_id, "courseId": course_id, "undo": "link_tag_to_course"}


async def _bulk_tag_students(method: str, tag_id: int, student_ids: list[int]) -> dict:
    ok, skipped, errores = [], [], []
    for sid in dict.fromkeys(student_ids):
        try:
            await api().call(method, "/tag/student", json={"tag_id": tag_id, "student_id": sid})
            ok.append(sid)
        except ApiError as e:
            (skipped.append(sid) if _status(e) in (404, 409) else errores.append({"studentId": sid, "error": str(e)}))
    return {"ok": not errores, "tagId": tag_id, "done": ok, "skipped": skipped, "errors": errores}


@mcp.tool(annotations=WRITE, tags={"admin"})
async def add_students_to_tag(tag_id: int, student_ids: list[int]) -> dict:
    """(Admin) Inscribe usuarios existentes (eduserId) en una tag. Los que ya estaban quedan en skipped."""
    await _admin()
    return await _bulk_tag_students("POST", tag_id, student_ids)


@mcp.tool(annotations=REVERSIBLE_REMOVE, tags={"admin"})
async def remove_students_from_tag(tag_id: int, student_ids: list[int]) -> dict:
    """(Admin) Saca usuarios de una tag (pierden el acceso a sus cursos). Reversible con add_students_to_tag."""
    await _admin()
    res = await _bulk_tag_students("DELETE", tag_id, student_ids)
    res["undo"] = "add_students_to_tag"
    return res


@mcp.tool(annotations=IRREVERSIBLE, tags={"admin"})
async def delete_tag(tag_id: int, remove_associations: bool = False, confirm_token: str | None = None,
                     ctx: Context = CurrentContext()) -> dict:
    """(Admin) Borra una tag. Pide confirmación.

    La API solo borra tags sin alumnos ni cursos. Con remove_associations=true primero los desasocia
    (los alumnos pierden el acceso que les daba esa tag) y devuelve la lista para poder rehacerlo.
    """
    claims = await _admin()
    tag = await get_tag(tag_id)
    alumnos, cursos = [u["eduserId"] for u in tag["users"]], [c["course_id"] for c in tag["courses"]]
    if (alumnos or cursos) and not remove_associations:
        return {"ok": False, "tag": tag["name"], "students": len(alumnos), "courses": cursos,
                "message": "La tag tiene asociaciones. Vuelve a llamar con remove_associations=true si el usuario lo quiere."}
    summary = (f"Borrar la tag {tag_id} «{tag['name']}»"
               + (f", quitándole {len(alumnos)} alumno(s) y {len(cursos)} curso(s) ({', '.join(c['name'] for c in tag['courses'])})"
                  if alumnos or cursos else " (sin asociaciones)") + ".")
    firmados = {"tag_id": tag_id, "remove_associations": remove_associations, "students": len(alumnos), "courses": cursos}
    if pending := await _confirm(ctx, claims, "delete_tag", firmados, summary, confirm_token):
        return pending
    for cid in cursos:
        await api().call("DELETE", "/tag/course", json={"tag_id": tag_id, "course_id": cid})
    quitados = await _bulk_tag_students("DELETE", tag_id, alumnos) if alumnos else {"errors": []}
    if quitados["errors"]:
        return {"ok": False, "message": "No se pudo quitar a todos los alumnos; la tag NO se borró.", **quitados}
    await api().call("DELETE", f"/tag/{tag_id}")
    return {"ok": True, "deleted": {"tagId": tag_id, "name": tag["name"], "description": tag.get("description")},
            "removedStudents": alumnos, "removedCourses": cursos}


# --- Cursos y roles -----------------------------------------------------------------------


@mcp.tool(annotations=WRITE, tags={"admin"})
async def create_course(name: str, description: str, teacher_id: int | None = None,
                        institution_id: int | None = None) -> dict:
    """(Admin) Crea un curso. Para que los alumnos lo vean, asócialo a una tag con link_tag_to_course."""
    await _admin()
    return await api().call("POST", "/course", json={"name": name, "description": description,
                                                     "teacher_id": teacher_id, "institution_id": institution_id})


@mcp.tool(annotations=WRITE, tags={"admin"})
async def update_course(course_id: int, name: str | None = None, description: str | None = None,
                        teacher_id: int | None = None, institution_id: int | None = None) -> dict:
    """(Admin) Cambia nombre, descripción, profesor o institución de un curso. Devuelve los valores previos."""
    await _admin()
    antes = await api().call("GET", f"/course/{course_id}")
    cambios = {k: v for k, v in {"name": name, "description": description, "teacher_id": teacher_id,
                                 "institution_id": institution_id}.items() if v is not None}
    if not cambios:
        raise ToolError("No indicaste ningún campo para cambiar.")
    await api().call("PATCH", f"/course/{course_id}", json=cambios)
    return {"ok": True, "courseId": course_id, "changed": cambios, "previous": {k: antes.get(k) for k in cambios}}


@mcp.tool(annotations=IRREVERSIBLE, tags={"admin"})
async def delete_course(course_id: int, confirm_token: str | None = None, ctx: Context = CurrentContext()) -> dict:
    """(Admin) Borra un curso (soft delete: deja de aparecer; la API no permite restaurarlo). Pide confirmación."""
    claims = await _admin()
    acceso = await course_access(course_id)
    tests = await api().call("GET", "/tests", {"courseId": course_id})
    n_tests = tests["total"]
    alumnos = sum(t["users"] for t in acceso["tags"])
    summary = (f"Borrar el curso {course_id} «{acceso['course']['name']}»: {n_tests} test(s), "
               f"{len(acceso['tags'])} tag(s) con {alumnos} alumno(s) en total y {len(acceso['roles'])} rol(es) docentes.")
    if pending := await _confirm(ctx, claims, "delete_course", {"course_id": course_id}, summary, confirm_token):
        return pending
    await api().call("DELETE", f"/course/{course_id}")
    return {"ok": True, "deleted": acceso["course"]}


@mcp.tool(annotations=WRITE, tags={"admin"})
async def grant_course_role(course_id: int, user_id: int, role: CourseRole) -> dict:
    """(Admin) Da un rol docente en un curso (REVIEWER, TEST_CREATOR, COURSE_MANAGER). No sirve para alumnos: usa tags."""
    await _admin()
    await api().call("POST", f"/course/{course_id}/role", json={"targetUserId": user_id, "role": role})
    return {"ok": True, "courseId": course_id, "userId": user_id, "role": role, "undo": "revoke_course_role"}


@mcp.tool(annotations=REVERSIBLE_REMOVE, tags={"admin"})
async def revoke_course_role(course_id: int, user_id: int, role: CourseRole) -> dict:
    """(Admin) Quita un rol docente de un curso. Reversible con grant_course_role."""
    await _admin()
    await api().call("DELETE", f"/course/{course_id}/role", json={"targetUserId": user_id, "role": role})
    return {"ok": True, "courseId": course_id, "userId": user_id, "role": role, "undo": "grant_course_role"}


@mcp.tool(annotations=WRITE, tags={"admin"})
async def assign_monitor_tag(user_id: int, tag_id: int) -> dict:
    """(Admin) Asigna una tag a un usuario MONITOR (verá a esos alumnos en su panel)."""
    await _admin()
    try:
        await api().call("POST", f"/monitor/{user_id}/tag", json={"tag_id": tag_id})
    except ApiError as e:
        if _status(e) == 409:
            return {"ok": True, "alreadyAssigned": True}
        raise
    return {"ok": True, "userId": user_id, "tagId": tag_id, "undo": "remove_monitor_tag"}


@mcp.tool(annotations=REVERSIBLE_REMOVE, tags={"admin"})
async def remove_monitor_tag(user_id: int, tag_id: int) -> dict:
    """(Admin) Quita una tag a un monitor. Reversible con assign_monitor_tag."""
    await _admin()
    await api().call("DELETE", f"/monitor/{user_id}/tag/{tag_id}")
    return {"ok": True, "userId": user_id, "tagId": tag_id, "undo": "assign_monitor_tag"}


# --- Edubits retroactivos -----------------------------------------------------------------


def _retro_body(test_id: int, from_date: str, to_date: str, coins: int | None) -> dict:
    return {"edutestId": test_id, "from": from_date, "to": to_date, "coinsPerQuestionOverride": coins}


@mcp.tool(annotations=READ, tags={"admin"})
async def retroactive_awards_preview(test_id: int, from_date: str, to_date: str,
                                     coins_per_question_override: int | None = None) -> dict:
    """(Admin) Simula los edubits que faltan acreditar de un test en un rango (ISO UTC, p. ej. 2026-08-01T00:00:00Z).

    La deuda real es la suma de awardedAmount > 0 en `eligible`; eligibleCount incluye nota 0 y topados.
    """
    await _admin()
    return await api().call("POST", "/admin/retroactive-awards/preview",
                            json=_retro_body(test_id, from_date, to_date, coins_per_question_override))


@mcp.tool(annotations=IRREVERSIBLE, tags={"admin"})
async def retroactive_awards_execute(test_id: int, from_date: str, to_date: str,
                                     coins_per_question_override: int | None = None,
                                     confirm_token: str | None = None, ctx: Context = CurrentContext()) -> dict:
    """(Admin) Acredita en edubank los edubits pendientes de un test en un rango. Pide confirmación.

    Recomendado: rangos de un intento ([finishedAt - 1 s, finishedAt + 1 s]) en orden cronológico,
    así se respeta el tope por alumno. Los ya acreditados se saltan solos.
    """
    claims = await _admin()
    body = _retro_body(test_id, from_date, to_date, coins_per_question_override)
    prev = await api().call("POST", "/admin/retroactive-awards/preview", json=body)
    pagar = [e for e in prev.get("eligible", []) if (e.get("awardedAmount") or 0) > 0]
    total = sum(e["awardedAmount"] for e in pagar)
    if not pagar:
        return {"ok": True, "message": "No hay edubits pendientes en ese rango; no se hizo nada.", "preview": prev}
    summary = (f"Acreditar {total} edubits a {len({e.get('studentId') for e in pagar})} alumno(s) por {len(pagar)} intento(s) "
               f"del test {test_id} «{prev.get('edutestName')}» entre {from_date} y {to_date}.")
    if pending := await _confirm(ctx, claims, "retroactive_awards_execute", {**body, "total": total}, summary, confirm_token):
        return pending
    return await api().call("POST", "/admin/retroactive-awards/execute", json=body)
