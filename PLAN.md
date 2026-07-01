# Plan de refonte — Scraping immobilier

Remplacement de l'API Melo.io (supprimée) par du scraping direct des plateformes,
avec stockage local et rafraîchissement à la demande. Usage strictement personnel.

## Décisions d'architecture (verrouillées)

- **Base de données** : PostgreSQL conservée (DVF/loyers/zonage déjà importés, enrichissement georisques fonctionnel).
- **Frontend** : templates Jinja existants conservés (grille, carte Leaflet, favoris, calculateur).
- **Modèle de rafraîchissement** : pas de scheduler. Le scraping se déclenche **au chargement de la page**,
  avec un **throttle de 5 min par périmètre de recherche** (signature des critères). Si scrapé il y a < 5 min → on sert le cache DB.
- **Pilote du scraping** : les critères de recherche (et `saved_searches`) définissent le périmètre scrapé.
- **Sources v1** : Bien'ici (JSON ouvert, `httpx`) + PAP (HTML `selectolax`) + autocomplétion via l'API officielle `geo.api.gouv.fr`.
  PAP est désormais derrière Cloudflare → le scraper PAP utilise `curl_cffi` (usurpation d'empreinte TLS Chrome) au lieu de `httpx`.
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
- [x] Réécriture `routers/search.py` : `GET /api/search/` sert les listings depuis la DB + déclenche le refresh throttlé
- [x] `routers/search.py` : `GET /locations` rebranché sur `geo.api.gouv.fr`
- [x] Nouveau `routers/listings.py` : détail annonce (remplace `properties.py`) + `POST /hide`
- [x] `routers/favorites.py` : mise en favori par lookup du listing en DB (copie complète + enrichissement)
- [x] `main.py` : enregistrement du router listings
- [x] Frontend `index.html` : bouton « masquer », indicateur de baisse de prix
- [x] Frontend `property.html` : détail servi depuis la DB (`/api/listings/{uuid}`)
- [x] Adaptation `saved_searches` (INSEE via `selected_cities`, retrait des reliquats Melo `city_id`/`city_name`/`city_insee`/`zipcode`)
- [x] Test d'intégration du pipeline (scrape → DB → throttle → baisse de prix → masquage) ✅

### 3. Sources supplémentaires
- [x] Scraper **PAP** (HTML via selectolax + `curl_cffi` pour franchir Cloudflare) — résolution commune→geo id via `ac-geo`,
  pagination en suivant le lien « Suivante » (stop avant `/proximite`), URL par type (vente : pluriel ; location : singulier).
  Limites : pas de coordonnées (absent de la carte) ; INSEE = commune recherchée ; loc. limitée aux types appartement/maison/local-commercial.
- [x] **Recherche par rayon (lat/lon/radius)** — Bien'ici uniquement (besoin de coordonnées).
  L'API `realEstateAds.json` ne filtre que par `zoneIds` (pas de bbox/polygon/rayon), donc :
  - `communes_within_radius()` (`services/geo.py`) énumère les communes du rayon. geo.api.gouv n'expose pas la
    géométrie des départements → table statique des centroïdes (`_DEPT_CENTROIDS`) pour préfiltrer les départements
    candidats, puis filtrage précis des communes par distance (haversine). La commune hôte est toujours incluse
    (cas des grandes communes dont le centre tombe hors rayon). Plafond de **60 communes** (plus proches) par run.
  - Bien'ici combine désormais tous les `zoneIds` des communes en **une seule requête paginée** (dédup des zones,
    cache `INSEE→zoneIds`, résolution concurrente). Plafond inchangé de 500 annonces/run.
  - Côté lecture, `routers/search.py` filtre par distance haversine SQL (préfiltre bbox + cercle exact), pas par INSEE.
  - PAP est exclu du rayon (pas de coordonnées). Au passage : correctif `_int()` pour les `roomsQuantity`/`floor`
    renvoyés en plage (`[2, 4]`, programmes neufs) qui cassaient l'insert sur colonne entière.
- [x] Scraper **Leboncoin** (API JSON `finder/search`) — une requête par commune (tag INSEE de la commune
  cherchée, comme PAP) ; contrairement à PAP, les annonces portent des coordonnées → carte + recherche
  par rayon. Mapping catégorie (vente 9 / location 10), `real_estate_type` et `attributes`
  (square/rooms/bedrooms/energy_rate/ges).
