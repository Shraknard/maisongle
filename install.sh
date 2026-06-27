#!/bin/bash

# Maisongle - Script d'installation
# Usage: ./install.sh

set -e  # Exit on error

echo "=========================================="
echo "  Maisongle - Installation"
echo "=========================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running from project root
if [ ! -f "requirements.txt" ]; then
    echo -e "${RED}Erreur: Exécutez ce script depuis la racine du projet${NC}"
    exit 1
fi

# Check Python version
echo -e "\n${YELLOW}1. Vérification de Python...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 n'est pas installé${NC}"
    exit 1
fi
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "${GREEN}Python $PYTHON_VERSION détecté${NC}"

# Create virtual environment if not exists
echo -e "\n${YELLOW}2. Création de l'environnement virtuel...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}Environnement virtuel créé${NC}"
else
    echo -e "${GREEN}Environnement virtuel existant${NC}"
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo -e "\n${YELLOW}3. Installation des dépendances Python...${NC}"
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}Dépendances installées${NC}"

# Check .env file
echo -e "\n${YELLOW}4. Vérification de la configuration...${NC}"
if [ ! -f ".env" ]; then
    cat > .env << EOF
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/maisongle
GEORISQUES_API_BASE_URL=https://georisques.gouv.fr/api/v1
EOF
    echo -e "${GREEN}Fichier .env créé${NC}"
else
    echo -e "${GREEN}Fichier .env existant${NC}"
fi

# Check PostgreSQL
echo -e "\n${YELLOW}5. Vérification de PostgreSQL...${NC}"
if command -v psql &> /dev/null; then
    echo -e "${GREEN}PostgreSQL détecté${NC}"
else
    echo -e "${YELLOW}PostgreSQL non détecté - Assurez-vous qu'il est installé${NC}"
fi

# Check data files
echo -e "\n${YELLOW}6. Vérification des fichiers de données...${NC}"
DATA_DIR="data"
MISSING_FILES=0

if [ ! -f "$DATA_DIR/zonage_abc.xlsx" ]; then
    echo -e "${YELLOW}Manquant: zonage_abc.xlsx${NC}"
    MISSING_FILES=1
fi

if [ ! -f "$DATA_DIR/communesdvf2024.csv" ]; then
    echo -e "${YELLOW}Manquant: communesdvf2024.csv (données DVF)${NC}"
    echo "   Télécharger depuis: https://www.data.gouv.fr/datasets/indicateurs-immobiliers-par-commune-et-par-annee-prix-et-volumes-sur-la-periode-2014-2024"
    MISSING_FILES=1
fi

if [ ! -f "$DATA_DIR/pred-app-mef-dhup.csv" ]; then
    echo -e "${YELLOW}Manquant: pred-app-mef-dhup.csv (données loyers)${NC}"
    echo "   Télécharger depuis: https://www.data.gouv.fr/datasets/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2024/"
    MISSING_FILES=1
fi

if [ $MISSING_FILES -eq 0 ]; then
    echo -e "${GREEN}Tous les fichiers de données sont présents${NC}"
fi

# Import data
echo -e "\n${YELLOW}7. Import des données...${NC}"
read -p "Voulez-vous importer les données maintenant? (o/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Oo]$ ]]; then
    echo -e "\n${YELLOW}Import du zonage ABC...${NC}"
    if [ -f "$DATA_DIR/zonage_abc.xlsx" ]; then
        python scripts/import_zonage.py
        echo -e "${GREEN}Zonage ABC importé${NC}"
    else
        echo -e "${RED}Fichier zonage_abc.xlsx manquant${NC}"
    fi

    echo -e "\n${YELLOW}Import des données DVF...${NC}"
    if [ -f "$DATA_DIR/communesdvf2024.csv" ]; then
        python scripts/import_dvf.py
        echo -e "${GREEN}Données DVF importées${NC}"
    else
        echo -e "${RED}Fichier communesdvf2024.csv manquant${NC}"
    fi

    echo -e "\n${YELLOW}Import des données de loyers...${NC}"
    if [ -f "$DATA_DIR/pred-app-mef-dhup.csv" ]; then
        python scripts/import_loyers.py
        echo -e "${GREEN}Données de loyers importées${NC}"
    else
        echo -e "${RED}Fichiers de loyers manquants${NC}"
    fi
fi

echo -e "\n=========================================="
echo -e "${GREEN}Installation terminée !${NC}"
echo "=========================================="
echo -e "\nPour lancer l'application:"
echo -e "  ${YELLOW}source venv/bin/activate${NC}"
echo -e "  ${YELLOW}uvicorn app.main:app --reload --host 0.0.0.0 --port 8000${NC}"
echo -e "\nL'application sera accessible sur http://localhost:8000"
