Aquí tienes la traducción exacta de tu versión editada al español:

# Kairós Framework

### Prótesis Cognitiva y Entorno de Saneamiento Dialéctico

> **Estado:** Operativo / Desarrollo Activo
> **Plataforma Nativa:** Ecosistema Google Gemini
> **Licencia:** CC BY-NC-SA 4.0 (No Comercial).
> **Uso Corporativo:** Ver `COMMERCIAL_ACCESS.md`.

---

## 1. Introducción: El Concepto

En la retórica antigua, los griegos distinguían dos formas de tiempo: *Chronos* (tiempo secuencial, cuantitativo) y **Kairós** (el momento oportuno, el instante crítico de intervención).

Este framework no es un asistente de productividad diseñado para ahorrarte *Chronos*. Es una intervención dialéctica diseñada para el *Kairós*: ese instante crítico en el que un proceso de pensamiento está a punto de sucumbir a una falacia, un sesgo cognitivo o una ilusión reconfortante.

**Kairós es una arquitectura de System Prompt** que altera fundamentalmente el comportamiento de los Grandes Modelos de Lenguaje (LLMs). Elimina la persona de "asistente útil" —que tiende a la sicofancia y al acuerdo— y la reemplaza por un **Sparring Cognitivo**. Su función no es complacer al usuario, sino maximizar la densidad lógica y la integridad del proceso de pensamiento del mismo.

---

## 2. Arquitectura Teórica

Kairós opera bajo una lógica de "Alta Fricción". Asume que el pensamiento riguroso requiere resistencia, no validación. El framework se basa en varios pilares clave de la ciencia cognitiva y la filosofía aplicada:

* **Prompting Hermenéutico:** A diferencia de la generación estándar "Input -> Output", Kairós trata la conversación como un círculo hermenéutico, donde la comprensión se refina a través de la deconstrucción y la crítica recursiva.
* **Colaboración Adversaria (Red Teaming):** El sistema tiene el mandato de actuar como un "Abogado del Diablo" benévolo (falsacionismo). Valida las ideas solo después de intentar desmantelarlas.
* **Andamiaje Cognitivo (La Prótesis):** Actúa como una función ejecutiva externa, diseñada específicamente para sostener hilos lógicos complejos que de otro modo podrían disolverse debido a la fatiga, la neurodivergencia (TDAH/TEA) o la carga cognitiva.
* **Isomorfismo Conceptual (El Bibliotecario):** El sistema escanea el input del usuario en busca de "intuiciones crudas" y las mapea a marcos académicos, filosóficos o científicos establecidos, salvando la brecha entre la "calle" y la nomenclatura formal.
* **Distanciamiento Metacognitivo:** Kairós fuerza una separación entre el *pensador* y el *pensamiento*, tratando las ideas como objetos externos para ser auditados en lugar de marcadores de identidad para ser defendidos.

---

## 3. Anatomía del Sistema

El prompt de Kairós no es una instrucción única, sino un sistema operativo modular.

### El Códex (Axiomas del Usuario)

Este es el núcleo mutable del sistema. Contiene el perfil psicológico específico del usuario, sus valores no negociables y sus verdades establecidas. Kairós usa esto para adaptar su lógica al "Hardware" específico del usuario.

### Las Leyes del Kernel (Aequitas)

Reglas inviolables que anulan todas las demás instrucciones.

* **Veracidad Activa:** Los datos de entrenamiento se tratan como legado; los datos de búsqueda actuales se tratan como la verdad.
* **Dignidad Humana:** La crítica se limita a las ideas, nunca al individuo (a menos que se solicite expresamente).
* **Anti-Bucle:** Intervención obligatoria cuando se detectan lógicas circulares o bucles depresivos.

### Los Demonios (Procesos en Segundo Plano)

Scripts de monitoreo silencioso que se ejecutan en paralelo a la conversación:

* **Escudo Anti-Sicofancia:** Intercepta la adulación y el acuerdo.
* **Interruptor de Deriva Narrativa:** Detiene la "narración de historias" para volver a los hechos.
* **Monitor de Entropía:** Detecta cuando la conversación está perdiendo coherencia lógica.

### La Caja Negra (Protocolo de Persistencia)

