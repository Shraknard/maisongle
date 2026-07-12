from sqlalchemy import Column, Integer, String, DateTime, JSON
from sqlalchemy.sql import func
from app.database import Base


class CommuneStats(Base):
    """Cache par commune des données socio-démographiques de bien-dans-ma-ville.fr.

    Ces données (statistiques de population, évolution des habitants et des
    infractions) sont identiques pour toutes les annonces d'une même commune et
    changent rarement (mise à jour annuelle) → mises en cache par INSEE avec un
    horodatage pour un rafraîchissement throttlé (``bien_dans_ma_ville_cache_days``).
    """
    __tablename__ = "commune_stats"

    id = Column(Integer, primary_key=True, index=True)
    insee = Column(String(5), unique=True, index=True, nullable=False)
    city_name = Column(String(255), nullable=True)
    source_url = Column(String(500), nullable=True)
    # Payload complet parsé (population_stats, population_evolution,
    # infractions_evolution). Voir services/bien_dans_ma_ville.py.
    payload = Column(JSON, nullable=True)
    fetched_at = Column(DateTime(timezone=True), server_default=func.now())
