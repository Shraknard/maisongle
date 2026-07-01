# Maisongle

Application de recherche immobilière avec enrichissement des données. Scraping direct des annonces depuis les sites immobiliers français.

## Stack technique

- **Backend** : Python 3.10+ / FastAPI / uvicorn
- **Base de données** : PostgreSQL
- **Frontend** : Jinja2 templates / TailwindCSS / Leaflet (OpenStreetMap)

## Installation rapide

```bash
cd maisongle

# Créer la base PostgreSQL
sudo -u postgres psql -c "CREATE DATABASE maisongle;"

# Lancer l'installation
./install.sh
```

Le script `install.sh` crée l'environnement virtuel, installe les dépendances, génère le `.env` et propose d'importer les données.

## Installation manuelle

### 1. Environnement Python

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration

Créer un fichier `.env` :

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maisongle
GEORISQUES_API_BASE_URL=https://georisques.gouv.fr/api/v1
```

### 3. Base de données

```bash
sudo -u postgres psql -c "CREATE DATABASE maisongle;"
```

## Données externes

### Zonage ABC (Pinel)

Fichier inclus : `data/zonage_abc.xlsx`

```bash
python scripts/import_zonage.py
```

### Prix immobiliers (DVF)

Source : [data.gouv.fr - Indicateurs immobiliers](https://www.data.gouv.fr/datasets/indicateurs-immobiliers-par-commune-et-par-annee-prix-et-volumes-sur-la-periode-2014-2024)

1. Télécharger le CSV le plus récent
2. Le renommer en `communesdvf2024.csv` et le placer dans `/data`
3. Importer :

```bash
python scripts/import_dvf.py
```

### Loyers moyens (Carte des Loyers)

Source : [data.gouv.fr - Carte des Loyers 2024](https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2024/)

Télécharger les 4 fichiers CSV et les placer dans `/data` :
- `pred-app-mef-dhup.csv` (appartements tous types)
- `pred-app12-mef-dhup.csv` (appartements T1-T2)
- `pred-app3-mef-dhup.csv` (appartements T3+)
- `pred-mai-mef-dhup.csv` (maisons)

```bash
python scripts/import_loyers.py
```

### Géorisques

Données récupérées automatiquement via l'API : https://www.georisques.gouv.fr/doc-api#/

## Recherche & scraping des annonces

Les annonces sont récupérées par **scraping direct** des plateformes (ancien système Melo.io
supprimé). Architecture :

- **Stockage local** : les annonces sont normalisées et stockées en base (`listings`), avec
  identité stable `(source, source_id)`, historique de prix (`price_history`) et liste de masquage
  (`hidden`).
- **Rafraîchissement à la demande** : pas de scheduler. Le scraping se déclenche au chargement de
  la page de recherche, mais est **throttlé à 5 minutes par périmètre de recherche** : si le même
  périmètre a été scrapé il y a moins de 5 min, on sert directement le cache en base.
- **Autocomplétion des villes** : via l'API officielle [geo.api.gouv.fr](https://geo.api.gouv.fr)
  (nom, code INSEE, code postal, coordonnées).
- **Sources** : architecture modulaire (`app/scrapers/`), une classe par plateforme avec une
  sortie normalisée commune. Sources actives :
  - **Bien'ici** (endpoints JSON ouverts, via `httpx`).
  - **PAP** (Particulier à Particulier) — HTML parsé avec `selectolax`. Le site est protégé par
    Cloudflare, donc le scraper utilise `curl_cffi` avec usurpation d'empreinte TLS Chrome. Les
    annonces PAP n'ont pas de coordonnées (absentes des cartes de résultats) : elles sont rattachées
    à l'INSEE de la commune recherchée pour le filtrage, mais n'apparaissent pas sur la carte.

  Sources optionnelles (derrière DataDome, désactivées par défaut, franchies via un **transport
  pluggable** — Scrapfly Web Unlocker recommandé) :
  - **Leboncoin** (API JSON `finder/search`, validée en live) — annonces géolocalisées (carte + rayon).
  - **SeLoger** (HTML `list.htm`, blob JSON embarqué) — *scaffolding, validation live à faire* ; comme
    PAP, pas de coordonnées sur les cartes de recherche.

  Ajouter une source = déposer un module et l'enregistrer dans `app/scrapers/registry.py`.

> À exécuter depuis une **IP résidentielle** : les plateformes protégées (Leboncoin, SeLoger via
> DataDome) bannissent rapidement les IP datacenter.

## Lancer l'application

```bash
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

L'application sera accessible sur http://localhost:8000

## Fonctionnalités

- Agrégation d'annonces par scraping direct, stockées et dédoublonnées en local
- Rafraîchissement à la demande, throttlé à 5 min par périmètre de recherche
- Historique de prix et indicateur de baisse depuis la première vue
- Masquage définitif d'une annonce (ne réapparaît jamais, même après re-scrape)
- Carte interactive avec POIs (transports, commerces, écoles...)
- Favoris avec enrichissement automatique (zonage ABC, géorisques)
- Indicateur de prix vs moyenne commune (DVF pour ventes, Carte des Loyers pour locations)
- Sauvegarde des recherches
- Simulateur de prêt immobilier
