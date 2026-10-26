<a id="top"></a>
<div align="center">

# Kairós
**Une exo-prothèse cognitive pour les esprits neuroatypiques.**

Un système conçu pour déplacer le processus de cognition hors de la tête avec une confidentialité à confiance nulle (zero-trust), offrant un soutien fonctionnel stable à des facultés cognitives qui peuvent être intermittentes ou altérées. En transférant la charge de l'organisation, de la continuité et de la vérification vers un substrat externe, Kairós permet à l'utilisateur d'exercer son intelligence existante sans la friction du dysfonctionnement exécutif ou d'autres troubles.

[**Implémentation technique**](#implémentation-technique) · [**Écosystème**](#écosystème) · [**Ingénierie du contexte**](#ingénierie-du-contexte) · [**Carte du dépôt**](#carte-du-dépôt) · [**Installation**](#installation) · [**Feuille de route**](#statut-feuille-de-route-et-ce-qui-nest-pas-prouvé)

</div>

---

## De quoi il s'agit

Kairós n'est pas un assistant ; c'est une prothèse cognitive. Alors qu'un assistant vous aide à accomplir une tâche précise, une prothèse soutient fonctionnellement une faculté.

Le système transforme les capacités génériques des LLM en « cognition artificielle ». Il ne vise ni à augmenter l'intelligence innée ni à ajouter de nouvelles capacités. Il se concentre plutôt sur l'élimination de la friction causée par les lacunes cognitives (comme le dysfonctionnement exécutif), permettant à l'utilisateur d'opérer à la hauteur de son potentiel intellectuel réel avec un niveau de stabilité et de constance qui serait autrement inatteignable.

En transformant la nature fragmentée de la pensée neuroatypique en un actif récupérable, connectable et falsifiable (au sens poppérien), Kairós garantit que le résultat de la pensée ne dépend plus de la stabilité du moment, mais de la robustesse du système.

## Le problème

La plupart des outils de soutien cognitif relèvent de l'une des quatre catégories suivantes, chacune avec un défaut fondamental qui l'empêche de fonctionner comme une véritable prothèse :

**Les assistants conversationnels** sont optimisés pour l'assentiment. En raison de leur flagornerie inhérente, ils tendent à valider les prémisses de l'utilisateur plutôt qu'à les contester. Cela crée une boucle de rétroaction qui peut amplifier les biais ou renforcer les erreurs au lieu de les corriger.

**Les applications de prise de notes** sont un stockage passif. Elles excellent à capturer et organiser les données, mais elles ne raisonnent pas dessus. Elles fournissent une archive interrogeable, mais elles ne peuvent pas relier une pensée actuelle à un schéma d'il y a deux ans, ni alerter l'utilisateur d'une contradiction logique dans son propre raisonnement.

**La thérapie et le soutien clinique** apportent un jugement et une perspective extérieure essentiels, mais ils manquent souvent des données brutes. Ces processus reposent généralement sur la mémoire rétrospective de l'utilisateur, qui est fragile et sujette aux biais, en particulier dans les profils neuroatypiques.

**La vulnérabilité des données** est intrinsèque à la plupart des systèmes d'IA avancés : les utilisateurs doivent confier leurs processus cognitifs les plus intimes à des fournisseurs de cloud opaques. Pour une prothèse cognitive, c'est inacceptable : on ne peut pas faire confiance à une « carte mentale » si le substrat sur lequel elle vit n'est pas privé. Cela crée une tension où l'utilisateur doit choisir entre puissance cognitive et sécurité épistémique.

**Kairós** comble cette lacune en combinant la puissance de raisonnement de l'IA avec une mémoire privée, gouvernée et versionnée de l'utilisateur. Il déplace le processus de « se souvenir et rapporter » vers « analyser un registre vérifiable », garantissant que le soutien repose sur des preuves plutôt que sur le souvenir.

## Implémentation technique
<div align="right">

[↑ Retour en haut](#top)

</div>

Kairós est un orchestrateur d'outils spécialisés. Plutôt que de réimplémenter des primitives de bas niveau, il intègre des technologies établies pour exécuter une boucle cognitive en quatre étapes : **Capture $\rightarrow$ Organisation $\rightarrow$ Synthèse $\rightarrow$ Falsification**.

**Capture** est le point d'entrée. Elle rassemble la matière première issue de dictées vocales et de lectures annotées. Cette étape s'appuie sur [Tinfoil.sh](https://tinfoil.sh) pour l'informatique confidentielle, garantissant que la transcription a lieu dans des enclaves sécurisées afin d'assurer une confidentialité totale. [Syncthing](https://syncthing.net) est utilisé pour synchroniser ces enregistrements et les fichiers sidecar entre les appareils sans fournisseur de cloud centralisé.

**Organisation** transforme les données brutes en un actif récupérable. Cette étape est propulsée par [Fortémi](https://github.com/fortemi/fortemi) et [Elasticsearch](https://www.elastic.co/elasticsearch), permettant une recherche hybride (lexicale BM25 et sémantique vectorielle) et une organisation gouvernée des connaissances à l'aide de hiérarchies SKOS. Elle garantit que l'information est structurée pour être retrouvée, et pas seulement stockée.

**Synthèse** est le processus qui transforme un grand volume de données organisées en hypothèses actionnables. C'est un module qui utilise [GTFlow](https://github.com/zw-zhtlab/GTFlow) pour implémenter les étapes de la théorie ancrée et [AG2](https://github.com/ag2ai/ag2) pour mettre en scène des débats antagonistes entre agents. Cette tension dialectique permet au système de détecter des schémas et de proposer des mécanismes pour les expliquer.

**Falsification** est l'étape finale et la plus critique. Elle soumet les hypothèses générées à des tests rigoureux face à de nouvelles preuves et à l'examen logique. Ce processus se matérialise par l'interaction entre l'utilisateur et un LLM gouverné par le prompt système de Kairós, qui impose un protocole dialectique de contestation et de vérification. Une hypothèse ne rejoint les connaissances stables de l'utilisateur (Codex) qu'une fois qu'elle a résisté à son meilleur antagoniste.

En fonctionnement quotidien, le système se concentre sur **Capture, Organisation et Falsification**. La synthèse est un module périodique et optionnel, car elle exige un volume de corpus conséquent pour produire des résultats significatifs.

## Écosystème
<div align="right">

[↑ Retour en haut](#top)

</div>

Au-delà du dépôt principal, Kairós utilise des outils complémentaires pour optimiser l'environnement de production :

- [**`tinfoil-proxy-stack`**](https://github.com/groove-dharma/tinfoil-proxy-stack) : Un proxy non officiel de couche 7 qui permet à un réseau de conteneurs internes de partager une identité attestée unique. Il implémente le traitement par lots des embeddings pour réduire la latence de l'API et optimiser le débit.
- **`kairos-tts-koplugin`** : Un fork intégrant [norude/TTS.koplugin](https://github.com/norude/TTS.koplugin) et [stradichenko/audiobook.koplugin](https://github.com/stradichenko/audiobook.koplugin) plus un serveur local [Piper](https://github.com/OHF-Voice/piper1-gpl). Cela permet d'écouter des livres avec une voix neuronale qui ne quitte jamais le réseau local, étendant la fonctionnalité à des liseuses plus anciennes.

## Ingénierie du contexte
<div align="right">

[↑ Retour en haut](#top)

</div>

Kairós gère l'énorme quantité de données personnelles à travers deux couches distinctes d'ingénierie du contexte. Cela garantit que le système reste conscient de l'identité et de l'historique de l'utilisateur sans surcharger la mémoire immédiate du modèle.

### Le Codex

Le Codex est une carte vivante et versionnée de la topographie cognitive de l'utilisateur, incluant ses propres modèles théoriques, axiomes, protocoles et décisions. Il remplit deux fonctions principales : il indique au système à qui il parle (ce qui a été décidé, ce qui reste clos et ce qui est en attente) et il calibre la façon dont le système s'exprime.

Le Codex est gouverné par le Protocole Delta. Chaque changement est proposé comme un delta atomique : il est appliqué dans son intégralité ou pas du tout. Pour conserver une piste d'audit complète, rien n'est jamais supprimé ; chaque modification est versionnée. Parce que le Codex est un état dérivé, il peut être entièrement reconstruit à partir de la séquence des deltas appliqués, garantissant que la carte de l'esprit de l'utilisateur est transparente et vérifiable.

### La Boîte Noire

La Boîte Noire est une méthode de compression quasi sans perte des sessions de longue durée. Plutôt que de s'appuyer sur un historique tronqué ou des résumés avec perte, le système compresse les conversations en un registre structuré qui préserve la forme logique et les preuves de la session.

Actuellement, le système génère une seule boîte complète par session. L'architecture est conçue pour évoluer vers une séquence de boîtes chaînées, où la boîte $N+1$ commence exactement là où la boîte $N$ s'est terminée, garantissant l'absence de lacunes ou de chevauchements dans le registre.

Chaque boîte sert d'index haute densité de la conversation. En capturant les prémisses initiales, l'itinéraire dialectique (la séquence des hypothèses et de leurs falsifications) et les résultats actionnables finaux, la Boîte Noire permet au système de retrouver l'état cognitif exact d'une session antérieure sans retraiter l'intégralité de la transcription brute.

## Carte du dépôt
<div align="right">

[↑ Retour en haut](#top)

</div>

Le projet est organisé en composants modulaires afin de séparer la logique cognitive de l'infrastructure sous-jacente.

- **[`prompt/`](prompt/)** Prompts système en espagnol (source), anglais et français. Version actuelle : v7.5.4. C'est le cœur de Kairós.

- **[`capture/daemon`](capture/daemon)** L'étape de capture. Surveille un dossier synchronisé (avec Syncthing) de fichiers audio (enregistrements vocaux), les transcrit via les enclaves sécurisées et privées de Tinfoil.sh et les transfère dans le corpus (données de l'utilisateur). Côté lecture, il analyse les fichiers sidecar de [KOReader](https://github.com/koreader/koreader), associe chaque enregistrement au surlignage auquel il appartient et réécrit la transcription comme note sur ce surlignage.

- **[`synthesize/elenchos`](synthesize/elenchos)**
 Le module de synthèse optionnel. Échantillonne le corpus par période, exécute les étapes de la théorie ancrée via GTFlow, met en scène un débat entre deux agents aux biais opposés, et matérialise les hypothèses survivantes dans le corpus à l'aide d'un parseur déterministe.

- **[`falsify/daemon-delta`](falsify/daemon-delta)**
 Écrit le Codex. Valide un delta dans son ensemble avant d'écrire quoi que ce soit, l'applique, puis régénère la vue lisible par l'humain. Rien n'est supprimé et chaque version est conservée.

- **[`falsify/black-boxer`](falsify/black-boxer)**
 Génère les boîtes de session. Prend une conversation, la compresse au format boîte et écrit le résultat comme version traitée de cette note.

- **[`falsify/querier`](falsify/querier)**
 Le chemin de lecture sur le corpus. Recherche hybride, hydratation des résultats, et détection des conversations d'IA dépourvues de Boîte Noire.

- **[`infra/stt-proxy`](infra/stt-proxy)**
 Un proxy léger devant les modèles vocaux servis par Tinfoil.sh. Voxtral d'abord, Whisper en repli.

- **[`infra/searxng`](infra/searxng)**
 Une instance de méta-recherche auto-hébergée, utilisée par les agents de débat lorsqu'une affirmation nécessite un ancrage hors du corpus.

- **[`fortemi/`](fortemi/)**
 La couche d'intégration pour le moteur d'organisation actuel : un override docker compose, deux correctifs dont la version épinglée a besoin pour fonctionner correctement, et un NOTICE déclarant la provenance et les conditions du code tiers sous-jacent.

- **[`tools/_deprecated/onboarding`](tools/_deprecated/onboarding)**
L'entretien de première exécution qui produisait autrefois un Codex initial. Déprécié et en cours de refonte.

## Installation
<div align="right">

[↑ Retour en haut](#top)

</div>

Pour le moment, Kairós est un système à fonctionnement manuel et peut tourner sur du matériel standard : toute machine exécutant Docker, sans GPU. Linux et WSL2 sont les chemins testés. Vous avez également besoin d'un compte [Tinfoil](https://tinfoil.sh) : le plan Chat héberge l'étape de falsification, et une clé API alimente les démons ([démarrage rapide](https://docs.tinfoil.sh/quickstart)).

### 1. Le moteur d'organisation

La pile est calibrée sur Fortémi v2026.7.1. Clonez ce tag exact et appliquez les correctifs locaux :

```bash
git clone --branch v2026.7.1 --depth 1 https://github.com/fortemi/fortemi.git
cd fortemi
git apply /path/to/kairos/fortemi/patches/local-fixes.patch
docker compose -f docker-compose.workstation.yml up -d
```

Le correctif répare un bug de déduplication des tâches et une erreur de liaison de paramètres présents dans cette version. Ne suivez pas l'amont : les fichiers touchés ont depuis été réécrits dans des versions ultérieures et le correctif ne s'appliquera pas.

### 2. Initialisation de la base de données

Une fois le moteur lancé, exécutez le script d'initialisation une fois :

```bash
bash /path/to/kairos/fortemi/patches/init-fortemi-fix.sh
```

Il effectue trois opérations que le moteur ne fait pas de lui-même : les permissions de stockage, la fonction SQL `gen_uuid_v7()` appelée par le trigger d'embedding, et l'index HNSW pour la recherche sémantique. Sans cela, les nouvelles notes ne reçoivent jamais leur embedding et la recherche sémantique échoue silencieusement. Réexécutez-le après toute purge de volumes.

### 3. La couche Kairós

Clonez ce dépôt à côté du checkout Fortémi et reliez les deux via l'override compose :

```bash
cp kairos/fortemi/docker-compose.override.yml fortemi/
cp kairos/fortemi/.env.example fortemi/.env   # définissez votre clé API Tinfoil et les chemins de l'hôte
cd fortemi
docker compose -f docker-compose.workstation.yml -f docker-compose.override.yml \
  --profile querier --profile black-boxer up -d --build
```

La première exécution construit les images Kairós. Elasticsearch et daemon-delta démarrent inconditionnellement ; le querier et le black-boxer sont derrière leurs profils. Avant le premier delta du codex, exécutez la commande `init` de daemon-delta une fois (voir `falsify/daemon-delta`) : elle installe le modèle d'index dont héritera chaque index du codex.

### 4. L'interface de falsification

Aujourd'hui, la quatrième étape s'exécute dans [Tinfoil Chat](https://tinfoil.sh/chat), pilotée par le prompt système dans `prompt/` (source en espagnol, versions anglaise et française, version actuelle v7.5.4).

- Installez le prompt comme prompt système par défaut sous Settings → Prompts. La priorité, les préréglages et les espaces réservés sont documentés dans les [paramètres du chat](https://docs.tinfoil.sh/chat/settings).
- Créez un projet et ajoutez les vues du codex (les fichiers `.md` que daemon-delta écrit dans /out) à sa zone de documents. Chaque conversation lancée depuis le projet lit cette carte. Les projets requièrent la synchronisation cloud et un plan Premium, et leurs contenus sont chiffrés avec votre clé ([projets](https://docs.tinfoil.sh/chat/features), [synchronisation cloud](https://docs.tinfoil.sh/chat/cloud-sync)).
- Le codex commence vide. Il se remplit avec l'usage : les conversations produisent des décisions, les décisions sont émises sous forme de deltas, le démon les applique, et les fichiers `.md` régénérés remplacent les anciens dans le projet.

### 5. Capture vocale

Le démon de capture surveille un dossier. Choisissez un dossier qui reçoit vos mémos vocaux et synchronisez-le depuis l'endroit où vous enregistrez ; vous pouvez utiliser Syncthing pour cela. La façon dont vous enregistrez dedans relève de votre choix. La seule exigence du protocole : les noms de fichiers doivent porter l'horodatage de l'enregistrement dans un format triable (style `2026-10-24_14-32-00`), car le démon dérive d'abord l'horodatage de la note du nom ; s'il ne suit pas ce schéma de nommage, l'horodatage proviendra de celui du fichier lui-même. Pointez le démon vers le dossier via ses variables d'environnement et il récupérera les nouveaux enregistrements de lui-même.

### 6. Lecture annotée (optionnel)

Les surlignages de lecture vivent dans les fichiers sidecar de KOReader à côté de chaque livre. Synchronisez le dossier contenant les livres et les sidecars avec le même mécanisme et pointez le démon dessus : il analyse les sidecars, associe chaque enregistrement au surlignage auquel il appartient et réécrit la transcription comme note sur ce surlignage. L'appareil et le transport relèvent de votre choix.

## Statut, feuille de route et ce qui n'est pas prouvé
<div align="right">

[↑ Retour en haut](#top)

</div>

Kairós est actuellement un prototype opérationnel. Il a été développé et validé comme prothèse cognitive personnelle, ce qui signifie que le système est pleinement fonctionnel mais optimisé pour le profil cognitif et l'infrastructure spécifiques de l'auteur.

### Statut actuel
Le cycle central (Capture $\rightarrow$ Organisation $\rightarrow$ Synthèse $\rightarrow$ Falsification) est complet et actif. Le système transforme avec succès la pensée neuroatypique brute en une base de connaissances gouvernée et versionnée. Tous les composants décrits dans la Carte du dépôt sont intégrés et produisent des résultats dans un environnement personnel.

### Feuille de route
Le développement de Kairós s'oriente vers la réduction de la friction manuelle et l'augmentation de la flexibilité de déploiement :

- **Refonte Kubernetes** : Migration de la pile vers une architecture native k8s. Cela fera passer le système d'une installation manuelle à un déploiement professionnel et évolutif.
- **Kairós automatique** : Implémentation d'un pipeline d'ingénierie du contexte entièrement automatisé. Cela inclut la génération automatique des Boîtes Noires de session et l'injection transparente du Codex dans le contexte du LLM sans manipulation manuelle de fichiers.
- **Migration complète vers l'anglais** : Traduction de la base de code interne, des commentaires et des prompts vers l'anglais pour faciliter une collaboration académique et technique plus large.

### Ce qui n'est pas prouvé
Dans un souci d'honnêteté technique, les limites suivantes sont reconnues :

- **Installation autonome** : Bien que l'installation soit documentée, une installation « en un clic » pour des tiers n'a pas été vérifiée. Le déploiement actuel est un processus piloté manuellement.
- **Généralisabilité** : Le système a été validé sur un seul profil neuroatypique complexe (l'auteur). Bien que l'architecture soit conçue pour être adaptée à tout utilisateur, l'efficacité du processus sur des profils cognitifs divers reste un domaine de recherche ouvert.
- **Échelle de production** : Le système est conçu pour une expérience « exocortex » à utilisateur unique. Aucune conception multi-utilisateur n'a encore été développée ni testée.
