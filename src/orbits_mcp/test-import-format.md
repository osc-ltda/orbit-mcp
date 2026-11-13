# Guía de formato: bundle de importación de tests

Esta guía está dirigida a **profesores y administradores** que deseen importar tests creados externamente a la plataforma Orbit.

---

## 1. ¿Qué es un bundle de importación?

Un bundle de importación es un archivo **`.zip`** autocontenido que le permite cargar en la plataforma un test completo — con sus preguntas, respuestas, contextos e imágenes — sin necesidad de crearlo manualmente dentro de la interfaz.

El bundle puede prepararse con cualquier herramienta externa (un editor de texto, una hoja de cálculo exportada, un LLM, un script propio, etc.) y después importarse desde la sección **Importar test** del panel de administración o staff.

> **Nota:** Al importar, la plataforma le pedirá que seleccione el **curso destino**. El bundle en sí no incluye ningún identificador de curso; esto lo hace portable y reutilizable en distintos cursos.

---

## 2. Estructura del ZIP

```
test.zip
├── test.json          ← definición completa del test (obligatorio, en la raíz)
└── images/            ← imágenes referenciadas por las preguntas o contextos (opcional)
    ├── q1.png
    └── ctx1.png
```

| Elemento | Descripción |
|---|---|
| `test.json` | Archivo JSON con la definición del test. Debe estar en la **raíz** del ZIP. |
| `images/` | Carpeta opcional con las imágenes. Las preguntas y contextos referencian imágenes usando su ruta relativa dentro del ZIP, p. ej. `images/q1.png`. |

**Tipos de imagen permitidos:** `png`, `jpg` / `jpeg`, `webp`.

---

## 3. Esquema de `test.json`

### 3.1 Campos raíz del test

| Campo | Tipo | Requerido | Valores permitidos | Descripción |
|---|---|---|---|---|
| `formatVersion` | number | Sí | `1` | Versión del formato. Siempre debe ser `1`. |
| `name` | string | Sí | Cualquier texto no vacío | Nombre del test que verán los alumnos. |
| `description` | string | No | — | Descripción o instrucciones generales del test. |
| `mode` | string | No | `"TEST"` \| `"TRAINING"` | Modo de evaluación. `"TEST"` oculta el feedback hasta el final; `"TRAINING"` lo muestra de inmediato. Por defecto: `"TEST"`. |
| `timeLimitMinutes` | number | No | Entero positivo | Tiempo límite en minutos para completar el test. Si se omite, no hay límite de tiempo. |
| `fixedOrder` | boolean | No | `true` \| `false` | Orden fijo. Si es `true`, todos los alumnos reciben las mismas preguntas en el mismo orden: las primeras `questionsToShow` de cada set, en el orden del archivo y con los sets en el orden del archivo (sin aleatorizar; las alternativas sí se siguen mezclando). Por defecto: `false`. |
| `questionSets` | array | Sí | Al menos 1 elemento | Lista de conjuntos de preguntas (ver sección 3.2). |

---

### 3.2 Campos de cada `questionSet`

| Campo | Tipo | Requerido | Valores permitidos | Descripción |
|---|---|---|---|---|
| `topic` | string | Sí | Texto no vacío | Nombre del tema al que pertenece este conjunto. Si ya existe un tema con ese nombre en el curso destino, se reutiliza; si no, se crea automáticamente. |
| `questionsToShow` | number | Sí | Entero entre 1 y el nº total de preguntas del set | Cuántas preguntas se muestran a cada alumno. El resto forma el *pool* de reserva para aleatorizar (con `fixedOrder: true` se muestran las primeras). |
| `contexts` | array | No | — | Lista de enunciados compartidos (textos, imágenes o tablas) que varias preguntas del mismo set pueden referenciar (ver sección 3.3). |
| `questions` | array | Sí | Al menos 1 elemento | Lista de preguntas del conjunto (ver sección 3.4). El orden del arreglo es el orden de las preguntas en el editor (y en el test con `fixedOrder`). |

---

### 3.3 Campos de cada `context` (dentro de `contexts`)

