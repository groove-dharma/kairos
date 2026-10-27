<a id="top"></a>
<div align="center">

# Kairós
**Una exoprótesis cognitiva para mentes neuroatípicas.**

Un sistema diseñado para sacar el proceso de la cognición fuera de la cabeza con privacidad de confianza cero, proporcionando un apoyo funcional estable para facultades cognitivas que pueden ser intermitentes o estar deterioradas. Al trasladar la carga de la organización, la continuidad y la verificación a un sustrato externo, Kairós permite al usuario ejercer su inteligencia existente sin la fricción de la disfunción ejecutiva u otros impedimentos.

[**Implementación técnica**](#implementación-técnica) · [**Ecosistema**](#ecosistema) · [**Ingeniería de contexto**](#ingeniería-de-contexto) · [**Mapa del repositorio**](#mapa-del-repositorio) · [**Instalación**](#instalación) · [**Hoja de ruta**](#estado-hoja-de-ruta-y-lo-que-no-está-probado)

</div>

---

## Qué es esto

Kairós no es un asistente; es una prótesis cognitiva. Mientras que un asistente te ayuda a realizar una tarea concreta, una prótesis apoya funcionalmente una facultad.

El sistema transforma las capacidades genéricas de los LLM en «cognición artificial». No pretende aumentar la inteligencia innata ni añadir nuevas capacidades. En cambio, se centra en eliminar la fricción causada por las lagunas cognitivas (como la disfunción ejecutiva), permitiendo al usuario operar a la altura de su potencial intelectual real con un nivel de estabilidad y consistencia que de otro modo sería inalcanzable.

Al transformar la naturaleza fragmentada del pensamiento neuroatípico en un activo recuperable, conectable y falsable (en el sentido popperiano), Kairós garantiza que el resultado del pensamiento ya no depende de la estabilidad del momento, sino de la robustez del sistema.

## El problema

La mayoría de las herramientas de apoyo cognitivo caen en una de cuatro categorías, cada una con un defecto fundamental que le impide funcionar como una verdadera prótesis:

**Los asistentes conversacionales** están optimizados para la conformidad. Debido a su adulación inherente, tienden a validar las premisas del usuario en lugar de cuestionarlas. Esto crea un bucle de retroalimentación que puede amplificar sesgos o reforzar errores en lugar de corregirlos.

**Las aplicaciones de notas** son almacenamiento pasivo. Destacan en capturar y organizar datos, pero no razonan sobre ellos. Proporcionan un archivo consultable, pero no pueden conectar un pensamiento actual con un patrón de hace dos años ni alertar al usuario de una contradicción lógica en su propio razonamiento.

**La terapia y el apoyo clínico** aportan criterio y perspectiva externa fundamentales, pero a menudo carecen de los datos brutos. Estos procesos suelen depender de la memoria retrospectiva del usuario, que es frágil y está sujeta a sesgos, especialmente en perfiles neuroatípicos.

**La vulnerabilidad de los datos** está integrada en la mayoría de los sistemas avanzados de IA: los usuarios deben entregar sus procesos cognitivos más íntimos a proveedores opacos de la nube. Para una prótesis cognitiva, esto es inaceptable: no se puede confiar en un «mapa mental» si el sustrato sobre el que vive no es privado. Esto crea una tensión en la que el usuario debe elegir entre potencia cognitiva y seguridad epistémica.

**Kairós** aborda esta brecha combinando la potencia de razonamiento de la IA con una memoria privada, gobernada y versionada del usuario. Traslada el proceso de «recordar e informar» a «analizar un registro verificable», garantizando que el apoyo se basa en evidencia y no en el recuerdo.

## Implementación técnica
<div align="right">

[↑ Volver arriba](#top)

</div>

Kairós es un orquestador de herramientas especializadas. En lugar de reimplementar primitivas de bajo nivel, integra tecnologías consolidadas para ejecutar un bucle cognitivo de cuatro etapas: **Captura $\rightarrow$ Organización $\rightarrow$ Síntesis $\rightarrow$ Falsación**.

**Captura** es el punto de entrada. Recoge material en bruto a partir de dictados de voz y lecturas anotadas. Esta etapa se apoya en [Tinfoil.sh](https://tinfoil.sh) para la computación confidencial, asegurando que la transcripción se produce dentro de enclaves seguros para garantizar una privacidad total. [Syncthing](https://syncthing.net) se utiliza para sincronizar estas grabaciones y los archivos sidecar entre dispositivos sin un proveedor centralizado de nube.

**Organización** transforma los datos en bruto en un activo recuperable. Esta etapa funciona con [Fortémi](https://github.com/fortemi/fortemi) y [Elasticsearch](https://www.elastic.co/elasticsearch), lo que permite búsqueda híbrida (léxica BM25 y semántica vectorial) y una organización gobernada del conocimiento mediante jerarquías SKOS. Garantiza que la información se estructura para su recuperación y no solo se almacena.

**Síntesis** es el proceso de convertir un gran volumen de datos organizados en hipótesis accionables. Es un módulo que utiliza [GTFlow](https://github.com/zw-zhtlab/GTFlow) para implementar las etapas de la Teoría Fundamentada y [AG2](https://github.com/ag2ai/ag2) para escenificar debates adversariales entre agentes. Esta tensión dialéctica permite al sistema detectar patrones y proponer mecanismos que los expliquen.

**Falsación** es la etapa final y la más crítica. Somete las hipótesis generadas a pruebas rigurosas contra nueva evidencia y al escrutinio lógico. Este proceso se materializa mediante la interacción entre el usuario y un LLM gobernado por el prompt del sistema de Kairós, que impone un protocolo dialéctico de cuestionamiento y verificación. Una hipótesis solo pasa a formar parte del conocimiento estable del usuario (Codex) una vez que ha resistido a su mejor antagonista.

En el funcionamiento diario, el sistema se centra en **Captura, Organización y Falsación**. La síntesis es un módulo periódico y opcional, ya que requiere un volumen de corpus considerable para producir resultados significativos.

## Ecosistema
<div align="right">

[↑ Volver arriba](#top)

</div>

Más allá del repositorio principal, Kairós utiliza herramientas complementarias para optimizar el entorno de producción:

- [**`tinfoil-proxy-stack`**](https://github.com/groove-dharma/tinfoil-proxy-stack): Un proxy no oficial de capa 7 que permite a una red de contenedores internos compartir una única identidad atestiguada. Implementa la agrupación por lotes de embeddings para reducir la latencia de la API y optimizar el rendimiento.
- [**`kairos-tts-koplugin`**](https://github.com/groove-dharma/kairos-tts-koplugin): Un fork que integra [norude/TTS.koplugin](https://github.com/norude/TTS.koplugin) y [stradichenko/audiobook.koplugin](https://github.com/stradichenko/audiobook.koplugin) más un servidor local de [Piper](https://github.com/OHF-Voice/piper1-gpl). Esto permite escuchar libros con una voz neuronal que nunca sale de la red local, extendiendo la funcionalidad a dispositivos e-reader más antiguos.

## Ingeniería de contexto
<div align="right">

[↑ Volver arriba](#top)

</div>

Kairós gestiona la enorme cantidad de datos personales mediante dos capas distintas de ingeniería de contexto. Esto garantiza que el sistema siga siendo consciente de la identidad y la historia del usuario sin sobrecargar la memoria inmediata del modelo.

### El Codex

El Codex es un mapa vivo y versionado de la topografía cognitiva del usuario, que incluye sus propios modelos teóricos, axiomas, protocolos y decisiones. Cumple dos funciones principales: le dice al sistema con quién está hablando (qué se ha decidido, qué permanece cerrado y qué está pendiente) y calibra cómo habla el sistema.

El Codex se rige por el Protocolo Delta. Cada cambio se propone como un delta atómico: se aplica íntegramente o no se aplica. Para mantener un rastro de auditoría completo, nunca se borra nada; cada modificación se versiona. Dado que el Codex es un estado derivado, puede reconstruirse por completo a partir de la secuencia de deltas aplicados, garantizando que el mapa de la mente del usuario es transparente y verificable.

### La Caja Negra

La Caja Negra es un método para la compresión casi sin pérdida de sesiones extensas. En lugar de depender de un historial truncado o de resúmenes con pérdida, el sistema comprime las conversaciones en un registro estructurado que preserva la forma lógica y la evidencia de la sesión.

Actualmente, el sistema genera una única caja completa para cada sesión. La arquitectura está diseñada para escalar hacia una secuencia de cajas encadenadas, donde la caja $N+1$ comienza exactamente donde terminó la caja $N$, garantizando que no haya huecos ni solapamientos en el registro.

Cada caja sirve como índice de alta densidad de la conversación. Al capturar las premisas iniciales, la ruta dialéctica (la secuencia de hipótesis y sus falsaciones) y los resultados accionables finales, la Caja Negra permite al sistema recuperar el estado cognitivo exacto de una sesión anterior sin reprocesar la transcripción bruta completa.

## Mapa del repositorio
<div align="right">

[↑ Volver arriba](#top)

</div>

El proyecto está organizado en componentes modulares para separar la lógica cognitiva de la infraestructura subyacente.

- **[`prompt/`](prompt/)** Prompts del sistema en español (fuente), inglés y francés. Versión actual: v7.5.4. Este es el corazón de Kairós.

- **[`capture/daemon`](capture/daemon)** La etapa de captura. Vigila una carpeta sincronizada (con Syncthing) de archivos de audio (grabaciones de voz), los transcribe a través de los enclaves seguros y privados de Tinfoil.sh y los traslada al corpus (datos del usuario). En el lado de la lectura, analiza los archivos sidecar de [KOReader](https://github.com/koreader/koreader), asocia cada grabación con el subrayado al que pertenece y escribe la transcripción de vuelta como una nota en ese subrayado.

- **[`synthesize/elenchos`](synthesize/elenchos)**
 El módulo opcional de síntesis. Muestrea el corpus por período, ejecuta las etapas de la Teoría Fundamentada a través de GTFlow, escenifica un debate entre dos agentes con sesgos opuestos y materializa las hipótesis supervivientes en el corpus con un parser determinista.

- **[`falsify/daemon-delta`](falsify/daemon-delta)**
 Escribe el Codex. Valida un delta como un todo antes de escribir nada, lo aplica y regenera la vista legible por humanos. No se borra nada y se conserva cada versión.

- **[`falsify/black-boxer`](falsify/black-boxer)**
 Genera las cajas de sesión. Toma una conversación, la comprime al formato de caja y escribe el resultado como la versión procesada de esa nota.

- **[`falsify/querier`](falsify/querier)**
 La ruta de lectura sobre el corpus. Búsqueda híbrida, hidratación de resultados y detección de conversaciones de IA que no tienen Caja Negra.

- **[`infra/stt-proxy`](infra/stt-proxy)**
 Un proxy ligero delante de los modelos de voz servidos por Tinfoil.sh. Voxtral primero, Whisper como respaldo.

- **[`infra/searxng`](infra/searxng)**
 Una instancia de metabúsqueda autoalojada, utilizada por los agentes de debate cuando una afirmación necesita fundamentación fuera del corpus.

- **[`fortemi/`](fortemi/)**
 La capa de integración para el motor de organización actual: un override de docker compose, dos parches que la versión fijada necesita para funcionar correctamente, y un NOTICE que declara la procedencia y los términos del código de terceros subyacente.

- **[`tools/_deprecated/onboarding`](tools/_deprecated/onboarding)**
La entrevista de primera ejecución que solía producir un Codex inicial. Obsoleta y en proceso de rediseño.

## Instalación
<div align="right">

[↑ Volver arriba](#top)

</div>

Por el momento, Kairós es un sistema operado manualmente y puede ejecutarse en hardware convencional: cualquier máquina que ejecute Docker, sin necesidad de GPU. Linux y WSL2 son las rutas probadas. También necesitas una cuenta de [Tinfoil](https://tinfoil.sh): el plan Chat aloja la etapa de falsación, y una clave de API alimenta a los daemons ([guía de inicio rápido](https://docs.tinfoil.sh/quickstart)).

### 1. El motor de organización

El stack está calibrado contra Fortémi v2026.7.1. Clona esa etiqueta exacta y aplica los parches locales:

```bash
git clone --branch v2026.7.1 --depth 1 https://github.com/fortemi/fortemi.git
cd fortemi
git apply /path/to/kairos/fortemi/patches/local-fixes.patch
docker compose -f docker-compose.workstation.yml up -d
```

El parche corrige un error de deduplicación de trabajos y un error de vinculación de parámetros presentes en esa versión. No sigas upstream: los archivos modificados han sido reescritos desde entonces en versiones posteriores y el parche no se aplicará.

### 2. Inicialización de la base de datos

Una vez que el motor esté en marcha, ejecuta el script de inicialización una vez:

```bash
bash /path/to/kairos/fortemi/patches/init-fortemi-fix.sh
```

Realiza tres operaciones que el motor no hace por sí mismo: permisos de almacenamiento, la función SQL `gen_uuid_v7()` que llama el trigger de embeddings, y el índice HNSW para la búsqueda semántica. Sin él, las notas nuevas nunca se embeben y la búsqueda semántica falla silenciosamente. Vuelve a ejecutarlo después de cualquier borrado de volúmenes.

### 3. La capa Kairós

Clona este repositorio junto al checkout de Fortémi y conecta ambos mediante el override de compose:

```bash
cp kairos/fortemi/docker-compose.override.yml fortemi/
cp kairos/fortemi/.env.example fortemi/.env   # define tu clave de API de Tinfoil y las rutas del host
cd fortemi
docker compose -f docker-compose.workstation.yml -f docker-compose.override.yml \
  --profile querier --profile black-boxer up -d --build
```

La primera ejecución construye las imágenes de Kairós. Elasticsearch y daemon-delta se inician incondicionalmente; el querier y el black-boxer están detrás de sus perfiles. Antes del primer delta del codex, ejecuta el comando `init` de daemon-delta una vez (ver `falsify/daemon-delta`): instala la plantilla de índice que heredará cada índice del codex.

### 4. La interfaz de falsación

Hoy la cuarta etapa se ejecuta en [Tinfoil Chat](https://tinfoil.sh/chat), impulsada por el prompt del sistema en `prompt/` (fuente en español, versiones en inglés y francés, versión actual v7.5.4).

- Instala el prompt como tu prompt del sistema predeterminado en Settings → Prompts. La precedencia, los valores predefinidos y los marcadores de posición están documentados en la [configuración del chat](https://docs.tinfoil.sh/chat/settings).
- Crea un proyecto y añade las vistas del codex (los archivos `.md` que daemon-delta escribe en /out) a su área de documentos. Cada chat iniciado desde el proyecto lee ese mapa. Los proyectos requieren sincronización en la nube y un plan Premium, y sus contenidos se cifran bajo tu clave ([proyectos](https://docs.tinfoil.sh/chat/features), [sincronización en la nube](https://docs.tinfoil.sh/chat/cloud-sync)).
- El codex empieza vacío. Se llena con el uso: las conversaciones producen decisiones, las decisiones se emiten como deltas, el daemon los aplica y los archivos `.md` regenerados sustituyen a los antiguos en el proyecto.

### 5. Captura de voz

El daemon de captura vigila una carpeta. Elige una carpeta que reciba tus notas de voz y sincronízala desde dondequiera que grabes; puedes usar Syncthing para ello. Cómo grabas en ella es elección tuya. El único requisito del protocolo: los nombres de archivo deben llevar la marca de tiempo de la grabación en un patrón ordenable (estilo `2026-10-24_14-32-00`), porque el daemon deriva la marca de tiempo de la nota primero del nombre; si no sigue ese patrón de nomenclatura, la marca de tiempo provendrá de la del propio archivo. Apunta el daemon a la carpeta mediante sus variables de entorno y recogerá las nuevas grabaciones por sí solo.

### 6. Lectura anotada (opcional)

Los subrayados de lectura viven en archivos sidecar de KOReader junto a cada libro. Sincroniza la carpeta que contiene los libros y los sidecars con el mismo mecanismo y apunta el daemon a ella: analiza los sidecars, asocia cada grabación con el subrayado al que pertenece y escribe la transcripción de vuelta como una nota en ese subrayado. El dispositivo y el transporte son elección tuya.

## Estado, hoja de ruta y lo que no está probado
<div align="right">

[↑ Volver arriba](#top)

</div>

Kairós es actualmente un prototipo operativo. Ha sido desarrollado y validado como una prótesis cognitiva personal, lo que significa que el sistema es plenamente funcional pero está optimizado para el perfil cognitivo y la infraestructura específicos del autor.

### Estado actual
El ciclo central (Captura $\rightarrow$ Organización $\rightarrow$ Síntesis $\rightarrow$ Falsación) está completo y activo. El sistema transforma con éxito el pensamiento neuroatípico en bruto en una base de conocimiento gobernada y versionada. Todos los componentes descritos en el Mapa del repositorio están integrados y produciendo resultados en un entorno personal.

### Hoja de ruta
El desarrollo de Kairós avanza hacia la reducción de la fricción manual y el aumento de la flexibilidad de despliegue:

- **Refactorización a Kubernetes**: Migración del stack a una arquitectura nativa de k8s. Esto llevará el sistema de una instalación manual a un despliegue profesional y escalable.
- **Kairós automático**: Implementación de un pipeline de ingeniería de contexto totalmente automatizado. Esto incluye la generación automática de las Cajas Negras de sesión y la inyección fluida del Codex en el contexto del LLM sin manipulación manual de archivos.
- **Migración completa al inglés**: Traducción de la base de código interna, los comentarios y los prompts al inglés para facilitar una colaboración académica y técnica más amplia.

### Lo que no está probado
En aras de la honestidad técnica, se reconocen los siguientes límites:

- **Instalación autónoma**: Aunque la instalación está documentada, no se ha verificado una instalación «en un clic» para terceros. El despliegue actual es un proceso dirigido manualmente.
- **Generalizabilidad**: El sistema ha sido validado en un único perfil neuroatípico complejo (el autor). Aunque la arquitectura está diseñada para adaptarse a cualquier usuario, la eficacia del proceso en perfiles cognitivos diversos sigue siendo un área abierta de investigación.
- **Escala de producción**: El sistema está diseñado para una experiencia de «exocórtex» de un solo usuario. Todavía no se ha desarrollado ni probado ningún diseño multiusuario.
