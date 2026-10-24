<a id="top"></a>
<div align="center">

# Kairós
**A cognitive exo-prosthesis for neuroatypical minds.**

A system designed to move the process of cognition outside the head with zero-trust privacy, providing stable functional support for cognitive faculties that may be intermittent or impaired. By shifting the burden of organization, continuity, and verification to an external substrate, Kairós allows the user to exercise their existing intelligence without the friction of executive dysfunction or other impairments.

[**Technical Implementation**](#technical-implementation) · [**Ecosystem**](#ecosystem) · [**Context Engineering**](#context-engineering) · [**Repository Map**](#repository-map) · [**Setup**](#setup) · [**Roadmap**](#status-roadmap-and-what-is-not-proven)

</div>

---

## What this is

Kairós is not an assistant; it is a cognitive prosthesis. While an assistant helps you perform a specific task, a prosthesis functionally supports a faculty. 

The system transforms generic LLM capabilities to "artificial cognition". It does not aim to increase innate intelligence or add new capacities. Instead, it focuses on removing the friction caused by cognitive gaps (such as executive dysfunction) allowing the user to operate at their actual intellectual potential with a level of stability and consistency that would otherwise be unattainable.

By transforming the fragmented nature of neuroatypical thought into a recoverable, connectable, and falsifiable asset (in the Popperian sense), Kairós ensures that the result of thinking is no longer dependent on the stability of the moment, but on the robustness of the system.

## The problem

Most cognitive support tools fall into one of four categories, each with a fundamental flaw that prevents them from functioning as a true prosthesis:

**Conversational assistants** are optimized for agreement. Due to inherent sycophancy, they tend to validate the user's premises rather than challenge them. This creates a feedback loop that can amplify biases or reinforce errors instead of correcting them.

**Note-taking applications** are passive storage. They excel at capturing and organizing data, but they do not reason over it. They provide a searchable archive, but they cannot connect a current thought to a pattern from two years ago or alert the user to a logical contradiction in their own reasoning.

**Therapy and clinical support** provide critical criteria and external perspective, but they often lack the raw data. These processes typically rely on the user's retrospective memory, which is fragile and subject to bias, especially in neuroatypical profiles.

**Data vulnerability** is baked into most advanced AI systems: users must surrender their most intimate cognitive processes to opaque cloud providers. For a cognitive prosthesis, this is unacceptable: a "mental map" cannot be trusted if the substrate it lives on is not private. This creates a tension where the user must choose between cognitive power and epistemic security.

**Kairós** addresses this gap by combining the reasoning power of AI with a private, governed and versioned memory of the user. It moves the process from "remembering and reporting" to "analyzing a verifiable record," ensuring that the support is based on evidence rather than recollection.

## Technical Implementation
<div align="right">

[↑ Return to top](#top)

</div>

Kairós is an orchestrator of specialized tools. Rather than reimplementing low-level primitives, it integrates established technologies to execute a four-stage cognitive loop: **Capture $\rightarrow$ Organize $\rightarrow$ Synthesize $\rightarrow$ Falsify**.

**Capture** is the entry point. It gathers raw material from voice dictations and annotated reading. This stage relies on [Tinfoil.sh](https://tinfoil.sh) for confidential computing, ensuring that transcription occurs within secure enclaves to guarantee total privacy. [Syncthing](https://syncthing.net) is used to synchronize these recordings and sidecar files across devices without a central cloud provider.

**Organize** transforms raw data into a recoverable asset. This stage is powered by [Fortémi](https://github.com/fortemi/fortemi) and [Elasticsearch](https://www.elastic.co/elasticsearch), enabling hybrid search (lexical BM25 and semantic vector search) and a governed organization of knowledge using SKOS hierarchies. It ensures that information is structured for retrieval rather than just stored.

**Synthesize** is the process of turning a large volume of organized data into actionable hypotheses. It is a module that uses [GTFlow](https://github.com/zw-zhtlab/GTFlow) to implement the stages of Grounded Theory and [AG2](https://github.com/ag2ai/ag2) to stage adversarial debates between agents. This dialectical tension allows the system to detect patterns and propose mechanisms to explain them.

**Falsify** is the final and most critical stage. It subjects the generated hypotheses to rigorous testing against new evidence and logical scrutiny. This process is materialized through the interaction between the user and an LLM governed by the Kairós system prompt, which enforces a dialectical protocol of challenge and verification. A hypothesis only becomes part of the user's stable knowledge (Codex) once it has resisted its best antagonist.

In daily operation, the system focuses on **Capture, Organize, and Falsify**. Synthesis is a periodic and optional module, as it requires a significant corpus volume to produce meaningful insights.

## Ecosystem
<div align="right">

[↑ Return to top](#top)

</div>

Beyond the main repository, Kairós utilizes complementary tools to optimize the production environment:

- **`tinfoil-proxy-stack`**: An unofficial Layer 7 proxy that allows a network of internal containers to share a single attested identity. It implements embedding batching to reduce API latency and optimize throughput.
- **`kairos-tts-koplugin`**: A fork integrating [norude/TTS.koplugin](https://github.com/norude/TTS.koplugin) and [stradichenko/audiobook.koplugin](https://github.com/stradichenko/audiobook.koplugin) plus a local [Piper](https://github.com/OHF-Voice/piper1-gpl) server. This allows books to be listened to with a neural voice that never leaves the local network, extending functionality to older e-reader devices.

## Context Engineering
<div align="right">

[↑ Return to top](#top)

</div>

Kairós manages the vast amount of personal data through two distinct layers of context engineering. This ensures that the system remains aware of the user's identity and history without overloading the model's immediate memory.

### The Codex

The Codex is a living, versioned map of the user's cognitive topography, including their own theoretic models, axioms, protocols, and decisions. It serves two primary functions: it tells the system who it is talking to (what has been decided, what stays closed, and what is pending) and it calibrates how the system speaks.

The Codex is governed by the Delta Protocol. Every change is proposed as an atomic delta: it is applied in its entirety or not at all. To maintain a complete audit trail, nothing is ever deleted; every modification is versioned. Because the Codex is a derived state, it can be fully reconstructed from the sequence of applied deltas, ensuring that the map of the user's mind is transparent and verifiable.

### The Black Box

The Black Box is a method for the quasi-lossless compression of long-form sessions. Rather than relying on a truncated history or lossy summaries, the system compresses conversations into a structured record that preserves the logical form and the evidence of the session.

Currently, the system generates a single comprehensive box for each session. The architecture is designed to scale toward a sequence of chained boxes, where box $N+1$ begins exactly where box $N$ ended, ensuring there are no gaps or overlaps in the record.

Each box serves as a high-density index of the conversation. By capturing the initial premises, the dialectical route (the sequence of hypotheses and their falsifications), and the final actionable outputs, the Black Box allows the system to retrieve the exact cognitive state of a previous session without re-processing the entire raw transcript.

## Repository Map
<div align="right">

[↑ Return to top](#top)

</div>

The project is organized into modular components to separate the cognitive logic from the underlying infrastructure.

- **[`prompt/`](prompt/)** System prompts in Spanish (source), English, and French. Current version: v7.5.4. This is the heart of Kairós.

- **[`capture/daemon`](capture/daemon)** The capture stage. Watches a synced (with Syncthing) audio files folder (voice recordings), transcribes them through the Tinfoil.sh secure and private enclaves and moves them into the corpus (user's data). On the reading side it parses [KOReader](https://github.com/koreader/koreader)'s sidecar files, matches each recording to the highlight it belongs to, and writes the transcription back as a note on that highlight.

- **[`synthesize/elenchos`](synthesize/elenchos)**
 The optional synthesis module. Samples the corpus by period, runs the Grounded Theory stages through GTFlow, stages a debate between two agents with opposed biases, and materializes the surviving hypotheses into the corpus with a deterministic parser.

- **[`falsify/daemon-delta`](falsify/daemon-delta)**
 Writes the Codex. Validates a delta as a whole before writing anything, applies it, and regenerates the human-readable view. Nothing is deleted and every version is kept.

- **[`falsify/black-boxer`](falsify/black-boxer)**
 Generates the session boxes. Takes a conversation, compresses it into the box format, and writes the result as the processed version of that note.

- **[`falsify/querier`](falsify/querier)**
 The read path over the corpus. Hybrid search, hydration of results, and detection of AI conversations that have no Black Box.

- **[`infra/stt-proxy`](infra/stt-proxy)**
 A thin proxy in front of the speech models served by Tinfoil.sh. Voxtral first, Whisper as fallback.

- **[`infra/searxng`](infra/searxng)**
 A self-hosted meta-search instance, used by the debate agents when a claim needs grounding outside the corpus.

- **[`fortemi/`](fortemi/)**
 The integration layer for the current organization engine: a docker compose override, two patches the pinned version needs in order to run correctly, and a NOTICE declaring the provenance and terms of the third-party code underneath.

- **[`tools/_deprecated/onboarding`](tools/_deprecated/onboarding)**
The first-run interview that used to produce an initial Codex. Deprecated and being redesigned.

## Setup
<div align="right">

[↑ Return to top](#top)

</div>

For the moment, Kairós is a manually-operated system and can run on commodity hardware: any machine that runs Docker, no GPU needed. Linux and WSL2 are the tested paths. You also need a [Tinfoil](https://tinfoil.sh) account: the Chat plan hosts the falsification stage, and an API key feeds the daemons ([quickstart](https://docs.tinfoil.sh/quickstart)).

### 1. The organization engine

The stack is calibrated against Fortémi v2026.7.1. Clone that exact tag and apply the local patches:

```bash
git clone --branch v2026.7.1 --depth 1 https://github.com/fortemi/fortemi.git
cd fortemi
git apply /path/to/kairos/fortemi/patches/local-fixes.patch
docker compose -f docker-compose.workstation.yml up -d
```

The patch fixes a job deduplication bug and a parameter binding error present in that release. Do not track upstream: the touched files have since been rewritten in later versions and the patch will not apply.

### 2. Database initialization

Once the engine is up, run the init script once:

```bash
bash /path/to/kairos/fortemi/patches/init-fortemi-fix.sh
```

It performs three operations the engine does not do on its own: storage permissions, the `gen_uuid_v7()` SQL function the embedding trigger calls, and the HNSW index for semantic search. Without it, new notes are never embedded and semantic search fails silently. Re-run it after any volume wipe.

### 3. The Kairós layer

Clone this repository next to the Fortémi checkout and wire both through the compose override:

```bash
cp kairos/fortemi/docker-compose.override.yml fortemi/
cp kairos/fortemi/.env.example fortemi/.env   # set your Tinfoil API key and host paths
cd fortemi
docker compose -f docker-compose.workstation.yml -f docker-compose.override.yml \
  --profile querier --profile black-boxer up -d --build
```

The first run builds the Kairós images. Elasticsearch and daemon-delta start unconditionally; the querier and the black-boxer sit behind their profiles. Before the first codex delta, run the daemon-delta `init` command once (see `falsify/daemon-delta`): it installs the index template every codex index will inherit.

### 4. The falsification interface

Today the fourth stage runs in [Tinfoil Chat](https://tinfoil.sh/chat), driven by the system prompt in `prompt/` (Spanish source, English and French versions, current release v7.5.4).

- Install the prompt as your default system prompt under Settings → Prompts. Precedence, presets and placeholders are documented in the [chat settings](https://docs.tinfoil.sh/chat/settings).
- Create a project and add the codex views (the `.md` files daemon-delta writes in /out) to its documents area. Every chat started from the project reads that map. Projects require cloud sync and a Premium plan, and their contents are encrypted under your key ([projects](https://docs.tinfoil.sh/chat/features), [cloud sync](https://docs.tinfoil.sh/chat/cloud-sync)).
- The codex starts empty. It fills through use: conversations produce decisions, decisions are emitted as deltas, the daemon applies them, and the regenerated `.md` files replace the old ones in the project.

### 5. Voice capture

The capture daemon watches one folder. Choose a folder that receives your voice memos and sync it from wherever you record, you can use Syncthing for this. How you record into it is your choice. The one protocol requirement: filenames must carry the recording timestamp in a sortable pattern (`2026-10-24_14-32-00` style), because the daemon derives the note's timestamp first from the name, if it doesn't follow that naming pattern, the timestamp will come from the file's own timestamp. Point the daemon at the folder through its environment variables and it picks up new recordings on its own.

### 6. Annotated reading (optional)

Reading highlights live in KOReader sidecar files next to each book. Sync the folder holding books and sidecars with the same mechanism and point the daemon at it: it parses the sidecars, matches each recording to the highlight it belongs to, and writes the transcription back as a note on that highlight. Device and transport are your choice.

## Status, Roadmap, and What is Not Proven
<div align="right">

[↑ Return to top](#top)

</div>

Kairós is currently an operational prototype. It has been developed and validated as a personal cognitive prosthesis, meaning the system is fully functional but optimized for the author's specific cognitive profile and infrastructure.

### Current Status
The core cycle (Capture $\rightarrow$ Organize $\rightarrow$ Synthesize $\rightarrow$ Falsify) is complete and active. The system successfully transforms raw neuroatypical thought into a governed, versioned knowledge base. All components described in the Repository Map are integrated and producing results in a personal environment.

### Roadmap
The development of Kairós is moving toward reducing manual friction and increasing deployment flexibility:

- **Kubernetes Refactor**: Migration of the stack to a k8s-native architecture. This will move the system from a manual setup to a professional, scalable deployment.
- **Automatic Kairós**: Implementation of a fully automated context engineering pipeline. This includes the automatic generation of session Black Boxes and the seamless injection of the Codex into the LLM context without manual file manipulation.
- **Full English Migration**: Translation of the internal codebase, comments, and prompts to English to facilitate broader academic and technical collaboration.

### What is Not Proven
In the interest of technical honesty, the following boundaries are acknowledged:

- **Autonomous Installation**: While the setup is documented, a "one-click" installation for third parties has not been verified. The current deployment is a manually steered process.
- **Generalizability**: The system has been validated on a single, complex neuroatypical profile (the author). While the architecture is designed to be tailored to any user, the efficacy of the process across diverse cognitive profiles remains an open area for research.
- **Production Scale**: The system is designed for a single-user "exocortex" experience. No multi-user design has been developed or tested yet.