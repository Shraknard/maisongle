#!/usr/bin/env python3
"""Import DVF (prix immobiliers) data from CSV."""

import csv
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import SessionLocal, engine, Base
from app.models.dvf_commune import DVFCommune


def import_dvf(csv_path: str):
    """Import DVF data from CSV file."""
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # Clear existing data
        db.query(DVFCommune).delete()
        db.commit()
        
        count = 0
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            batch = []
            for row in reader:
                try:
                    dvf = DVFCommune(
                        insee_com=row['INSEE_COM'].zfill(5),  # Ensure 5 digits
                        annee=int(row['annee']),
                        nb_mutations=int(row['nb_mutations']) if row['nb_mutations'] else None,
                        nb_maisons=int(row['NbMaisons']) if row['NbMaisons'] else None,
                        nb_apparts=int(row['NbApparts']) if row['NbApparts'] else None,
                        prop_maison=float(row['PropMaison']) if row['PropMaison'] else None,
                        prop_appart=float(row['PropAppart']) if row['PropAppart'] else None,
                        prix_moyen=float(row['PrixMoyen']) if row['PrixMoyen'] else None,
                        prix_m2_moyen=float(row['Prixm2Moyen']) if row['Prixm2Moyen'] else None,
                        surface_moy=float(row['SurfaceMoy']) if row['SurfaceMoy'] else None,
                    )
                    batch.append(dvf)
                    count += 1
                    
                    # Batch insert every 1000 rows
                    if len(batch) >= 1000:
                        db.bulk_save_objects(batch)
                        db.commit()
                        print(f"Imported {count} communes...")
                        batch = []
                        
                except Exception as e:
                    print(f"Error on row {row}: {e}")
                    continue
            
            # Insert remaining
            if batch:
                db.bulk_save_objects(batch)
                db.commit()
        
        print(f"Successfully imported {count} communes with DVF data")
        
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    csv_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data",
        "communesdvf2024.csv"
    )
    
    if not os.path.exists(csv_path):
        print(f"File not found: {csv_path}")
        sys.exit(1)
    
    import_dvf(csv_path)
