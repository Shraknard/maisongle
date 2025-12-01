from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.models.saved_search import SavedSearch
from app.schemas.saved_search import SavedSearchCreate, SavedSearchResponse

router = APIRouter()


@router.get("/", response_model=List[SavedSearchResponse])
def get_saved_searches(db: Session = Depends(get_db)):
    """Get all saved searches."""
    return db.query(SavedSearch).order_by(SavedSearch.created_at.desc()).all()


@router.post("/", response_model=SavedSearchResponse)
def create_saved_search(search: SavedSearchCreate, db: Session = Depends(get_db)):
    """Create a new saved search."""
    db_search = SavedSearch(
        name=search.name,
        department=search.department,
        city_id=search.city_id,
        city_name=search.city_name,
        city_insee=search.city_insee,
        zipcode=search.zipcode,
        selected_cities=search.selected_cities,
        property_type=search.property_type,
        transaction_type=search.transaction_type,
        budget_min=search.budget_min,
        budget_max=search.budget_max,
        surface_min=search.surface_min,
        surface_max=search.surface_max,
        room_min=search.room_min,
        room_max=search.room_max,
        bedroom_min=search.bedroom_min,
        bedroom_max=search.bedroom_max,
    )
    db.add(db_search)
    db.commit()
    db.refresh(db_search)
    return db_search


@router.get("/{search_id}", response_model=SavedSearchResponse)
def get_saved_search(search_id: int, db: Session = Depends(get_db)):
    """Get a saved search by ID."""
    search = db.query(SavedSearch).filter(SavedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    return search


@router.delete("/{search_id}")
def delete_saved_search(search_id: int, db: Session = Depends(get_db)):
    """Delete a saved search."""
    search = db.query(SavedSearch).filter(SavedSearch.id == search_id).first()
    if not search:
        raise HTTPException(status_code=404, detail="Saved search not found")
    db.delete(search)
    db.commit()
    return {"message": "Saved search deleted"}
