from sqlalchemy import Column, Integer, String, Float
from app.database import Base


class DVFCommune(Base):
    """Prix immobiliers par commune (données DVF)."""
    __tablename__ = "dvf_communes"
    
    id = Column(Integer, primary_key=True, index=True)
    insee_com = Column(String(5), unique=True, index=True, nullable=False)
    annee = Column(Integer, nullable=False)
    nb_mutations = Column(Integer)
    nb_maisons = Column(Integer)
    nb_apparts = Column(Integer)
    prop_maison = Column(Float)  # % de maisons
    prop_appart = Column(Float)  # % d'appartements
    prix_moyen = Column(Float)   # Prix moyen en €
    prix_m2_moyen = Column(Float)  # Prix au m² moyen
    surface_moy = Column(Float)  # Surface moyenne en m²
