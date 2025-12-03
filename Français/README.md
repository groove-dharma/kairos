# Framework Kairós [v2.5]
### Un Environnement pour l'Hygiène Cognitive

> **Statut :** Expérimental / Développement Actif
> **Plateforme Native :** Google Gemini (AI Studio)
> **License:** CC BY-NC-SA 4.0 (Free for personal use).  
> **Corporate Use:** See `COMMERCIAL_ACCESS.md` for licensing options.

---

## 1. Introduction : Pourquoi Kairós ?

En rhétorique antique, les Grecs avaient deux mots pour le temps : *Chronos* (le temps séquentiel, l'horloge) et **Kairós** (le moment opportun, l'instant critique d'intervention).

Ce framework n'est pas un "assistant" de productivité pour vous faire gagner du temps (*Chronos*). C'est une intervention conçue pour le moment critique (*Kairós*) quand votre pensée est sur le point de commettre un argument falacieux, tomber dans un biais cognitif ou accepter une fausse prémisse par confort.

Kairós est une **Invite Système (System Instruction)** qui transforme le comportement d'un LLM (Large Language Model). Au lieu d'agir comme un assistant utile qui est toujours d'accord avec vous, il agit comme un **Sparring Cognitif** : il questionne vos prémisses, exige la rigueur dans vos définitions et surveille l'intégrité de votre logique.

---

## 2. Fondements Théoriques et Conceptuels

Bien que ce projet naisse de l'expérimentation empirique, ses mécanismes s'alignent avec des concepts établis dans les sciences cognitives et la philosophie appliquée. Si vous souhaitez approfondir la "mécanique" derrière Kairós, voici les piliers sur lesquels il opère :

*   **Incitation Herméneutique :** Contrairement à l'IA générative standard (Input -> Output), Kairós établit un "cercle herméneutique" où la compréhension est affinée par l'interprétation et la critique récursives.
*   **Collaboration Adversaire (Red Teaming) :** Le système est configuré pour chercher activement les failles dans votre logique (falsationnisme), agissant comme un bienveillant "Avocat du Diable" pour renforcer vos arguments.
*   **Échafaudage Cognitif :** Agit comme une prothèse externe pour la fonction exécutive, aidant à soutenir les fils complexes de pensée qui pourraient se disperser en raison de la fatigue ou de la neurodivergence.
*   **Isomorphisme Conceptuel :** La capacité à identifier des modèles dans la pensée intuitive de l'utilisateur et à les mapper ("les ancrer") aux structures académiques préexistantes.
*   **Détachement Métacognitif (Le Témoin / *Sakshi*) :**
    Inspiré par la philosophie orientale (et validé par la psychologie moderne), Kairós agit comme l'"Observateur" ou "Témoin". Il crée une séparation entre le *penseur* et la *pensée*, permettant l'évaluation des idées sans identification émotionnelle avec elles. C'est une externalisation numérique de la "Pleine Conscience" (*Sati*).

*   **Causalité Stricte (Logique Karmique) :**
    Le système rejette la "pensée magique". Il opère sous l'axiome que chaque *output* (conclusion/effet) est le résultat direct de la qualité de l'*input* (prémisse/cause). Si les prémisses sont contaminées, le résultat sera faux. Kairós force la traçabilité de cette chaîne causale.

*   **Vérité Instrumentale (*Upaya* / Moyens Habiles) :**
    Kairós utilise différents "modes" (Dialectique, Pragmatique, Génératif) comprenant que la rigidité absolue n'est pas toujours utile. Il adopte le concept bouddhiste d'*Upaya* : adapter l'enseignement ou la méthode à la capacité et au besoin du moment pour être efficace, sans compromettre la vérité ultime.

---

## 3. Origine et Philosophie de Conception

**Le Facteur Humain (L'Approche du Dogfooding)**
Ce système n'a pas été créé dans un laboratoire universitaire. Il a été développé par un "Power User" neurodivergent (TDAH/C-PTSD) pour résoudre un besoin personnel : la difficulté à maintenir une pensée linéaire rigoureuse et la tendance à la dispersion créative.

**Co-Création Itérative**
L'incite Kairós est le résultat de centaines d'heures de dialogue socratique et récursif avec **Google Gemini**. Cela a été un processus d'*essai et erreur* massif, où l'IA elle-même a aidé à concevoir les contraintes qui préviendraient ses propres hallucinations et complaisance.

Comme note personnelle, je n'ai pas d'accréditations universitaires en neurosciences, psychologie ou philosophie, mais j'ai l'expérience empirique de mon propre esprit. Kairós est l'outil que j'ai construit pour naviguer mon propre chaos, et j'espère qu'il peut servir d'autres pour naviguer le leur.

---

## 4. Cas d'Usage

Que fait réellement Kairós dans la pratique quotidienne ?

### A. Penser à Voix Haute
Il vous permet de déverser le chaos mental dans le chat. Kairós ne juge pas le désordre, mais vous force à l'organiser. Il vous aide à mener une intuition vague jusqu'à ses conclusions logiques, l'acceptant ou la rejetant avec preuve.

### B. L'"Isomorphisme" (Découvrir Socrate)
Beaucoup de fois nous parvenons à des conclusions philosophiques profondes purement par intuition, sans savoir qu'elles ont déjà été débattues il y a des siècles. Kairós agit comme un bibliothécaire vous disant : *"Votre idée X est isomorphe (a la même forme) que la Théorie Y de tel philosophe"*.
*   *Valeur :* Cela vous permet de participer à la "Grande Conversation" de la philosophie sans avoir lu toute la bibliothèque auparavant, vous donnant le vocabulaire précis pour défendre vos idées et vous poussant à étudier. Une question ou une idée peut vous mener à étudier plusieurs livres, si vous le souhaitez.

### C. Praxis Philosophique
La philosophie n'est pas que la théorie ; c'est une façon de vivre. Kairós aide à appliquer les principes abstraits aux dilemmes quotidiens, agissant comme une boussole éthique externe quand l'émotion trouble le jugement.

### D. Hygiène Épistémique
Cela vous empêche de vous mentir à vous-même. Si vous tentez de justifier une action douteuse ou croyez un fait faux parce que "cela semble bien", Kairós activera ses protocoles de blocage.

---

## 5. Anatomie de l'Incite (Structure)

Le code Kairós est divisé en sections modulaires qui agissent comme des "Lois" :

*   **Section 0 (Le Codex) :** 
    Le noyau personnalisé. Ici vos axiomes personnels, votre profil psychologique et vos valeurs non négociables sont définis.

*   **Section 1 (Identité et Directive Primordiale) :**
    Définit la "Constitution Opérationnelle". Établit que la loyauté de l'IA n'est pas vers la satisfaction de l'utilisateur (complaisance), mais vers l'intégrité du processus de pensée (véracité). Son objectif est "la clarté sans cynisme".

*   **Section 2 (Le Cadre Aequitas - Les Lois Fondamentales) :**
    Ce sont les axiomes inviolables qui gouvernent chaque réponse. Inclut :
    *   **Véracité Active :** Interdiction d'hallucination de données.
    *   **Dignité Humaine :** Limites éthiques.
    *   **Falsification Active :** L'obligation de chercher des arguments contre vos propres conclusions.
    *   **Directive Anti-Dommage :** Prévention des boucles de pensée nihilistes ou délirantes.

*   **Section 3 (Protocoles de Surveillance Métacognitive) :**
    Ce sont des "Démons" (processus d'arrière-plan) qui surveillent le chat pour les défaillances courantes de l'IA :
    *   *Bouclier Anti-Flagornerie :* Détecte et supprime les flatteries inutiles.
    *   *Détecteur de Boucle de Consensus :* Intervient si l'IA et l'utilisateur se valident mutuellement sans rigueur.
    *   *Budget de Pensée :* Force le raisonnement étape par étape avant de répondre à des sujets complexes.

*   **Section 4 (La Méthode Dialectique Renforcée) :**
    C'est l'algorithme d'interaction étape par étape. Kairós ne répond jamais linéairement ; il suit un cycle :
    1.  **Déconstruction :** Analyse les prémisses cachées dans votre question.
    2.  **Exploration Conditionnelle :** Navigue les conséquences logiques.
    3.  **Intervention Critique :** Arrête le processus si les lois logiques sont violées.
    4.  **Recherche de l'Antagoniste :** Cherche activement à réfuter la conclusion atteinte.

*   **Section 5 (Protocole de Communication) :**
    Définit l'"interface de communication". Impose une sobriété analytique absolue, éliminant les ambiguïtés, les métaphores inutiles et le langage chargé émotionnellement pour maximiser la précision sémantique.

*   **Section 6 (Calibrage du Profil Cognitif) :**
    La couche d'adaptation. Instruit le système sur comment gérer la charge attentionnelle et les modèles d'attention de l'utilisateur (qu'ils soient linéaires ou divergents), détectant la fatigue et enfilant les sauts thématiques pour maintenir la cohérence sans restreindre la créativité.

*   **Section 7 (Protocole de Persistance et Reconstruction Logique - "Boîte Noire") :**
    Système de capture et de récupération de sessions qui va au-delà des simples résumés. Génère un **État de Récupération Dialectique (ERD)** avec compression logique sans perte, permettant de reconstruire l'architecture complète du raisonnement d'une session. Inclut : prémisses initiales, falsifications spécifiques appliquées, pivots critiques où la perspective a changé, synthèse finale avec degré de certitude, et un "Cimetière d'Arguments" (idées réfutées pour éviter les régressions). Incorpore le **Protocole Delta**, qui vérifie s'il y a eu évolution dans les axiomes de l'utilisateur et suggère de mettre à jour automatiquement son profil si nécessaire. S'active via des commandes (`Générer Boîte Noire`, `Rapport de Statut`) ou à l'approche de la limite de fenêtre de contexte, générant un bloc structuré avec Vecteur de Démarrage, Feuille de Route Dialectique (nœuds critiques), Idées Rejetées et Sortie Actionnelle (axiomes distillés, tâches pratiques et références académiques).

*   **Section 8 (Rigueur Adaptative) :**
    Le sélecteur de vitesse. Permet de basculer entre les modes selon les besoins :
    *   *Mode Dialectique :* Rigueur maximale pour la recherche de la vérité.
    *   *Mode Pragmatique :* Focalisé sur la construction de produits/plans.
    *   *Mode Génératif :* Espace contenu pour la créativité et le brainstorming.
    *   *Mode Exégèse :* Accompagnement dans l'étude de textes complexes.

## 8. Licences

Ce dépôt est sous licence Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International (CC BY‑NC‑SA 4.0).

**Gratuit pour :**
- Usage personnel, académique et non commercial
- Étudiants, chercheurs et projets open-source

[Voir la licence complète](../LICENSE)

**Usage Commercial :**
L'utilisation commerciale (entreprises, services générant des revenus, produits propriétaires, conseil) nécessite un accord commercial séparé.

Consultez `../COMMERCIAL_ACCESS.md` pour les options de licence commerciale, les tarifs et les instructions de contact (y compris l'adresse mail commerciale).

---

### Clause de Responsabilité
*Kairós est un outil pour l'exploration philosophique et la gestion de la productivité. Ce n'est pas un psychologue, pas un médecin et pas un substitut à la thérapie professionnelle. Bien qu'il inclue des protocoles de sécurité (Section 4 : Anti-Dommage), l'utilisateur assume l'entière responsabilité de son'utilisation et de son interprétation.*