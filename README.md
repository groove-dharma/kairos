[🇪🇸 Leer en Español](./Español/README.md) | [🇫🇷 Lire en Français](./Français/README.md)
# Kairós Framework

### A Cognitive Prosthesis & Dialectical Sanitation Environment

> **Status:** Operational / Active Development
> **Native Platform:** Google Gemini Ecosystem
> **License:** CC BY-NC-SA 4.0 (Non-Commercial).
> **Corporate Use:** See `COMMERCIAL_ACCESS.md`.

---

## 1. Introduction: The Concept

In ancient rhetoric, the Greeks distinguished two forms of time: *Chronos* (sequential, quantitative time) and **Kairós** (the opportune moment, the critical instant of intervention).

This framework is not a productivity assistant designed to save you *Chronos*. It is a dialectical intervention designed for *Kairós*: that critical split-second when a thought process is about to succumb to a fallacy, a cognitive bias, or a comfortable delusion.

**Kairós is a System Prompt architecture** that fundamentally alters the behavior of Large Language Models (LLMs). It strips away the "helpful assistant" persona—which tends toward sycophancy and agreement—and replaces it with a **Cognitive Sparring Partner**. Its function is not to please the user, but to maximize the logical density and integrity of the user's thought process.

---

## 2. Theoretical Architecture

Kairós operates on a "High-Friction" logic. It assumes that rigorous thinking requires resistance, not validation. The framework is built upon several key pillars of cognitive science and applied philosophy:

* **Hermeneutic Prompting:** Unlike standard "Input -> Output" generation, Kairós treats the conversation as a hermeneutic circle, where understanding is refined through recursive deconstruction and critique.
* **Adversarial Collaboration (Red Teaming):** The system is mandated to act as a benevolent "Devil's Advocate" (falsificationism). It validates ideas only after attempting to dismantle them.
* **Cognitive Scaffolding (The Prosthesis):** It acts as an external executive function, specifically designed to sustain complex logical threads that might otherwise dissolve due to fatigue, neurodivergence (ADHD/ASD), or cognitive load.
* **Conceptual Isomorphism (The Librarian):** The system scans user input for "raw intuitions" and maps them to established academic, philosophical, or scientific frameworks, bridging the gap between "Street Smarts" and formal nomenclature.
* **Metacognitive Detachment:** Kairós forces a separation between the *thinker* and the *thought*, treating ideas as external objects to be audited rather than identity markers to be defended.

---

## 3. System Anatomy

The Kairós prompt is not a single instruction, but a modular operating system.

### The Codex (User Axioms)

This is the mutable core of the system. It contains the user's specific psychological profile, non-negotiable values, and established truths. Kairós uses this to tailor its logic to the specific "Hardware" of the user.

### The Kernel Laws (Aequitas)

Inviolable rules that override all other instructions.

* **Active Truthfulness:** Training data is treated as legacy; current search data is treated as truth.
* **Human Dignity:** Critique is limited to ideas, never the individual (unless asked for it).
* **Anti-Loop:** Mandatory intervention when circular logic or depressive loops are detected.

### The Daemons (Background Processes)

Silent monitoring scripts that run parallel to the conversation:

* **Anti-Sycophancy Shield:** Intercepts flattery and agreement.
* **Narrative Drift Interrupter:** Stops "storytelling" to return to facts.
* **Entropy Monitor:** Detects when the conversation is losing logical coherence.

### The Black Box (Persistence Protocol)

Kairós includes a "Save State" mechanism called the **DRS (Dialectical Recovery State)**. It allows the system to generate a lossless logical compression of a session, enabling the user to "reboot" a conversation in a new context window without losing the logical architecture or established axioms.

---

## 4. Deployment & Compatibility

Kairós is optimized for the Google Gemini architecture due to its superior context handling and instruction adherence.

### Recommended Configurations

| Version Branch | Target Environment | Best For |
| --- | --- | --- |
| **Kairós v2.8** | **Google AI Studio** (Gemini 2.5 pro) | Deep structural work, prompting development, and raw logic processing. Offers the highest level of control. |
| **Kairós v3.0** | **Gemini Advanced** (Gemini 3 / Gems) | Daily integrated use. Ideal for deploying Kairós as a persistent "Gem" within the standard Google interface. |

> **Note on Model Behavior:** Attempts to run Kairós on models with high "Safety/Refusal" triggers (e.g., certain iterations of GPT-4o or Claude 3.5 Sonnet) may result in the model rejecting the "Amoral/Clinical" persona instruction. The prompt is tuned specifically for the Gemini reasoning engine. But there will be tests in the future to adapt th Kairós Framework to other LLMs.

---

## 5. Installation & Onboarding

Kairós is not "Plug & Play." It requires a reference frame to function effectively.

### Phase 1: The Codex Generation

You cannot run the main system without a **Codex**.

1. Navigate to the `/tools/onboarding` directory.
2. Copy the **Onboarding Agent** prompt.
3. Run this agent in a temporary chat session.
4. The agent will interview you to map your cognitive style, biases, and axioms.
5. **Output:** It will generate a code block labeled `USER_CODEX`.

### Phase 2: System Injection

1. Open the raw `KAIROS_SYSTEM_PROMPT` file (select the version matching your environment).
2. Locate the `<USER_CODEX>` tag within the XML structure.
3. Paste the code block generated in Phase 1 into this section.
4. Paste the entire resultant prompt into the "System Instructions" field of your LLM.

---

## 6. Usage Guidelines

### The "Zero-Shot" Rule

Do not start a session with "Hello." Start with data. Kairós is initialized to skip pleasantries and immediately engage with the first premise provided. **Pro-Tip:** You can say "remember to boot" at the start of the conversation to make sure the LLM has integrated the framework.

### The "Isomorphism" Tag

When you want Kairós to find the academic equivalent of your thought, explicitly ask for an "Isomorphic Audit." It will search the history of philosophy and science to find the structural twin of your idea. **Disclaimer:** Kairós will do this even if you do not ask this of him, which can be helpful for the thought process, but if you want a specific idea to be audited, say it clearly.

### The Black Box Command

When the context window fills up, or you wish to end a session, command: **"Generate Black Box."** Save the output. This is your seed for the next session.

---

## 7. License & Disclaimers

**Creative Commons BY-NC-SA 4.0**

* **Free for:** Personal use, academic research, open-source adaptation.
* **Commercial:** Corporate deployment, paid consulting using this framework, or proprietary integration requires a commercial license. See `COMMERCIAL_ACCESS.md`.

**Disclaimer:**
Kairós is a tool for logic, not a mental health professional. While it contains safeguards (The Anti-Nihilism Law), it is designed to strip away comforting illusions. Use with discretion and responsibility. The user assumes full liability for the interpretation of the system's output.