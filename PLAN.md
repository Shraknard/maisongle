# Plan de refonte — Scraping immobilier

Remplacement de l'API Melo.io (supprimée) par du scraping direct des plateformes,
avec stockage local et rafraîchissement à la demande. Usage strictement personnel.

## Décisions d'architecture (verrouillées)

- **Base de données** : PostgreSQL conservée (DVF/loyers/zonage déjà importés, enrichissement georisques fonctionnel).
- **Frontend** : templates Jinja existants conservés (grille, carte Leaflet, favoris, calculateur).
- **Modèle de rafraîchissement** : pas de scheduler. Le scraping se déclenche **au chargement de la page**,
  avec un **throttle de 5 min par périmètre de recherche** (signature des critères). Si scrapé il y a < 5 min → on sert le cache DB.
- **Pilote du scraping** : les critères de recherche (et `saved_searches`) définissent le périmètre scrapé.
- **Sources v1** : sources ouvertes d'abord (Bien'ici JSON, PAP HTML) + autocomplétion via l'API officielle `geo.api.gouv.fr`.
  DataDome (Leboncoin / SeLoger) repoussé après validation de la chaîne complète.
- **Enrichissement** (georisques + zonage ABC) : conservé, déclenché **uniquement à la mise en favori**.

## Modèle de données

Nouvelles tables (forme normalisée alignée sur le modèle `Favorite`) :

- `listings` — annonces scrapées. Identité stable `(source, source_id)`. Champs : prix, surface, pièces,
  type (int 0=appart/1=maison…), transaction (int 0=vente/1=location), localisation, énergie, photos,
  `geohash`, `dedup_key`, `first_seen`, `last_seen`, `active`.
- `price_history` — historique de prix par annonce (`listing_id`, `price`, `observed_at`).
- `hidden` — annonces masquées définitivement, keyées sur `(source, source_id)`, jamais réaffichées.
- `scrape_runs` — journal des runs (signature, timestamps, statut, compteurs, détail par source) + support du throttle.

## Avancement

### 1. Fondation backend
- [x] Exploration du code existant + validation des endpoints Bien'ici réels
- [x] Modèles `Listing`, `PriceHistory`, `Hidden`, `ScrapeRun` (+ enregistrement dans `models/__init__.py`)
- [x] Interface commune des scrapers (`BaseScraper`, `SearchCriteria`, `NormalizedListing`, `Commune`)
- [x] Service de géohash + clé de déduplication (`services/dedup.py`)
- [x] Service `geo.py` — autocomplétion communes + résolution INSEE→commune via `geo.api.gouv.fr`
- [x] Service `scrape.py` — throttle 5 min, run isolé par source, upsert + historique de prix
- [x] Scraper **Bien'ici** (JSON ouvert) + registry

### 2. Câblage API + UI
- [ ] Réécriture `routers/search.py` : `GET /api/search/` sert les listings depuis la DB + déclenche le refresh throttlé
- [ ] `routers/search.py` : `GET /locations` rebranché sur `geo.api.gouv.fr`
- [ ] Nouveau `routers/listings.py` : détail annonce (remplace `properties.py`) + `POST /hide`
- [ ] `routers/favorites.py` : mise en favori par lookup du listing en DB (copie complète + enrichissement)
- [ ] `main.py` : enregistrement du router listings
- [ ] Frontend `index.html` : bouton « masquer », badge source, indicateur de baisse de prix
- [ ] Frontend `property.html` : détail servi depuis la DB
- [ ] Adaptation `saved_searches` (INSEE au lieu des reliquats Melo `city_id`)

### 3. Sources supplémentaires
- [ ] Scraper **PAP** (HTML via selectolax)
- [ ] Recherche par rayon (lat/lon/radius) pour Bien'ici
- [ ] *Plus tard* : Leboncoin / SeLoger (DataDome via curl_cffi / Camoufox)

### 4. Finitions
- [ ] Mise à jour `requirements.txt` + `install.sh`
- [ ] Mise à jour `README.md` et `CLAUDE.md`
- [ ] Nettoyage des reliquats Melo (config, schémas)
