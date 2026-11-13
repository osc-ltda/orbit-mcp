# orbits-mcp

Servidor MCP de [Orbits](https://prod.orbits.cl) para staff: crear tests (importación de bundles) y analizar resultados desde Claude Code, Codex, omp u otro cliente MCP.

Requiere [uv](https://docs.astral.sh/uv/).

## 1. Registrar el servidor

**Claude Code**

```sh
claude mcp add orbits -- uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp
```

**Codex**

```sh
codex mcp add orbits -- uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp
```

o en `~/.codex/config.toml`:

```toml
[mcp_servers.orbits]
command = "uvx"
args = ["--from", "git+https://github.com/osc-ltda/orbit-mcp", "orbits-mcp"]
```

**omp**: importa solo la configuración de Claude Code y Codex. Para registrarlo directamente, agrega el mismo bloque JSON de abajo en `~/.omp/agent/mcp.json`.

**Cualquier cliente stdio** (JSON):

```json
{
  "mcpServers": {
    "orbits": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/osc-ltda/orbit-mcp", "orbits-mcp"]
    }
  }
}
```

## 2. Iniciar sesión

En el agente, usa el slash command del prompt `login` (en Claude Code: `/mcp__orbits__login`) o pídele "inicia sesión en Orbits". El servidor abre el navegador; inicia sesión y autoriza. Si el navegador no se abre, el agente te muestra un enlace y un código para ingresarlo desde cualquier dispositivo.

Alternativa desde la terminal (mismo flujo):

```sh
uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp login
```

Se crea un token de acceso personal `orbits-mcp@<host>` guardado en `~/.config/orbits/credentials.json` (modo 0600); tu contraseña nunca pasa por la terminal. En máquinas sin navegador o CI usa un token ya creado: `orbits-mcp login --token orb_...` (o la variable `ORBITS_TOKEN`).

Otros comandos: `orbits-mcp whoami` (usuario, rol, URL, expiración) y `orbits-mcp logout` (revoca el token y borra el archivo).

## Variables de entorno

- `ORBITS_URL`: URL base de la API (por defecto `https://prod.orbits.cl/api/v1`). Para desarrollo local: `ORBITS_URL=http://localhost:3000/api/v1` (o `orbits-mcp login --url http://localhost:3000/api/v1`, que guarda la URL).
- `ORBITS_TOKEN`: token de acceso personal (`orb_...`); tiene prioridad sobre el archivo de credenciales.

## Qué expone

- Recurso `orbits://docs/import-format`: formato de `test.json`.
- Prompt y tool `login`: inicio de sesión en el navegador.
- Tools de lectura: `whoami`, `list_courses`, `course_topics`, `list_tests`, `get_test`, `export_test`, `test_results`, `course_results`, `student_results`, `tag_summary`, `list_attempts`.
- Escritura: `import_test` (crea el test como borrador), `append_questions` y `set_test_published` (solo cuando el usuario lo pide).

## Desarrollo

```sh
uv run orbits-mcp --help
uv run pytest
```
