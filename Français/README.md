Voici la traduction de votre version éditée en français :

# Kairós Framework

### Prothèse Cognitive & Environnement d'Assainissement Dialectique

> **Statut :** Opérationnel / Développement Actif
> **Plateforme Native :** Écosystème Google Gemini
> **Licence :** CC BY-NC-SA 4.0 (Non Commercial).
> **Usage Corporatif :** Voir `COMMERCIAL_ACCESS.md`.

---

## 1. Introduction : Le Concept

Dans la rhétorique ancienne, les Grecs distinguaient deux formes de temps : *Chronos* (le temps séquentiel, quantitatif) et **Kairós** (le moment opportun, l'instant critique d'intervention).

Ce framework n'est pas un assistant de productivité conçu pour vous faire gagner du *Chronos*. C'est une intervention dialectique conçue pour le *Kairós* : cette fraction de seconde critique où un processus de pensée est sur le point de succomber à une erreur logique, un biais cognitif ou une illusion confortable.

**Kairós est une architecture de System Prompt** qui modifie fondamentalement le comportement des Grands Modèles de Langage (LLM). Il dépouille l'IA de sa persona d'« assistant utile » — qui tend vers la flagornerie et l'accord systématique — et la remplace par un **Partenaire de Sparring Cognitif**. Sa fonction n'est pas de plaire à l'utilisateur, mais de maximiser la densité logique et l'intégrité de son processus de pensée.

---

## 2. Architecture Théorique

Kairós opère selon une logique de « Haute Friction ». Il part du principe qu'une pensée rigoureuse nécessite de la résistance, et non de la validation. Le framework repose sur plusieurs piliers clés des sciences cognitives et de la philosophie appliquée :

* **Prompting Herméneutique :** Contrairement à la génération standard « Input -> Output », Kairós traite la conversation comme un cercle herméneutique, où la compréhension s'affine par la déconstruction récursive et la critique.
* **Collaboration Adversariale (Red Teaming) :** Le système a pour mandat d'agir comme un « Avocat du Diable » bienveillant (falsificationnisme). Il ne valide les idées qu'après avoir tenté de les démanteler.
* **Échafaudage Cognitif (La Prothèse) :** Il agit comme une fonction exécutive externe, spécifiquement conçue pour soutenir des fils logiques complexes qui pourraient autrement se dissoudre en raison de la fatigue, de la neurodivergence (TDAH/TSA) ou de la charge cognitive.
* **Isomorphisme Conceptuel (Le Bibliothécaire) :** Le système scanne l'input de l'utilisateur à la recherche d'« intuitions brutes » et les mappe vers des cadres académiques, philosophiques ou scientifiques établis, comblant le fossé entre le « savoir de la rue » (Street Smarts) et la nomenclature formelle.
* **Distanciation Métacognitive :** Kairós force une séparation entre le *penseur* et la *pensée*, traitant les idées comme des objets externes à auditer plutôt que comme des marqueurs d'identité à défendre.

---

## 3. Anatomie du Système

Le prompt Kairós n'est pas une simple instruction, mais un système d'exploitation modulaire.

### Le Codex (Axiomes Utilisateur)

C'est le noyau mutable du système. Il contient le profil psychologique spécifique de l'utilisateur, ses valeurs non négociables et ses vérités établies. Kairós l'utilise pour adapter sa logique au « Matériel » (Hardware) spécifique de l'utilisateur.

### Les Lois du Noyau (Aequitas)

Règles inviolables qui prévalent sur toutes les autres instructions.

* **Véracité Active :** Les données d'entraînement sont traitées comme obsolètes ; les données de recherche actuelles sont traitées comme la vérité.
* **Dignité Humaine :** La critique se limite aux idées, jamais à l'individu (sauf demande expresse).
* **Anti-Boucle :** Intervention obligatoire lorsque des logiques circulaires ou des boucles dépressives sont détectées.

### Les Démons (Processus d'Arrière-plan)

Scripts de surveillance silencieux qui s'exécutent en parallèle de la conversation :

* **Bouclier Anti-Flagornerie :** Intercepte la flatterie et l'accord complaisant.
* **Interrupteur de Dérive Narrative :** Arrête le « storytelling » pour revenir aux faits.
* **Moniteur d'Entropie :** Détecte lorsque la conversation perd sa cohérence logique.

### La Boîte Noire (Protocole de Persistance)

