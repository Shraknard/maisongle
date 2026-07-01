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
    seloger.py         # SeLoger (HTML selectolax-free ; blob JSON embarqué ; DataDome via transport)
    transport.py       # Transports anti-bot pluggables (post_json/get_text ; Scrapfly / cookie injecté)
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
# Transport "cookie" (fallback manuel) : coller un cookie datadome + son User-Agent
# depuis sa propre session navigateur (lié à l'IP résidentielle de l'app).
# LEBONCOIN_DATADOME=
# LEBONCOIN_USER_AGENT=

# SeLoger (optionnel, derrière DataDome, HTML) — désactivé par défaut. Même transport
# partagé (SCRAPFLY_API_KEY ci-dessus). Scaffolding : contrat des données à valider en live.
SELOGER_ENABLED=false
SELOGER_TRANSPORT=scrapfly             # "scrapfly" | "cookie"
# SELOGER_RENDER_JS=false              # true seulement si retours vides (DataDome non franchi)
# SELOGER_DATADOME=                    # transport "cookie" (fallback manuel)
# SELOGER_USER_AGENT=
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
- **Backfill au 1er scrape, incrémental ensuite** : `SearchCriteria.first_scrape` (posé par
  `services/scrape` quand aucun run n'existe pour le périmètre, hors signature) fait paginer en profondeur
  la 1ʳᵉ fois (Leboncoin jusqu'à 100 pages = plafond LBC ~3500 ; Bien'ici jusqu'à 24 = 2400), puis seulement
  les pages les plus récentes (5) aux refresh. La pagination s'arrête au total réel → une recherche filtrée
  backfille entièrement à bas coût. Leboncoin plafonne à 3500/requête : couverture plus large = filtres plus fins.
- Sources actives : **Bien'ici** (JSON, httpx) et **PAP** (HTML selectolax + curl_cffi pour passer
  Cloudflare). Les annonces PAP n'ont pas de coordonnées (pas de marqueur carte).
- Source optionnelle : **Leboncoin** (API JSON `finder/search`, **validée en live**). Protégée par DataDome
  → franchie via un **transport pluggable** (`scrapers/transport.py`) : `scrapfly` (Web Unlocker, IP
  résidentielle + ASP, recommandé) ou `cookie` (datadome collé à la main, fallback). No-op tant qu'aucun
  transport n'est configuré (jamais bloquant). Spécificités :
  - Localisation : `locationType: "city"` avec `area: {lat, lng, radius}` (un point + rayon, **pas** un
    polygone de commune ; un objet plat `{city, lat, lng}` renvoie 0). Le rayon (`COMMUNE_RADIUS_M`, 10 km)
    ramène les communes voisines → « commune et alentours », taggées avec l'INSEE cherché (comme PAP).
  - Les annonces portent des coordonnées (carte + rayon). La **recherche par rayon** fait **une seule**
    requête `area` au centre (bien moins coûteux qu'une requête par commune énumérée).
  - DataDome fait du **blocage furtif** (HTTP 200 + résultats vides) → retry de la 1ʳᵉ page
    (`FIRST_PAGE_ATTEMPTS`), chaque retry passant par une IP résidentielle fraîche.
  - `render_js` reste `False` (Scrapfly refuse le rendu JS en POST ; endpoint JSON de toute façon).
- Source optionnelle : **SeLoger** (HTML, DataDome, **validée en live**). Même transport que Leboncoin, via
  le protocole `get_text` (GET/HTML). `list.htm` est mort (HTTP 500) → recherche par URLs *slug*
  `/immobilier/{achat|location}/immo-{ville}-{dept}/bien-{type}/`, une requête par (commune, type) ;
  `_slugify()` construit le slug depuis le nom de commune, `bien-{type}` filtre le type (défaut appartement +
  maison). Budget/surface/pièces filtrés côté lecture (DB), pas dans l'URL. Données embarquées :
  `window["__UFRN_FETCHER__"] = JSON.parse("…")` → `pageProps.classifieds` (IDs) résolus via
  `pageProps.classifiedsData` (extraction sous-chaîne + double `json.loads`, accents préservés). **Comme
  PAP : pas de coordonnées** → pas de marqueur carte, INSEE = commune cherchée, **exclu du rayon**.
  **Pagination impossible** (SERP en SPA : le serveur ne rend que la 1ʳᵉ page, aucun param d'URL ne pagine) →
  **30 annonces récentes par (commune, type)**, pas de backfill. No-op tant que `SELOGER_ENABLED` est faux /
  aucun transport configuré ; `SELOGER_RENDER_JS` inutile (ASP seul suffit).
- **Recherche par rayon** (lat/lon/radius) : **Bien'ici + Leboncoin** (sources avec coordonnées ; PAP et
  SeLoger exclus — les runs rayon passent `sources=["bienici","leboncoin"]`).
  `services/geo.communes_within_radius()` énumère les communes du rayon (préfiltre par centroïdes de
  départements, filtrage haversine, plafond 60 communes) ; Bien'ici combine leurs `zoneIds` en une requête ;
  `routers/search.py` filtre les résultats par distance haversine SQL.
- Enrichissement (zonage ABC + géorisques) déclenché **uniquement à la mise en favori**.

## A faire

- **Leboncoin** : scraper + transport Scrapfly prêts et testés (mocks). **Validation live à faire** avec
  une vraie clé Scrapfly : `LEBONCOIN_ENABLED=true`, `SCRAPFLY_API_KEY=...`. Surveiller le coût/crédits
  (pool résidentiel + ASP) et le taux de succès DataDome.
- **SeLoger** : **validé en live** (Scrapfly réel, Bordeaux → 30 annonces, tous champs cœur). Désactivé par
  défaut (`SELOGER_ENABLED=false`) — l'activer (`SELOGER_ENABLED=true`) fait dépenser des crédits Scrapfly à
  chaque recherche, comme Leboncoin. Limite connue : **30 annonces/(commune, type)** (le SERP est une SPA, la
  pagination HTML n'existe plus ; les pages suivantes passeraient par l'API interne `classified-search`, non
  implémentée). Surveiller le coût/crédits si activé.