| Campo | Tipo | Requerido | Valores permitidos | Descripción |
|---|---|---|---|---|
| `ref` | string | Sí | Texto no vacío, único dentro del set | Identificador local del contexto. Las preguntas del mismo set lo referencian mediante el campo `contextRef`. |
| `type` | string | Sí | `"TEXT"` \| `"IMAGE"` \| `"CODE"` \| `"DATA"` \| `"SCENARIO"` \| `"OTHER"` | Tipo de contexto. Determina cómo lo renderiza la plataforma. Se acepta en mayúsculas o minúsculas (`"image"` = `"IMAGE"`) y se guarda en mayúsculas. Equivalencias: `TEXT` (texto/lectura), `IMAGE` (imagen, usar con `image`), `CODE` (fragmento de código), `DATA` (tabla o datos), `SCENARIO` (escenario/caso), `OTHER` (otro). |
| `title` | string | Sí | Texto no vacío | Título o encabezado del contexto. |
| `description` | string | No | — | Explicación o instrucciones asociadas al contexto. |
| `content` | string | No | — | Cuerpo de texto del contexto (soporta Markdown). |
| `image` | string | No | Ruta relativa dentro del ZIP | Imagen asociada al contexto, p. ej. `images/ctx1.png`. Debe existir en el ZIP. |

---

### 3.4 Campos de cada `question` (dentro de `questions`)

| Campo | Tipo | Requerido | Valores permitidos | Descripción |
|---|---|---|---|---|
| `text` | string | Sí | Texto no vacío | Enunciado de la pregunta (soporta Markdown y notación matemática LaTeX). |
| `difficulty` | string | Sí | `"EASY"` \| `"MEDIUM"` \| `"HARD"` | Nivel de dificultad de la pregunta. |
| `stepByStepSolution` | string | No | — | Solución paso a paso (soporta Markdown). Se muestra al alumno en modo TRAINING o al revisar el test. |
| `image` | string | No | Ruta relativa dentro del ZIP | Imagen que acompaña la pregunta, p. ej. `images/q1.png`. Debe existir en el ZIP. |
| `contextRef` | string | No | `ref` de un context del mismo set | Vincula la pregunta a un contexto compartido. El valor debe coincidir exactamente con el `ref` de uno de los contextos del **mismo** set. |
| `answers` | array | Sí | Al menos 2 elementos, con ≥1 correcta | Lista de opciones de respuesta (ver sección 3.5). |

---

### 3.5 Campos de cada `answer` (dentro de `answers`)

| Campo | Tipo | Requerido | Valores permitidos | Descripción |
|---|---|---|---|---|
| `text` | string | Sí | Texto no vacío | Texto de la opción de respuesta. |
| `isCorrect` | boolean | Sí | `true` \| `false` | Indica si esta opción es correcta. Al menos una respuesta del arreglo debe ser `true`. |
| `feedback` | string | No | — | Explicación que se muestra al alumno cuando selecciona esta opción (especialmente útil en modo TRAINING). |

---

## 4. Reglas de validación

La plataforma valida el bundle completo antes de importar. Los errores **se acumulan** y se muestran todos juntos — no se detiene al primero.

- `formatVersion` debe ser exactamente `1`.
- `name` no puede estar vacío.
- `mode`, si está presente, debe ser `"TEST"` o `"TRAINING"`.
- `timeLimitMinutes`, si está presente, debe ser un entero positivo.
- `questionSets` debe tener al menos 1 elemento.
- En cada set:
  - `topic` no puede estar vacío.
  - `questions` debe tener al menos 1 elemento.
  - `questionsToShow` debe ser un entero entre 1 y el número total de preguntas del set (inclusive).
  - Los `ref` de los contextos deben ser únicos dentro del mismo set.
  - El `type` de cada contexto debe ser uno de: `"TEXT"`, `"IMAGE"`, `"CODE"`, `"DATA"`, `"SCENARIO"`, `"OTHER"` (sin distinguir mayúsculas/minúsculas).
- En cada pregunta:
  - `text` no puede estar vacío.
  - `difficulty` debe ser `"EASY"`, `"MEDIUM"` o `"HARD"`.
  - `answers` debe contener al menos 2 opciones, y al menos 1 con `isCorrect: true`.
  - `contextRef`, si está presente, debe coincidir con el `ref` de un contexto del **mismo** set.
- Toda imagen referenciada (en preguntas o contextos) debe existir físicamente en el ZIP y ser de tipo `png`, `jpg`/`jpeg` o `webp`.
- El JSON **no debe incluir `courseId` ni ningún ID interno** de la plataforma. El curso destino se elige en la interfaz al momento de importar.

---

## 5. Ejemplo completo

El siguiente `test.json` incluye dos question sets:

- **Set 1 — "Ecuaciones lineales":** tiene un contexto con imagen y dos preguntas que lo referencian; una de ellas también tiene imagen propia.
- **Set 2 — "Geometría básica":** sin contextos, preguntas simples de opción múltiple.

