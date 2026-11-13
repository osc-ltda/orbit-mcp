"""Servidor MCP de Orbits: tools sobre la API REST."""

import asyncio
import io
import json
import time
import zipfile
from importlib.resources import files
from pathlib import Path

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import ApiError, OrbitsClient, build_bundle, jwt_claims, poll_device_login, raise_for_api, start_device_login

READ_ONLY = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
IMPORT_TIMEOUT = 120.0
POLL_SECONDS = 1.0

mcp = FastMCP(
    "orbits",
    instructions="""Orbits es una plataforma de evaluaciones.
Sesión: si una tool responde que no hay sesión de Orbits, llama la tool login y muestra al usuario el enlace
(verificationUriComplete) y el código (userCode); cuando autorice en el navegador, vuelve a llamar login para confirmar.
Flujo para crear tests:
1. Antes de redactar un test lee el recurso orbits://docs/import-format (formato de test.json).
2. Usa list_courses y course_topics para obtener el course_id y reutilizar los nombres de temas existentes.
3. import_test crea el test como BORRADOR (no visible para alumnos). append_questions agrega preguntas a un test existente.
4. Si el resultado trae ok=false con problems [{path, message}], corrige exactamente esos puntos y reintenta.
5. set_test_published hace visible el test a los alumnos: úsalo SOLO si el usuario lo pide explícitamente.
Análisis: test_results, course_results, student_results, tag_summary y list_attempts. Todos los ids son enteros.""",
)

_api: OrbitsClient | None = None


def api() -> OrbitsClient:
    global _api
    if _api is None or not _api.pat:  # sin token: releer por si el usuario ya hizo login
        _api = OrbitsClient()
    return _api


_login_task: asyncio.Task | None = None
_login_flow: dict = {}


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
async def login() -> dict:
    """Inicia sesión en Orbits desde el navegador. Si ya hay sesión devuelve el usuario (status ok).

    Si no, devuelve status pending con un enlace y un código que debes mostrar al usuario; cuando
    autorice, vuelve a llamar login para confirmar. Llamarla mientras está pendiente no inicia otro flujo.
    """
    global _login_task, _login_flow
    if _login_task and _login_task.done():
        task, _login_task = _login_task, None
        if not task.cancelled() and task.exception():
            return {"status": "error", "message": f"{task.exception()} Llama login de nuevo para obtener otro código."}
    if _login_task is None:
        try:
            return {"status": "ok", **await whoami()}
        except ApiError as e:
            if e.status not in (0, 401):  # solo falta de sesión o token inválido inician el flujo
                raise
        _login_flow = await start_device_login(api())
        _login_task = asyncio.create_task(poll_device_login(api(), _login_flow))
    return {
        "status": "pending",
        "verificationUri": _login_flow["verificationUri"],
        "userCode": _login_flow["userCode"],
        "verificationUriComplete": _login_flow["verificationUriComplete"],
        "message": f"Abre {_login_flow['verificationUriComplete']} (o entra a {_login_flow['verificationUri']} "
        f"e ingresa el código {_login_flow['userCode']}), inicia sesión y autoriza. Luego vuelve a llamar login.",
    }


@mcp.prompt(name="login", description="Iniciar sesión en Orbits")
def login_prompt() -> str:
    return (
        "Llama la tool `login` del servidor orbits. Si devuelve status pending, muestra al usuario el enlace "
        "verificationUriComplete y el código userCode, y pídele que inicie sesión y autorice en el navegador; "
        "cuando confirme, vuelve a llamar `login`. Si devuelve status ok, dile con qué usuario y rol quedó la sesión."
    )


@mcp.resource("orbits://docs/import-format", mime_type="text/markdown")
def import_format() -> str:
    """Formato del bundle de importación de tests (test.json + images/)."""
    # shortcut: copia del doc de la API; regenerar si cambia el formato
    return files("orbits_mcp").joinpath("test-import-format.md").read_text(encoding="utf-8")


@mcp.tool(annotations=READ_ONLY)
async def whoami() -> dict:
    """Usuario autenticado en Orbits (id, email, nombre, rol) y URL de la API."""
    claims = jwt_claims(await api().authenticate())
    return {
        "userId": int(claims["sub"]),
        "email": claims.get("email"),
        "name": " ".join(filter(None, [claims.get("firstName"), claims.get("lastName")])),
        "role": claims.get("role"),
        "url": api().url,
    }


@mcp.tool(annotations=READ_ONLY)
async def list_courses(search: str | None = None) -> dict:
    """Lista los cursos accesibles para el usuario.

    Args:
        search: filtro opcional por nombre (coincidencia parcial).
    """
    return await api().call("GET", "/courses", {"name": search})


@mcp.tool(annotations=READ_ONLY)
async def course_topics(course_id: int) -> list[dict]:
    """Temas (topics) de un curso."""
    return await api().call("GET", f"/course/{course_id}/topics")


@mcp.tool(annotations=READ_ONLY)
async def list_tests(course_id: int | None = None, name: str | None = None) -> dict:
    """Lista tests, opcionalmente filtrados por curso y/o nombre (coincidencia parcial)."""
    return await api().call("GET", "/tests", {"courseId": course_id, "edutestName": name})


