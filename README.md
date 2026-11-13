# orbits-mcp

Servidor MCP de [Orbits](https://orbits.cl). Con él, tu agente (Claude Code, Codex, omp o cualquier cliente MCP) puede crear tests y analizar resultados con **tus permisos**.

## Requisito: uv

Una sola vez:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`.) No hace falta instalar nada más: `uvx` descarga y ejecuta `orbits-mcp` la primera vez que se usa.

## 1. Agregarlo a tu agente

Elige tu agente. El servidor queda con el nombre `orbits`.

### Claude Code

```sh
# Disponible en todos tus proyectos
claude mcp add orbits --scope user -- uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp

# o solo en el proyecto actual (crea .mcp.json, que se puede commitear)
claude mcp add orbits --scope project -- uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp
```

Para comprobarlo: `claude mcp list` debe mostrar `orbits … ✔ Connected`.

### Codex

```sh
codex mcp add orbits -- uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp
```

También puedes agregarlo a mano en `~/.codex/config.toml`:

```toml
[mcp_servers.orbits]
command = "uvx"
args = ["--from", "git+https://github.com/osc-ltda/orbit-mcp", "orbits-mcp"]
```

Para comprobarlo: `codex mcp list`.

### omp

Crea `.omp/mcp.json` en la carpeta del proyecto (para que solo esté disponible ahí) o `~/.omp/agent/mcp.json` (para todos los proyectos):

```json
{
  "mcpServers": {
    "orbits": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "git+https://github.com/osc-ltda/orbit-mcp", "orbits-mcp"]
    }
  }
}
```

Para comprobarlo: abre omp en esa carpeta y escribe `/mcp`.

> omp también importa la configuración MCP de Claude Code y Codex. Si ya lo registraste con `--scope user` en alguno de ellos, omp lo verá en todos tus proyectos.

### Otro cliente MCP (Cursor, Claude Desktop, etc.)

Usa el mismo comando stdio: `command: "uvx"`, `args: ["--from", "git+https://github.com/osc-ltda/orbit-mcp", "orbits-mcp"]`.

## 2. Iniciar sesión (una vez)

En el agente, escribe **"inicia sesión en Orbits"**. En Claude Code también puedes usar `/mcp__orbits__login`.

1. Se abre el navegador en `orbits.cl/dispositivo` con un código.
2. Inicia sesión con tu cuenta de Orbits, si no lo has hecho.
3. Revisa el nombre del dispositivo y haz click en **Autorizar**.

Listo: la sesión dura 90 días. Si el navegador no se abre (por ejemplo, el agente corre en otra máquina), el agente te muestra el enlace y el código para ingresarlo desde cualquier dispositivo.

También puedes iniciar sesión desde la terminal, con el mismo flujo:

```sh
uvx --from git+https://github.com/osc-ltda/orbit-mcp orbits-mcp login
```

Tu contraseña nunca pasa por la terminal ni por el agente: se guarda un token personal en `~/.config/orbits/credentials.json` (con permisos 0600). Puedes ver tus tokens y revocarlos en tu perfil de Orbits, en **Tokens de acceso**.

En máquinas sin navegador o en CI, crea un token en tu perfil y úsalo con `orbits-mcp login --token orb_...` o con la variable `ORBITS_TOKEN`.

Otros comandos útiles: `orbits-mcp whoami` y `orbits-mcp logout` (revoca el token y borra las credenciales locales).

## 3. Úsalo

Ejemplos de lo que puedes pedirle al agente:

- "Crea un test de 10 preguntas de fracciones para el curso Matemática 4° Básico". El test se crea como **borrador**.
- "¿Qué preguntas del test 383 tuvieron peor resultado y qué alternativa incorrecta eligieron más?"
- "Publica el test 383." Solo publica cuando se lo pides, y el cliente te pide confirmación.

Lo que expone:

- Tools de lectura: `whoami`, `list_courses`, `course_topics`, `list_tests`, `get_test`, `export_test`, `test_results`, `course_results`, `student_results`, `tag_summary`, `list_attempts`.
- Tools de escritura: `import_test`, `append_questions`, `set_test_published`.
- `login`, como tool y como prompt.
- El recurso `orbits://docs/import-format`, con el formato de los tests.

Los permisos son los de tu cuenta: un staff ve los resultados de los cursos donde tiene rol.

## Variables de entorno

- `ORBITS_URL`: URL de la API (por defecto `https://prod.orbits.cl/api/v1`). Para desarrollo local, usa `http://localhost:3000/api/v1`.
- `ORBITS_TOKEN`: token personal (`orb_...`). Tiene prioridad sobre el archivo de credenciales.

## Desarrollo

```sh
uv run orbits-mcp --help
uv run pytest
```

## Licencia

[GPL-3.0](LICENSE). Puedes usarlo libremente; si distribuyes una versión modificada, debes compartir su código bajo la misma licencia.
