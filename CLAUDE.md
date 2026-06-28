# Maisongle

Application web de recherche immobilière en France avec scraping direct des sources d'annonces.

## Stack technique

- **Backend** : Python 3.10+ / FastAPI / uvicorn
- **Base de données** : PostgreSQL / SQLAlchemy ORM / Alembic (migrations)
- **Frontend** : Jinja2 templates / TailwindCSS / Leaflet (OpenStreetMap)
- **HTTP client** : httpx (async) ; curl_cffi pour les sources protégées par anti-bot (usurpation TLS)
- **Scraping** : selectolax (parsing HTML)

## Structure du projet

```
app/
  main.py              # Point d'entrée FastAPI, routes pages HTML
  config.py            # Settings via pydantic-settings + .env
  database.py          # Engine SQLAlchemy + session
  models/              # ORM models (Favorite, Listing/PriceHistory/Hidden/ScrapeRun, DVFCommune, LoyerCommune, ZonageCommune, SavedSearch)
  schemas/             # Pydantic schemas (validation entrée/sortie API)
  routers/             # Endpoints API REST
    search.py          # Recherche d'annonces (DB + refresh throttlé), autocomplétion villes, DVF/loyers
    listings.py        # Détail d'une annonce + masquage (POST /hide)
    favorites.py       # CRUD favoris + enrichissement (zonage ABC, géorisques)
    saved_searches.py  # CRUD recherches sauvegardées
  scrapers/            # Scrapers par plateforme (interface commune BaseScraper)
    base.py            # BaseScraper, SearchCriteria, NormalizedListing, Commune
    bienici.py         # Bien'ici (JSON ouvert, httpx)
    pap.py             # PAP / Particulier à Particulier (HTML selectolax, curl_cffi/Cloudflare)
    leboncoin.py       # Leboncoin (API JSON finder/search ; DataDome via transport)
    transport.py       # Transports anti-bot pluggables (Scrapfly Web Unlocker / cookie injecté)
    registry.py        # Enregistrement des scrapers actifs
  services/            # Logique métier
    scrape.py          # Refresh throttlé (5 min/périmètre), upsert + historique de prix
    geo.py             # Autocomplétion communes + résolution INSEE via geo.api.gouv.fr
    dedup.py           # Géohash + clé de déduplication inter-sources
    listing_view.py    # Sérialisation Listing -> shape attendue par les templates
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

# Leboncoin (optionnel, derrière DataDome) — désactivé par défaut.
# Transport "scrapfly" (recommandé) : Scrapfly Web Unlocker fournit l'IP résidentielle
# et franchit DataDome, l'IP maison n'est jamais exposée. Renseigner SCRAPFLY_API_KEY.
LEBONCOIN_ENABLED=false
LEBONCOIN_TRANSPORT=scrapfly          # "scrapfly" | "cookie"
SCRAPFLY_API_KEY=
# SCRAPFLY_COUNTRY=fr                  # exit résidentiel FR (Leboncoin est FR-only)
# SCRAPFLY_PROXY_POOL=public_residential_pool
# SCRAPFLY_RENDER_JS=false            # API JSON : pas de rendu navigateur
# Transport "cookie" (fallback manuel) : coller un cookie datadome + son User-Agent
# depuis sa propre session navigateur (lié à l'IP résidentielle de l'app).
# LEBONCOIN_DATADOME=
# LEBONCOIN_USER_AGENT=
```

## Conventions

- Langue du code : anglais (noms de variables, fonctions, classes)
- Langue des données et de l'UI : français
- Les codes INSEE de Paris/Lyon/Marseille sont normalisés (arrondissements → ville principale) via `normalize_insee_code()` dans `services/geo.py` — appliqué côté stockage (`scrape._apply`) et côté filtre de recherche
- Les favoris stockent une copie complète des données du bien en base (pas de dépendance externe pour relecture)
- L'enrichissement (zonage ABC + géorisques) est déclenché automatiquement à l'ajout en favoris

## Scraping des annonces

Le scraping direct remplace l'ancienne API Melo.io (supprimée). Voir `PLAN.md` pour l'architecture
complète et l'avancement. En résumé :

- Refresh **à la demande** au chargement de la recherche, **throttlé à 5 min par périmètre**
  (signature des critères) — pas de scheduler. Cache servi en deçà du throttle.
- Sources actives : **Bien'ici** (JSON, httpx) et **PAP** (HTML selectolax + curl_cffi pour passer
  Cloudflare). Les annonces PAP n'ont pas de coordonnées (pas de marqueur carte).
- Source optionnelle : **Leboncoin** (API JSON `finder/search`). Protégée par DataDome → franchie via un
  **transport pluggable** (`scrapers/transport.py`) : `scrapfly` (Web Unlocker, IP résidentielle + ASP,
  recommandé) ou `cookie` (datadome collé à la main, fallback). No-op tant qu'aucun transport n'est
  configuré (jamais bloquant). Une requête par commune (INSEE de la commune cherchée, comme PAP) ; les
  annonces portent des coordonnées (carte + rayon).
- **Recherche par rayon** (lat/lon/radius) : **Bien'ici + Leboncoin** (sources avec coordonnées ; PAP exclu).
  `services/geo.communes_within_radius()` énumère les communes du rayon (préfiltre par centroïdes de
  départements, filtrage haversine, plafond 60 communes) ; Bien'ici combine leurs `zoneIds` en une requête ;
  `routers/search.py` filtre les résultats par distance haversine SQL.
- Enrichissement (zonage ABC + géorisques) déclenché **uniquement à la mise en favori**.

## A faire

- **Leboncoin** : scraper + transport Scrapfly prêts et testés (mocks). **Validation live à faire** avec
  une vraie clé Scrapfly : `LEBONCOIN_ENABLED=true`, `SCRAPFLY_API_KEY=...`. Surveiller le coût/crédits
  (pool résidentiel + ASP) et le taux de succès DataDome.
- **SeLoger** : repoussé (DataDome, HTML) — réutilisera le transport Scrapfly.