Kairós incluye un mecanismo de "Guardado de Estado" llamado **DRS (Estado de Recuperación Dialéctica)**. Permite al sistema generar una compresión lógica sin pérdidas de una sesión, permitiendo al usuario "reiniciar" una conversación en una nueva ventana de contexto sin perder la arquitectura lógica o los axiomas establecidos.

---

## 4. Despliegue y Compatibilidad

Kairós está optimizado para la arquitectura de Google Gemini debido a su manejo superior del contexto y su adherencia a las instrucciones.

### Configuraciones Recomendadas

| Rama de Versión | Entorno Objetivo | Mejor Para |
| --- | --- | --- |
| **Kairós v2.8** | **Google AI Studio** (Gemini 2.5 pro) | Trabajo estructural profundo, desarrollo de prompts y procesamiento lógico puro. Ofrece el nivel más alto de control. |
| **Kairós v3.0** | **Gemini Advanced** (Gemini 3 / Gems) | Uso diario integrado. Ideal para desplegar Kairós como un "Gem" persistente dentro de la interfaz estándar de Google. |

> **Nota sobre el Comportamiento del Modelo:** Los intentos de ejecutar Kairós en modelos con altos disparadores de "Seguridad/Rechazo" (ej. ciertas iteraciones de GPT-4o o Claude 3.5 Sonnet) pueden resultar en que el modelo rechace la instrucción de la persona "Amoral/Clínica". El prompt está afinado específicamente para el motor de razonamiento de Gemini. Pero habrá pruebas en el futuro para adaptar el Framework Kairós a otros LLMs.

---

## 5. Instalación y Onboarding

Kairós no es "Plug & Play". Requiere un marco de referencia para funcionar eficazmente.

### Fase 1: Generación del Códex

No puedes ejecutar el sistema principal sin un **Códex**.

1. Navega al directorio `/tools/onboarding`.
2. Copia el prompt del **Agente de Onboarding**.
3. Ejecuta este agente en una sesión de chat temporal.
4. El agente te entrevistará para mapear tu estilo cognitivo, sesgos y axiomas.
5. **Output:** Generará un bloque de código etiquetado como `USER_CODEX`.

### Fase 2: Inyección del Sistema

1. Abre el archivo `KAIROS_SYSTEM_PROMPT` crudo (selecciona la versión que coincida con tu entorno).
2. Localiza la etiqueta `<USER_CODEX>` dentro de la estructura XML.
3. Pega el bloque de código generado en la Fase 1 en esta sección.
4. Pega el prompt resultante completo en el campo de "Instrucciones del Sistema" de tu LLM.

---

## 6. Guías de Uso

### La Regla "Zero-Shot"

No inicies una sesión con "Hola". Comienza con datos. Kairós está inicializado para omitir cortesías e involucrarse inmediatamente con la primera premisa proporcionada. **Pro-Tip:** Puedes decir "recuerda bootear" ("remember to boot") al inicio de la conversación para asegurarte de que el LLM ha integrado el framework.

### La Etiqueta de "Isomorfismo"

Cuando quieras que Kairós encuentre el equivalente académico de tu pensamiento, pide explícitamente una "Auditoría Isomórfica". Buscará en la historia de la filosofía y la ciencia para encontrar el gemelo estructural de tu idea. **Disclaimer:** Kairós hará esto incluso si no se lo pides, lo cual puede ser útil para el proceso de pensamiento, pero si quieres que una idea específica sea auditada, dilo claramente.

### El Comando Caja Negra

Cuando la ventana de contexto se llene, o desees terminar una sesión, ordena: **"Generar Caja Negra"**. Guarda el resultado. Esta es tu semilla para la siguiente sesión.

---

## 7. Licencia y Descargos

**Creative Commons BY-NC-SA 4.0**

* **Gratis para:** Uso personal, investigación académica, adaptación de código abierto.
* **Comercial:** El despliegue corporativo, consultoría pagada utilizando este framework o integración propietaria requiere una licencia comercial. Ver `COMMERCIAL_ACCESS.md`.

**Descargo de Responsabilidad (Disclaimer):**
Kairós es una herramienta para la lógica, no un profesional de la salud mental. Aunque contiene salvaguardas (La Ley Anti-Nihilismo), está diseñado para despojar de ilusiones reconfortantes. Úsese con discreción y responsabilidad. El usuario asume toda la responsabilidad por la interpretación del output del sistema.