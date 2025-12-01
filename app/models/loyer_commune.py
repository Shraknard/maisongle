from sqlalchemy import Column, Integer, String, Float
from app.database import Base


class LoyerCommune(Base):
    """Loyers moyens par commune (données Carte des Loyers)."""
    __tablename__ = "loyers_communes"
    
    id = Column(Integer, primary_key=True, index=True)
    insee_com = Column(String(5), index=True, nullable=False)
    type_bien = Column(String(20), nullable=False)  # 'appartement', 'app_t1t2', 'app_t3plus', 'maison'
    
    loyer_m2 = Column(Float)  # Loyer prédit au m² charges comprises
    loyer_m2_min = Column(Float)  # Borne basse intervalle de prédiction
    loyer_m2_max = Column(Float)  # Borne haute intervalle de prédiction
    
    type_pred = Column(String(10))  # 'commune' ou 'maille'
    nb_obs_commune = Column(Integer)  # Nombre d'observations dans la commune
    nb_obs_maille = Column(Integer)  # Nombre d'observations dans la maille
    r2_adj = Column(Float)  # Coefficient de détermination ajusté
    
    # Contrainte unique sur insee_com + type_bien
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )
