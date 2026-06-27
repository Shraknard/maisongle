from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from contextlib import asynccontextmanager
import os

from app.database import engine, Base
from app.routers import search, favorites, saved_searches, listings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create database tables on startup."""
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Maisongle",
    description="Application de recherche immobilière",
    version="1.0.0",
    lifespan=lifespan
)

# Static files
static_path = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_path):
    app.mount("/static", StaticFiles(directory=static_path), name="static")

# Templates
templates_path = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=templates_path)

# Include routers
app.include_router(search.router, prefix="/api/search", tags=["search"])
app.include_router(favorites.router, prefix="/api/favorites", tags=["favorites"])
app.include_router(saved_searches.router, prefix="/api/saved-searches", tags=["saved-searches"])
app.include_router(listings.router, prefix="/api/listings", tags=["listings"])


@app.get("/")
async def home(request: Request):
    """Home page with search form."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/favorites")
async def favorites_page(request: Request):
    """Favorites page."""
    return templates.TemplateResponse("favorites.html", {"request": request})


@app.get("/property/{property_uuid}")
async def property_page(request: Request, property_uuid: str):
    """Property detail page."""
    return templates.TemplateResponse(
        "property.html", 
        {"request": request, "property_uuid": property_uuid}
    )


@app.get("/calculator")
async def calculator_page(request: Request, price: int = None):
    """Loan calculator page."""
    return templates.TemplateResponse(
        "calculator.html", 
        {"request": request, "price": price}
    )


@app.get("/documentation")
async def documentation_page(request: Request):
    """Documentation page."""
    return templates.TemplateResponse("documentation.html", {"request": request})
