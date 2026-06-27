# Maisongle

Application web de recherche immobilière en France avec scraping direct des sources d'annonces.

## Stack technique

- **Backend** : Python 3.10+ / FastAPI / uvicorn
- **Base de données** : PostgreSQL / SQLAlchemy ORM / Alembic (migrations)
- **Frontend** : Jinja2 templates / TailwindCSS / Leaflet (OpenStreetMap)
- **HTTP client** : httpx (async)

## Structure du projet

```
app/
  main.py              # Point d'entrée FastAPI, routes pages HTML
  config.py            # Settings via pydantic-settings + .env
  database.py          # Engine SQLAlchemy + session
  models/              # ORM models (Favorite, DVFCommune, LoyerCommune, ZonageCommune, SavedSearch)
  schemas/             # Pydantic schemas (validation entrée/sortie API)
  routers/             # Endpoints API REST
    search.py          # DVF (prix) et loyers par commune (données locales)
    favorites.py       # CRUD favoris + enrichissement (zonage ABC, géorisques)
    saved_searches.py  # CRUD recherches sauvegardées
  services/            # Logique métier
    enrichment.py      # Orchestration enrichissement (zonage + géorisques)
    georisques.py      # Client API Géorisques (risques naturels/technologiques)
    zonage.py          # Lookup zonage ABC Pinel en base
  templates/           # Pages HTML Jinja2 (index, favorites, property, calculator, documentation)
scripts/               # Import de données (DVF, loyers, zonage) depuis CSV/Excel vers PostgreSQL
data/                  # Fichiers CSV/Excel sources (DVF, loyers, zonage ABC)
```

## Commandes

```bash
# Installer
./install.sh

# Lancer le serveur de dev
source venv/bin/activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Importer les données
python scripts/import_zonage.py
python scripts/import_dvf.py
python scripts/import_loyers.py
```

## Configuration

Fichier `.env` à la racine :

```env
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maisongle
GEORISQUES_API_BASE_URL=https://georisques.gouv.fr/api/v1
```

## Conventions

- Langue du code : anglais (noms de variables, fonctions, classes)
- Langue des données et de l'UI : français
- Les codes INSEE de Paris/Lyon/Marseille sont normalisés (arrondissements → ville principale) via `normalize_insee_code()` dans `routers/search.py`
- Les favoris stockent une copie complète des données du bien en base (pas de dépendance externe pour relecture)
- L'enrichissement (zonage ABC + géorisques) est déclenché automatiquement à l'ajout en favoris

## A faire

La recherche et le scraping des annonces immobilières (Leboncoin, SeLoger, etc.) ne sont pas encore implémentés. L'ancien système utilisait l'API Melo.io (supprimée car trop cher). Les endpoints de recherche d'annonces et d'autocomplétion des villes sont à recoder avec du scraping direct.
