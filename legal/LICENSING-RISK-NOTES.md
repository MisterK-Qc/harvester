# Notes de risque sur les licences — à lire avant de vendre Harvester

Ce document est une **analyse technique**, pas un avis juridique. Si tu vends réellement des
licences commerciales de Harvester (surtout en mode "logiciel propriétaire fermé"), fais
valider ce qui suit par un avocat spécialisé en propriété intellectuelle avant de lancer —
le risque principal ci-dessous (GPL) est un domaine où l'interprétation légale varie et où
une erreur peut être coûteuse à corriger après coup.

## 1. Le risque principal : des dépendances GPL via `spotdl`

`spotdl` (MIT) tire dans sa chaîne de dépendances des paquets copyleft :

- `mutagen` — GPL-2.0-or-later
- `pykakasi` — GPL-3.0-or-later
- `Unidecode` — GPL-2.0-or-later
- `text-unidecode` — GPL-2.0 / Artistic (double licence)

**Pourquoi ce n'est probablement pas bloquant ici :** j'ai vérifié
`app/services/spotify_harvester_service.py` — Harvester n'importe **pas** `spotdl` comme
librairie Python dans son propre processus. Il appelle le binaire `spotdl` via
`subprocess` (`SPOTDL_BINARY = "spotdl"`, `shutil.which(...)`, `cmd += ["download", ...]`).
C'est exactement le même schéma pour MKVToolNix (`mkvmerge`/`mkvpropedit`) dans
`mkvtoolnix_service.py`.

C'est important parce que la FSF elle-même distingue :
- **lier/importer** un programme GPL dans ton propre processus → crée une "œuvre combinée",
  qui doit alors être distribuée sous GPL dans son ensemble (c'est le scénario qui aurait
  posé un vrai problème pour vendre Harvester fermé) ;
- **invoquer** un programme GPL comme processus séparé, via sa ligne de commande, en lisant
  juste sa sortie → ce que la FSF appelle de la "simple agrégation" / usage comme outil
  externe, qui **n'oblige pas** le programme appelant à être sous GPL.

Harvester est dans le deuxième cas pour tous ses outils copyleft actuels (`spotdl`,
`mkvtoolnix`, `alass-cli`). Le code source de Harvester lui-même peut donc rester sous une
licence propriétaire/commerciale sans que GPL "contamine" ton code.

## 2. Ce qui reste une vraie obligation : la distribution des binaires GPL eux-mêmes

Le `Dockerfile` installe et **copie dans l'image finale** :

- `mkvtoolnix` (GPL-2.0-or-later) — via `apt-get install`
- `alass-cli` (GPL-3.0) — compilé depuis les sources Rust dans l'image
- `ffmpeg` — build Debian, généralement compilé avec des composants GPL (`libx264`, etc.),
  donc à traiter comme GPL-2.0-or-later par prudence

Dès que tu distribues l'image Docker de Harvester (Docker Hub, template Unraid, etc.), tu
distribues aussi ces binaires GPL. La GPL (art. 3 en v2, art. 6 en v3) exige alors que tu
fournisses, pour **ces programmes précis** (pas pour Harvester lui-même) :

- soit le code source correspondant,
- soit une offre écrite valable (généralement 3 ans) de fournir ce code source sur demande,
- soit — accepté en pratique quand le binaire est **non modifié** — un pointeur clair vers
  les sources officielles upstream (ce qui est ton cas : tu ne modifies ni mkvtoolnix, ni
  ffmpeg, ni alass-cli).

Concrètement, pour être couvert : garde `legal/THIRD-PARTY-NOTICES.md` et
`legal/licenses/GPL-2.0.txt` / `GPL-3.0.txt` visibles et accessibles avec chaque
distribution de l'image (README du repo, page de téléchargement, template Unraid), avec les
liens vers les dépôts upstream déjà listés dans `THIRD-PARTY-NOTICES.md` §2. Ne mets rien
dans ta propre licence commerciale qui restreindrait les droits GPL des utilisateurs sur ces
outils précis (ex. : interdire la redistribution de `mkvtoolnix` lui-même) — ta licence
propriétaire ne peut porter que sur **ton** code.

## 3. Paquet sans licence connue : `spotapi`

`pip-licenses` rapporte `spotapi` (dépendance transitive de `spotdl`) avec une licence
`UNKNOWN` — aucune métadonnée de licence n'est déclarée dans son packaging. Par défaut, en
l'absence de licence explicite, le droit d'auteur s'applique intégralement ("tous droits
réservés") : l'usage/redistribution n'est alors permis que dans la mesure où une licence est
quand même présente ailleurs (fichier `LICENSE` dans le repo GitHub, par exemple, non
remonté par les métadonnées pip).

**À faire avant une vente commerciale** : va vérifier à la main le dépôt source de `spotapi`
(lien dans `THIRD-PARTY-LICENSES-PYTHON.txt`) pour confirmer s'il y a un fichier `LICENSE`.
S'il n'y en a vraiment aucun, envisage de remplacer cette dépendance ou de la isoler dans un
sous-processus séparé (comme `spotdl` déjà) pour rester cohérent avec le reste de
l'architecture.

## 4. Résumé actionnable

| Action | Pourquoi |
|---|---|
| Garder `spotdl`, `mkvtoolnix`, `ffmpeg`, `alass-cli` appelés en sous-processus (jamais importés comme lib Python) | Évite toute "œuvre combinée" GPL avec le code propriétaire de Harvester |
| Inclure `legal/` (NOTICES + textes GPL) dans toute distribution de l'image/du produit | Obligation de la GPL pour les binaires qu'elle couvre, même non modifiés |
| Vérifier manuellement la licence de `spotapi` | Métadonnée manquante = risque non quantifié actuellement |
| Garder le nom et la marque "Harvester" neutres (déjà en cours) | Voir `TRADEMARKS.md` — éviter toute marque tierce dans le nom/logo du produit |
| Faire valider ce document par un juriste avant la mise en vente | Ceci est une analyse technique, pas un avis juridique |
