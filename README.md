# orbits-mcp

Servidor MCP de [Orbits](https://orbits.cl). Con él, tu agente (Claude Code, Codex, omp o cualquier cliente MCP) puede crear tests y analizar resultados con **tus permisos**.

Está publicado en **`https://prod.orbits.cl/mcp`**. No necesitas instalar nada: solo agregas la URL a tu agente.

## 1. Agregarlo a tu agente

### Claude Code

```sh
claude mcp add --transport http --scope user orbits https://prod.orbits.cl/mcp
```

Con `--scope user` queda disponible en todos tus proyectos. Si usas `--scope project`, se agrega solo al proyecto actual, en un `.mcp.json` que puedes commitear para compartirlo con tu equipo.

### Codex

```sh
codex mcp add orbits --url https://prod.orbits.cl/mcp
codex mcp login orbits
```

También puedes agregarlo a mano en `~/.codex/config.toml`:

```toml
[mcp_servers.orbits]
url = "https://prod.orbits.cl/mcp"
```

### omp

Crea `.omp/mcp.json` en la carpeta del proyecto (para usarlo solo ahí) o `~/.omp/agent/mcp.json` (para todos tus proyectos):

```json
{
  "mcpServers": {
    "orbits": { "type": "http", "url": "https://prod.orbits.cl/mcp" }
  }
}
```

> omp también importa la configuración MCP de Claude Code y Codex. Si ya lo agregaste ahí con alcance de usuario, omp lo verá en todos tus proyectos.

### Otro cliente MCP (Cursor, Claude Desktop, VS Code, etc.)

Agrega un servidor MCP remoto (HTTP) con la URL `https://prod.orbits.cl/mcp`.

## 2. Iniciar sesión (una vez)

La primera vez que el agente usa Orbits, el cliente abre el navegador:

1. Inicia sesión con tu cuenta de Orbits, si no lo has hecho.
2. Revisa el nombre del cliente que pide acceso y haz click en **Autorizar**.
3. El navegador vuelve a tu agente y queda conectado.

Si no se abre solo:

- **Claude Code:** escribe `/mcp` → `orbits` → *Authenticate*.
- **Codex:** corre `codex mcp login orbits`.
- **omp:** escribe `/mcp`.

La sesión dura 90 días. Tu contraseña nunca pasa por el agente. Puedes ver y revocar los accesos en tu perfil de Orbits, en **Tokens de acceso**.

## 3. Úsalo

Ejemplos de lo que puedes pedirle al agente:

- "Crea un test de 10 preguntas de fracciones para el curso Matemática 4° Básico". El test se crea como **borrador**.
- "¿Qué preguntas del test 383 tuvieron peor resultado y qué alternativa incorrecta eligieron más?"
- "Publica el test 383." Solo publica cuando se lo pides, y el cliente te pide confirmación.

Lo que expone:

- **Tools de lectura:** `whoami`, `list_courses`, `course_topics`, `list_tests`, `get_test`, `export_test`, `test_results`, `course_results`, `student_results`, `tag_summary`, `list_attempts`.
- **Tools de escritura:** `import_test`, `append_questions`, `set_test_published`. Las imágenes van en `images`, en base64.
- **Recurso** `orbits://docs/import-format`, con el formato de los tests.

Los permisos son los de tu cuenta: un staff ve los resultados de los cursos donde tiene rol.

## Alternativa: correrlo localmente

Esta opción sirve para desarrollo, o si quieres que el servidor lea imágenes directamente de tu disco (`image_paths`). Requiere [uv](https://docs.astral.sh/uv/).

```sh
claude mcp add orbits -- uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp
uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp login   # abre el navegador; guarda el token en ~/.config/orbits
```

En Codex y omp se usa el mismo comando stdio: `uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp`.

Variables de entorno del modo local:

- `ORBITS_URL`: URL de la API. Por defecto es `https://prod.orbits.cl/api/v1`.
- `ORBITS_TOKEN`: token personal (`orb_...`), útil para CI o máquinas sin navegador.

## Desarrollo

```sh
uv run pytest
uv run orbits-mcp --help                 # stdio local + login/logout/whoami
MCP_SECRET=dev MCP_PUBLIC_URL=http://localhost:8000 uv run orbits-mcp serve-http   # modo remoto (OAuth)
```

Al publicar en `main` se construye la imagen `ghcr.io/osc-ltda/orbit-mcp:sha-<commit>`; en producción se despliega desde [orbit-ops](https://github.com/osc-ltda/orbit-ops).

## Licencia

[GPL-3.0](LICENSE). Puedes usarlo libremente; si distribuyes una versión modificada, debes compartir su código bajo la misma licencia.