```json
{
  "formatVersion": 1,
  "name": "Matemáticas Primer Semestre",
  "description": "Evaluación de álgebra y geometría elemental. Leer cada enunciado con atención.",
  "mode": "TEST",
  "timeLimitMinutes": 45,
  "fixedOrder": false,
  "questionSets": [
    {
      "topic": "Ecuaciones lineales",
      "questionsToShow": 2,
      "contexts": [
        {
          "ref": "ctx-sistema",
          "type": "TEXT",
          "title": "Sistema de ecuaciones",
          "description": "Usa este sistema para responder las preguntas siguientes.",
          "content": "Considera el siguiente sistema:\n\n$$2x + y = 7$$\n$$x - y = 2$$",
          "image": "images/ctx1.png"
        }
      ],
      "questions": [
        {
          "text": "¿Cuál es el valor de **x** que satisface el sistema?",
          "difficulty": "MEDIUM",
          "contextRef": "ctx-sistema",
          "stepByStepSolution": "Sumando las dos ecuaciones: 3x = 9, por lo tanto x = 3.",
          "answers": [
            { "text": "x = 3", "isCorrect": true,  "feedback": "Correcto. Sumando ambas ecuaciones obtienes 3x = 9." },
            { "text": "x = 2", "isCorrect": false, "feedback": "Revisa: al sumar las ecuaciones el término en y se cancela." },
            { "text": "x = 5", "isCorrect": false, "feedback": "Ese valor no satisface ninguna de las dos ecuaciones." },
            { "text": "x = 1", "isCorrect": false, "feedback": "Verifica sustituyendo en la primera ecuación." }
          ]
        },
        {
          "text": "¿Cuál es el valor de **y** que satisface el sistema?",
          "difficulty": "MEDIUM",
          "contextRef": "ctx-sistema",
          "image": "images/q1.png",
          "stepByStepSolution": "Con x = 3, sustituye en x - y = 2: 3 - y = 2, entonces y = 1.",
          "answers": [
            { "text": "y = 1", "isCorrect": true,  "feedback": "Correcto. Sustituye x = 3 en cualquiera de las ecuaciones." },
            { "text": "y = 3", "isCorrect": false, "feedback": "Eso sería el valor de x, no de y." },
            { "text": "y = 4", "isCorrect": false, "feedback": "Verifica sustituyendo en la segunda ecuación." },
            { "text": "y = 0", "isCorrect": false, "feedback": "Revisa el despeje de y en x - y = 2." }
          ]
        },
        {
          "text": "¿Qué método se usó para resolver el sistema mostrado?",
          "difficulty": "EASY",
          "contextRef": "ctx-sistema",
          "answers": [
            { "text": "Suma o eliminación", "isCorrect": true,  "feedback": "Correcto. Se sumaron ambas ecuaciones para eliminar y." },
            { "text": "Sustitución",         "isCorrect": false, "feedback": "La sustitución también es válida, pero no fue el método descrito." },
            { "text": "Igualación",          "isCorrect": false, "feedback": "La igualación requiere despejar la misma variable en ambas ecuaciones primero." }
          ]
        }
      ]
    },
    {
      "topic": "Geometría básica",
      "questionsToShow": 2,
      "questions": [
        {
          "text": "¿Cuánto mide el área de un triángulo rectángulo con catetos de 6 cm y 8 cm?",
          "difficulty": "EASY",
          "stepByStepSolution": "Área = (base × altura) / 2 = (6 × 8) / 2 = 24 cm².",
          "answers": [
            { "text": "24 cm²", "isCorrect": true,  "feedback": "Correcto. Área = (6 × 8) / 2 = 24." },
            { "text": "48 cm²", "isCorrect": false, "feedback": "Eso sería base × altura sin dividir entre 2." },
            { "text": "14 cm²", "isCorrect": false, "feedback": "Ese valor corresponde a la suma de los catetos, no al área." },
            { "text": "10 cm²", "isCorrect": false, "feedback": "Revisa la fórmula del área del triángulo." }
          ]
        },
        {
          "text": "¿Cuál es la longitud de la hipotenusa de un triángulo rectángulo con catetos de 3 cm y 4 cm?",
          "difficulty": "EASY",
          "stepByStepSolution": "Teorema de Pitágoras: c² = 3² + 4² = 9 + 16 = 25, entonces c = 5 cm.",
          "answers": [
            { "text": "5 cm",   "isCorrect": true,  "feedback": "Correcto. √(9 + 16) = √25 = 5." },
            { "text": "7 cm",   "isCorrect": false, "feedback": "Eso sería la suma de los catetos, no la hipotenusa." },
            { "text": "12 cm",  "isCorrect": false, "feedback": "Verifica aplicando el teorema de Pitágoras." },
            { "text": "3.5 cm", "isCorrect": false, "feedback": "Revisa el cálculo: c = √(a² + b²)." }
          ]
        },
        {
          "text": "Un cuadrado tiene un lado de 5 cm. ¿Cuánto mide su perímetro?",
          "difficulty": "EASY",
          "answers": [
            { "text": "20 cm", "isCorrect": true,  "feedback": "Correcto. Perímetro = 4 × lado = 4 × 5 = 20 cm." },
            { "text": "25 cm", "isCorrect": false, "feedback": "Eso sería el área, no el perímetro." },
            { "text": "10 cm", "isCorrect": false, "feedback": "10 cm sería la suma de solo dos lados." },
            { "text": "15 cm", "isCorrect": false, "feedback": "Un cuadrado tiene 4 lados iguales, no 3." }
          ]
        }
      ]
    }
  ]
}
```

