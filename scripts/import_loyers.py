#!/usr/bin/env python3
"""Import des données de loyers depuis les fichiers CSV de la Carte des Loyers."""

import csv
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal, engine, Base
from app.models.loyer_commune import LoyerCommune


def parse_french_float(value: str) -> float:
    """Parse a French-formatted float (comma as decimal separator)."""
    if not value or value == 'NA':
        return None
    return float(value.replace(',', '.'))


def import_loyers_file(db, filepath: Path, type_bien: str):
    """Import a single loyers CSV file."""
    print(f"Importing {filepath.name} as {type_bien}...")
    
    count = 0
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        # CSV with semicolon separator
        reader = csv.DictReader(f, delimiter=';')
        
        for row in reader:
            insee = row.get('INSEE_C', '').strip().strip('"')
            if not insee:
                continue
            
            loyer = LoyerCommune(
                insee_com=insee.zfill(5),
                type_bien=type_bien,
                loyer_m2=parse_french_float(row.get('loypredm2', '')),
                loyer_m2_min=parse_french_float(row.get('lwr.IPm2', '')),
                loyer_m2_max=parse_french_float(row.get('upr.IPm2', '')),
                type_pred=row.get('TYPPRED', '').strip().strip('"'),
                nb_obs_commune=int(row.get('nbobs_com', 0) or 0),
                nb_obs_maille=int(row.get('nbobs_mail', 0) or 0),
                r2_adj=parse_french_float(row.get('R2_adj', '')),
            )
            db.add(loyer)
            count += 1
            
            if count % 5000 == 0:
                db.commit()
                print(f"  {count} records...")
    
    db.commit()
    print(f"  Imported {count} records for {type_bien}")
    return count


def main():
    # Create tables
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    
    try:
        # Clear existing data
        deleted = db.query(LoyerCommune).delete()
        db.commit()
        print(f"Deleted {deleted} existing records")
        
        data_dir = Path(__file__).parent.parent / 'data'
        
        # Import each file with its type
        files = [
            ('pred-app-mef-dhup.csv', 'appartement'),
            ('pred-app12-mef-dhup.csv', 'app_t1t2'),
            ('pred-app3-mef-dhup.csv', 'app_t3plus'),
            ('pred-mai-mef-dhup.csv', 'maison'),
        ]
        
        total = 0
        for filename, type_bien in files:
            filepath = data_dir / filename
            if filepath.exists():
                total += import_loyers_file(db, filepath, type_bien)
            else:
                print(f"Warning: {filename} not found")
        
        print(f"\nTotal: {total} loyer records imported")
        
        # Show sample
        sample = db.query(LoyerCommune).filter(LoyerCommune.insee_com == '75056').all()
        if sample:
            print("\nSample for Paris (75056):")
            for s in sample:
                print(f"  {s.type_bien}: {s.loyer_m2:.2f} €/m²")
        
    finally:
        db.close()


if __name__ == '__main__':
    main()
