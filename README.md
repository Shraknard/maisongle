# Maisongle

Application de recherche immobilière avec enrichissement des données.

## Stack technique

- **Backend**: Python 3.10+ / FastAPI / uvicorn
- **Base de données**: PostgreSQL
- **Frontend**: Jinja2 templates / TailwindCSS / Leaflet (OpenStreetMap)

## Installation rapide

```bash
# 1. Cloner le projet et se placer dans le dossier
cd maisongle

# 2. Configurer PostgreSQL
sudo -u postgres psql -c "CREATE DATABASE maisongle;"

# 3. Lancer le script d'installation
./install.sh
```

Le script `install.sh` :
- Crée l'environnement virtuel Python
- Installe les dépendances
- Crée le fichier `.env` si nécessaire
- Vérifie les fichiers de données
- Propose d'importer les données

## Installation manuelle

### 1. Environnement Python

```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### 2. Configuration

Créer un fichier `.env` et remplir la clé d'API Melo:

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maisongle
MELO_API_BASE_URL=https://api.notif.immo
MELO_API_KEY=votre_cle_api
GEORISQUES_API_BASE_URL=https://georisques.gouv.fr/api/v1
```

**Clé API Melo** (30€/mois) : https://www.melo.io/settings?apikeys

### 3. Base de données PostgreSQL

```bash
sudo -u postgres psql
CREATE DATABASE maisongle;
\q
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
Données récupérées via l'API : https://www.georisques.gouv.fr/doc-api#/

Aucune action requise - les données sont récupérées automatiquement pour les favoris.

## Lancer l'application

```bash
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

L'application sera accessible sur http://localhost:8000

## Fonctionnalités

- 🔍 Recherche d'annonces immobilières (vente/location)
- 📍 Carte interactive avec POIs (transports, commerces, écoles...)
- ⭐ Favoris avec enrichissement (zonage ABC, géorisques)
- 📊 Indicateur de prix vs moyenne commune (DVF pour ventes, Carte des Loyers pour locations)
- 🏠 Multi-sélection des types de biens
- 📏 Recherche par rayon géographique
- 💾 Sauvegarde des recherches
- 🧮 Simulateur de prêt immobilier