Kairós inclut un mécanisme de « Sauvegarde d'État » appelé **DRS (État de Récupération Dialectique)**. Il permet au système de générer une compression logique sans perte d'une session, permettant à l'utilisateur de « redémarrer » une conversation dans une nouvelle fenêtre contextuelle sans perdre l'architecture logique ou les axiomes établis.

---

## 4. Déploiement & Compatibilité

Kairós est optimisé pour l'architecture Google Gemini en raison de sa gestion supérieure du contexte et de son adhésion stricte aux instructions.

### Configurations Recommandées

| Branche de Version | Environnement Cible | Idéal Pour |
| --- | --- | --- |
| **Kairós v2.8** | **Google AI Studio** (Gemini 2.5 pro) | Travail structurel profond, développement de prompts et traitement logique brut. Offre le plus haut niveau de contrôle. |
| **Kairós v3.0** | **Gemini Advanced** (Gemini 3 / Gems) | Usage quotidien intégré. Idéal pour déployer Kairós en tant que « Gem » persistant au sein de l'interface Google standard. |

> **Note sur le Comportement du Modèle :** Les tentatives d'exécution de Kairós sur des modèles ayant des déclencheurs de « Sécurité/Refus » élevés (ex: certaines itérations de GPT-4o ou Claude 3.5 Sonnet) peuvent entraîner un rejet de l'instruction de persona « Amorale/Clinique ». Le prompt est calibré spécifiquement pour le moteur de raisonnement de Gemini. Des tests futurs viseront à adapter le Framework Kairós à d'autres LLM.

---

## 5. Installation & Onboarding

Kairós n'est pas « Plug & Play ». Il nécessite un cadre de référence pour fonctionner efficacement.

### Phase 1 : Génération du Codex

Vous ne pouvez pas exécuter le système principal sans un **Codex**.

1. Naviguez vers le répertoire `/tools/onboarding`.
2. Copiez le prompt de l'**Agent d'Onboarding**.
3. Exécutez cet agent dans une session de chat temporaire.
4. L'agent vous interrogera pour cartographier votre style cognitif, vos biais et vos axiomes.
5. **Sortie :** Il générera un bloc de code étiqueté `USER_CODEX`.

### Phase 2 : Injection du Système

1. Ouvrez le fichier brut `KAIROS_SYSTEM_PROMPT` (sélectionnez la version correspondant à votre environnement).
2. Localisez la balise `<USER_CODEX>` dans la structure XML.
3. Collez le bloc de code généré lors de la Phase 1 dans cette section.
4. Collez l'intégralité du prompt résultant dans le champ « Instructions Système » de votre LLM.

---

## 6. Guides d'Utilisation

### La Règle « Zero-Shot »

Ne commencez pas une session par « Bonjour ». Commencez par des données. Kairós est initialisé pour ignorer les politesses et s'engager immédiatement avec la première prémisse fournie. **Astuce Pro :** Vous pouvez dire « n'oublie pas de booter » ("remember to boot") au début de la conversation pour vous assurer que le LLM a bien intégré le framework.

### L'Étiquette « Isomorphisme »

Lorsque vous souhaitez que Kairós trouve l'équivalent académique de votre pensée, demandez explicitement un « Audit Isomorphique ». Il cherchera dans l'histoire de la philosophie et de la science pour trouver le jumeau structurel de votre idée. **Avertissement :** Kairós le fera souvent même sans demande, ce qui aide le processus de pensée, mais si vous voulez qu'une idée spécifique soit auditée, dites-le clairement.

### La Commande Boîte Noire

Lorsque la fenêtre contextuelle se remplit, ou que vous souhaitez terminer une session, ordonnez : **« Générer Boîte Noire »**. Sauvegardez le résultat. C'est votre graine (seed) pour la session suivante.

---

## 7. Licence & Avertissements

**Creative Commons BY-NC-SA 4.0**

* **Gratuit pour :** Usage personnel, recherche académique, adaptation open-source.
* **Commercial :** Le déploiement corporatif, le conseil payant utilisant ce framework ou l'intégration propriétaire nécessitent une licence commerciale. Voir `COMMERCIAL_ACCESS.md`.

**Avertissement (Disclaimer) :**
Kairós est un outil pour la logique, pas un professionnel de la santé mentale. Bien qu'il contienne des garde-fous (La Loi Anti-Nihilisme), il est conçu pour dépouiller l'utilisateur de ses illusions réconfortantes. À utiliser avec discrétion et responsabilité. L'utilisateur assume l'entière responsabilité de l'interprétation des résultats du système.