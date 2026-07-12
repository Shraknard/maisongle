"""Client + cache pour bien-dans-ma-ville.fr (données socio-démographiques).

Source d'enrichissement au niveau *commune* (comme Géorisques/DVF), pas une
source d'annonces : elle ne suit donc pas l'interface ``BaseScraper``. On extrait
trois blocs depuis la page HTML server-rendered d'une commune :

- ``population_stats`` : la grille "Statistiques sur la population"
  (nombre d'habitants, âge moyen, population active, taux de chômage, densité,
  revenu moyen) — lue depuis ``table.bloc_chiffre``.
- ``population_evolution`` : le graphe "Evolution du nombre d'habitants"
  (canvas ``#chart_evolution``, attribut ``data-data`` + années dans l'aria-label).
- ``infractions_evolution`` : le graphe "Evolution du nombre d'infractions"
  (canvas ``#chart_infraction``, attributs ``data-data1..4`` = 4 séries).

L'URL est ``{base}/{slug}-{insee}/``. Le site **résout par code INSEE** et
ignore le slug (un slug erroné renvoie quand même la bonne commune) → un slug
approximatif construit depuis le nom de commune suffit, et le lien reste propre
pour l'utilisateur. Les données sont mises en cache par INSEE (table
``commune_stats``) avec un TTL, et le fetch est non bloquant (échec → None ou
cache périmé).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from selectolax.parser import HTMLParser
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.commune_stats import CommuneStats

# Libellés des 4 séries du graphe d'infractions (data-data1..4), dans l'ordre.
_INFRACTION_LABELS = [
    "Agressions physiques / sexuelles",
    "Cambriolages",
    "Vols / dégradations",
    "Stupéfiants",
]

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) "
    "Gecko/20100101 Firefox/120.0"
)


def _strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()


def _slugify(name: str) -> str:
    """Nom de commune -> slug d'URL ('Saint-Étienne' -> 'saint-etienne')."""
    s = _strip_accents(name).lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _json_list(raw: Optional[str]) -> List[float]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except (ValueError, TypeError):
        return []


class BienDansMaVilleService:
    """Récupère et met en cache les statistiques communales de bien-dans-ma-ville."""

    def __init__(self):
        settings = get_settings()
        self.enabled = settings.bien_dans_ma_ville_enabled
        self.base_url = settings.bien_dans_ma_ville_base_url.rstrip("/")
        self.cache_days = settings.bien_dans_ma_ville_cache_days

    def build_url(self, insee: str, city_name: Optional[str]) -> str:
        slug = _slugify(city_name or "") or "ville"
        return f"{self.base_url}/{slug}-{insee}/"

    # -- Parsing ---------------------------------------------------------------

    @staticmethod
    def _parse(html: str) -> Optional[Dict[str, Any]]:
        tree = HTMLParser(html)
        out: Dict[str, Any] = {}

        # Statistiques sur la population (table.bloc_chiffre : th=label, td=valeur)
        stats: List[Dict[str, str]] = []
        table = tree.css_first("table.bloc_chiffre")
        if table:
            for tr in table.css("tr"):
                th = tr.css_first("th")
                td = tr.css_first("td")
                if th and td:
                    label = th.text(strip=True)
                    value = td.text(strip=True)
                    if label and value:
                        stats.append({"label": label, "value": value})
        if stats:
            out["population_stats"] = stats

        # Evolution du nombre d'habitants (#chart_evolution)
        ev = tree.css_first("#chart_evolution")
        if ev is not None:
            values = _json_list(ev.attributes.get("data-data"))
            if values:
                aria = ev.attributes.get("aria-label") or ""
                m = re.search(r"de\s+(\d{4})\s+[àa]\s+(\d{4})", aria)
                if m:
                    start = int(m.group(1))
                    years = list(range(start, start + len(values)))
                else:
                    end = datetime.now().year
                    years = list(range(end - len(values) + 1, end + 1))
                out["population_evolution"] = {"years": years, "values": values}

        # Evolution du nombre d'infractions (#chart_infraction, data-data1..4)
        inf = tree.css_first("#chart_infraction")
        if inf is not None:
            series: List[Dict[str, Any]] = []
            for i, label in enumerate(_INFRACTION_LABELS, start=1):
                values = _json_list(inf.attributes.get(f"data-data{i}"))
                if values:
                    series.append({"label": label, "values": values})
            if series:
                n = max(len(s["values"]) for s in series)
                end = BienDansMaVilleService._infraction_end_year(inf)
                years = list(range(end - n + 1, end + 1))
                out["infractions_evolution"] = {"years": years, "series": series}

        return out or None

    @staticmethod
    def _infraction_end_year(canvas_node) -> int:
        """Année la plus récente du graphe d'infractions.

        Ancrée sur la mention de source ("... portant sur l'année AAAA") de la
        section délinquance qui contient le canvas ; défaut = année précédente.
        """
        section = canvas_node
        while section is not None and section.tag != "section":
            section = section.parent
        if section is not None:
            years = re.findall(
                r"portant sur l['’]ann[ée]e\s+(\d{4})", section.text() or ""
            )
            if years:
                return max(int(y) for y in years)
        return datetime.now().year - 1

    # -- Fetch + cache ---------------------------------------------------------

    async def _fetch_live(self, insee: str, city_name: Optional[str]) -> Optional[Dict[str, Any]]:
        url = self.build_url(insee, city_name)
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=True, headers={"User-Agent": _UA}
            ) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return None
            parsed = self._parse(resp.text)
            if not parsed:
                return None
            parsed["source_url"] = url
            return parsed
        except Exception:
            # Non bloquant : la page d'annonce s'affiche sans ce bloc.
            return None

    async def get_stats(
        self, db: Session, insee: Optional[str], city_name: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Retourne les stats communales (cache DB, fetch si absent/périmé)."""
        if not self.enabled or not insee:
            return None
        # Lazy import: app.services.geo pulls in the scrapers package, which
        # imports geo back — a top-level import here would trip that cycle.
        from app.services.geo import normalize_insee_code

        insee = normalize_insee_code(insee)

        cached = db.query(CommuneStats).filter(CommuneStats.insee == insee).first()
        if cached and cached.payload and self._is_fresh(cached.fetched_at):
            return self._with_url(cached)

        parsed = await self._fetch_live(insee, city_name or (cached.city_name if cached else None))
        if not parsed:
            # Échec du fetch : on sert le cache même périmé s'il existe.
            return self._with_url(cached) if cached and cached.payload else None

        source_url = parsed.pop("source_url", self.build_url(insee, city_name))
        now = datetime.now(timezone.utc)
        if cached:
            cached.payload = parsed
            cached.city_name = city_name or cached.city_name
            cached.source_url = source_url
            cached.fetched_at = now
        else:
            cached = CommuneStats(
                insee=insee,
                city_name=city_name,
                source_url=source_url,
                payload=parsed,
                fetched_at=now,
            )
            db.add(cached)
        db.commit()
        return self._with_url(cached)

    def _is_fresh(self, fetched_at) -> bool:
        if not fetched_at:
            return False
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - fetched_at < timedelta(days=self.cache_days)

    @staticmethod
    def _with_url(row: CommuneStats) -> Dict[str, Any]:
        data = dict(row.payload or {})
        data["source_url"] = row.source_url
        return data