@mcp.tool(annotations=READ_ONLY)
async def get_test(test_id: int) -> dict:
    """Detalle de un test y todas sus preguntas (con solución e imagen)."""
    test = await api().call("GET", f"/test/{test_id}")
    questions, page = [], 1
    while True:
        res = await api().call(
            "GET", f"/test/{test_id}/questions", {"page": page, "perPage": 100, "include": "full"}
        )
        questions += res["items"]
        if page >= res["total_pages"]:  # PaginatedResponse de la API no usa camelCase
            return {"test": test, "questions": questions}
        page += 1


async def _upload(path: str, test: dict, image_paths: dict[str, str] | None, data: dict | None = None) -> dict:
    zip_bytes = build_bundle(test, image_paths)
    r = await api().request("POST", path, files={"file": ("test.zip", zip_bytes, "application/zip")}, data=data)
    if r.status_code == 400 and r.headers.get("content-type", "").startswith("application/json"):
        body = r.json()
        if isinstance(body, list):  # [{path, message}]: el modelo debe corregir y reintentar
            return {"ok": False, "problems": body}
    raise_for_api(r)
    started = r.json()
    test_id, job_id = started["edutestId"], started["jobId"]
    deadline = time.monotonic() + IMPORT_TIMEOUT
    while True:
        job = await api().call("GET", f"/test/{test_id}/import-job")
        done = job["jobId"] == job_id and job["status"] != "RUNNING"
        if done or time.monotonic() > deadline:
            break
        await asyncio.sleep(POLL_SECONDS)
    result = {"ok": done and job["status"] == "COMPLETED", "testId": test_id, "status": job["status"] if done else "RUNNING"}
    if done and job.get("error"):
        result["problems"] = job["error"]
    if not done:
        result["message"] = "La importación sigue en curso; revisa más tarde con get_test."
    return result


@mcp.tool(annotations=WRITE)
async def import_test(course_id: int, test: dict, image_paths: dict[str, str] | None = None) -> dict:
    """Crea un test nuevo (BORRADOR, no publicado) en un curso a partir de un test.json.

    Lee antes orbits://docs/import-format. Si ok=false con problems, corrígelos y reintenta.

    Args:
        course_id: curso destino.
        test: contenido de test.json (formatVersion 1).
        image_paths: imágenes a incluir, ruta en el bundle (p. ej. "images/q1.png") -> ruta local del archivo.
    """
    return await _upload("/test/import", test, image_paths, {"courseId": str(course_id)})


@mcp.tool(annotations=WRITE)
async def append_questions(test_id: int, test: dict, image_paths: dict[str, str] | None = None) -> dict:
    """Agrega preguntas a un test existente (mismo formato test.json; solo se leen formatVersion y questionSets).

    Args:
        test_id: test destino.
        test: contenido de test.json.
        image_paths: imágenes a incluir, ruta en el bundle -> ruta local del archivo.
    """
    return await _upload(f"/test/{test_id}/import", test, image_paths)


@mcp.tool(annotations=READ_ONLY)
async def export_test(test_id: int, save_zip_to: str | None = None) -> dict:
    """Exporta un test en formato de importación: devuelve test.json y los nombres de sus imágenes.

    Args:
        test_id: test a exportar.
        save_zip_to: ruta local opcional donde guardar el .zip completo (con imágenes).
    """
    r = await api().request("GET", f"/test/{test_id}/export")
    raise_for_api(r)
    result = {}
    if save_zip_to:
        path = Path(save_zip_to).expanduser()
        path.write_bytes(r.content)
        result["savedTo"] = str(path)
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        result["test"] = json.loads(z.read("test.json"))
        result["images"] = [n for n in z.namelist() if n != "test.json" and not n.endswith("/")]
    return result


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True))
async def set_test_published(test_id: int, published: bool) -> dict:
    """Publica (visible para los alumnos) o despublica un test. Úsalo SOLO si el usuario lo pide explícitamente."""
    return await api().call("PATCH", f"/test/{test_id}/publish", json={"published": published})


@mcp.tool(annotations=READ_ONLY)
async def test_results(test_id: int) -> dict:
    """Resultados agregados de un test (monitor)."""
    return await api().call("GET", f"/monitor/test/{test_id}")


@mcp.tool(annotations=READ_ONLY)
async def course_results(course_id: int) -> dict:
    """Resultados agregados de un curso (monitor)."""
    return await api().call("GET", f"/monitor/course/{course_id}")


@mcp.tool(annotations=READ_ONLY)
async def student_results(student_id: int) -> dict:
    """Resultados de un alumno en sus cursos (monitor)."""
    return await api().call("GET", f"/monitor/student/{student_id}")


@mcp.tool(annotations=READ_ONLY)
async def tag_summary(tag_id: int) -> dict:
    """Resumen de resultados de un tag (grupo de alumnos/cursos)."""
    return await api().call("GET", f"/monitor/tag/{tag_id}/summary")


@mcp.tool(annotations=READ_ONLY)
async def list_attempts(
    test_id: int | None = None,
    course_id: int | None = None,
    student_id: int | None = None,
    min_score: int | None = None,
    max_score: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Intentos de tests con filtros opcionales.

    Args:
        start_date: fecha ISO 8601 con zona (p. ej. 2026-03-01T00:00:00Z).
        end_date: fecha ISO 8601 con zona.
    """
    return await api().call(
        "GET",
        "/attempts",
        {
            "edutest_id": test_id,
            "course_id": course_id,
            "student_id": student_id,
            "min_score": min_score,
            "max_score": max_score,
            "start_date": start_date,
            "end_date": end_date,
        },
    )
