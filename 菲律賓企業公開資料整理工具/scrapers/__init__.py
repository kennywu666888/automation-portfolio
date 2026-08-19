from .businesslist import BusinessListScraper
from .filbuild import FilbuildScraper

SCRAPERS = {
    "BusinessList Philippines": BusinessListScraper,
    "Filbuild": FilbuildScraper,
}