- [x] **Transport anti-bot pluggable** (`scrapers/transport.py`) pour franchir DataDome, source no-op tant
  qu'aucun transport n'est configuré (ne casse jamais une recherche). Deux protocoles : `post_json`
  (POST + réponse JSON, Leboncoin) et `get_text` (GET + corps brut HTML, SeLoger). Deux implémentations :
  - `scrapfly` (**recommandé**) — Scrapfly Web Unlocker : requête `finder/search` routée via leur API
    (`asp=true`, `proxy_pool=public_residential_pool`, `country=fr`, `render_js=false`). IP résidentielle
    fournie par Scrapfly → **IP maison jamais exposée**, DataDome franchi côté Scrapfly, **zéro cookie/proxy
    à gérer**. `SCRAPFLY_API_KEY` requis. Volume throttlé (5 min) → coût ~quelques cents/mois.
  - `cookie` (fallback) — `curl_cffi` + cookie `datadome` collé à la main (lié à l'IP, refresh manuel).
  - **Validé en live** (clé Scrapfly réelle) : DataDome franchi (HTTP 200, vraies annonces lyonnaises,
    parsing complet prix/surface/pièces/DPE/coords/particulier-vs-agence). Apprentissages :
    - Localisation : `locationType:"city"` + `area:{lat,lng,radius}` (point + rayon, **pas** polygone) —
      un objet plat `{city,lat,lng}` renvoie 0. Rayon `COMMUNE_RADIUS_M` (10 km) → « commune et alentours ».
      Recherche par rayon = **une seule** requête `area` au centre.
    - DataDome **blocage furtif** (HTTP 200 + 0 résultat) → retry 1ʳᵉ page (`FIRST_PAGE_ATTEMPTS`),
      chaque retry sur IP résidentielle fraîche. `render_js` interdit en POST → reste `False`.
    - Constat initial : en pur HTTP depuis une IP datacenter, DataDome renvoie 403 `x-datadome: protected`
      + `captcha-delivery.com` et flague l'IP en quelques requêtes → d'où le routage Scrapfly.
- [x] Scraper **SeLoger** (`scrapers/seloger.py`, HTML DataDome) — **scaffolding, validation live à faire**.
  Réutilise le transport (nouveau `get_text` GET/HTML). Une requête `list.htm` par commune
  (`places=[{"inseeCodes":[…]}]`, filtre INSEE — pas de slug), pagination `LISTING-LISTpg`. Les résultats
  sont un blob JSON embarqué (`window["initialData"] = JSON.parse("…")`) : on l'extrait, on décode le
  littéral JS (double `json.loads`, accents préservés), on lit `datasets[i].cards.list` en gardant
  `cardType == "classified"`. Comme PAP : **pas de coordonnées** sur les cartes de recherche (elles ne sont
  que sur les pages détail via `__NEXT_DATA__`) → pas de marqueur carte, INSEE = commune cherchée ; **exclu
  du rayon** (les runs rayon passent `sources=["bienici","leboncoin"]`). No-op tant que `SELOGER_ENABLED`
  est faux ou qu'aucun transport n'est configuré. Testé sur mocks (extraction/normalisation/URL/no-op).
  - **Contrat provisoire** (noms de champs `estateType`/`epc`, codes `types`, métadonnées pagination,
    champ `photos`) reconstitué depuis des références publiques — à ajuster à la 1ʳᵉ validation live
    (`SELOGER_ENABLED=true`, `SCRAPFLY_API_KEY=…` ; passer `SELOGER_RENDER_JS=true` si retours vides/bloqués).
- [ ] *Suivi Leboncoin* : surveiller coût/crédits Scrapfly (résidentiel + ASP + retries) et taux de
  blocage furtif ; ajuster `COMMUNE_RADIUS_M` / `FIRST_PAGE_ATTEMPTS` si besoin.

### 4. Finitions
- [x] Mise à jour `requirements.txt` (selectolax)
- [x] Mise à jour `README.md`
- [x] Mise à jour `CLAUDE.md` (section « À faire »)
- [x] Nettoyage des reliquats Melo (`saved_searches.city_id` & co. — schémas favoris déjà normalisés ; migration `scripts/migrate_saved_searches.py`)
