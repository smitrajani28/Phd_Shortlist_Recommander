"""
Application-wide constants. Values here should NOT be configurable at runtime;
use config.py / Settings for anything that varies per environment.
"""

# Pipeline limits
MIN_SHORTLIST_SIZE = 50
MAX_SHORTLIST_SIZE = 200

# Scoring weight keys — must match RecommendationScorer.DEFAULT_WEIGHTS
SCORE_KEY_RESEARCH_ALIGNMENT = "research_alignment"
SCORE_KEY_PUBLICATION_RECENCY = "publication_recency"
SCORE_KEY_FUNDING_ACTIVITY = "funding_activity"
SCORE_KEY_COUNTRY_PREFERENCE = "country_preference"
SCORE_KEY_DOMAIN_MATCH = "domain_match"

# API base URLs (canonical; do not override in env)
OPENALEX_BASE_URL = "https://api.openalex.org"
SEMANTIC_SCHOLAR_BASE_URL = "https://api.semanticscholar.org/graph/v1"

# Output
OUTPUT_FILENAME_TEMPLATE = "{student_name}_shortlist_{timestamp}.json"
TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S"

# HTTP
DEFAULT_REQUEST_TIMEOUT = 10  # seconds
DEFAULT_CRAWL_DELAY = 1.0     # seconds between scraper requests