### Estructura del ZIP correspondiente

```
test.zip
├── test.json
└── images/
    ├── ctx1.png    ← imagen del contexto "ctx-sistema"
    └── q1.png      ← imagen de la segunda pregunta del primer set
```

---

## 6. Añadir preguntas a un test existente

Además de crear un test nuevo, puede **cargar más preguntas a un test ya existente** subiendo otro `.zip` **con el mismo formato** descrito arriba. No hay un formato aparte: se reutiliza `test.json` + `images/`.

La diferencia está en cómo se interpreta el bundle al añadir:

- Los campos de **nivel test** (`name`, `description`, `mode`, `timeLimitMinutes`, `fixedOrder`) se **ignoran**: el test destino ya los define. Puede dejarlos o quitarlos; solo se leen `formatVersion` y `questionSets`.
- Las preguntas añadidas quedan **al final** de su conjunto, en el orden del archivo.
- **No se envía `courseId`**: el curso se toma del test destino.
- Para cada `questionSet`:
  - Si el `topic` **ya existe** en el test, las preguntas se **agregan al pool** de ese conjunto (es el caso típico "cargar más preguntas").
  - Si el `topic` **no existe**, se crea un conjunto nuevo dentro del test.
  - `questionsToShow` es **opcional** al añadir:
    - Si lo incluye, **sobrescribe** cuántas preguntas se muestran en ese conjunto.
    - Si lo omite, se **conserva** el valor actual del conjunto (y en un conjunto nuevo, sin valor, se muestran todas las preguntas cargadas).
- El límite de preguntas por topic se valida contando **lo ya existente + lo que trae el bundle**.

El resto de las reglas de validación (dificultad, respuestas, contextos, imágenes, etc.) son idénticas a las de la sección 4.

> **Nota:** el test no se degrada ni se despublica durante la carga; las preguntas nuevas se agregan en segundo plano y el test sigue disponible. Si la carga falla, el test queda intacto (no se añade nada parcial).

---

## 7. Exportar un test

En el editor, **Descargar** (`GET /test/{id}/export`) entrega un `.zip` con este mismo formato: `test.json` (con `fixedOrder`, los contextos con `ref` del tipo `ctx-1`, `ctx-2`… y las preguntas en el orden del editor) más las imágenes de preguntas y contextos en `images/`. Ese archivo puede importarse tal cual para copiar el test a otro curso. Los conjuntos sin preguntas no se exportan.

---

## Preguntas frecuentes

**¿Puedo incluir más preguntas que `questionsToShow`?**
Sí, y es recomendable. Si un set tiene 10 preguntas y `questionsToShow` es 5, cada alumno verá 5 preguntas seleccionadas aleatoriamente del pool. Esto reduce la posibilidad de que los alumnos compartan las mismas preguntas.

**¿Puedo tener varias preguntas correctas en `answers`?**
La plataforma requiere al menos 1 respuesta correcta, pero acepta más de una. Sin embargo, el flujo de evaluación actual trata el test como selección única — se recomienda dejar exactamente 1 opción correcta por pregunta.

**¿Qué pasa si el `topic` ya existe en el curso destino?**
Se reutiliza automáticamente. Las preguntas importadas se agregan al pool de ese topic.

**¿Puedo importar el mismo bundle en varios cursos?**
Sí. El bundle no contiene ningún ID de curso; puedes importarlo tantas veces como quieras en distintos cursos.

**¿Qué sucede si hay errores en el bundle?**
La importación se cancela por completo y la plataforma muestra la lista de todos los errores encontrados. No se crea ningún registro parcial.
