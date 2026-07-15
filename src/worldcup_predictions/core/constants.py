"""Shared constants for source ids, env vars, endpoints, and model policies."""

from __future__ import annotations

PROJECT_USER_AGENT = "worldcup-predictions/0.1"

ENV_ODDS_API_KEY = "ODDS_API_KEY"
ENV_NEWS_API_KEY = "NEWS_API_KEY"
ENV_FOOTBALL_DATA_API_KEY = "FOOTBALL_DATA_API_KEY"
ENV_KAGGLE_API_TOKEN = "KAGGLE_API_TOKEN"
ENV_GTM_CONTAINER_ID = "GTM_CONTAINER_ID"
ENV_BASE_URL = "BASE_URL"

SOURCE_THE_ODDS_API = "the_odds_api"
SOURCE_MARKET_ODDS = "market_odds"
SOURCE_MARKET_TREND = "market_trend"
SOURCE_OPEN_METEO = "open_meteo"
SOURCE_NEWS_API = "news_api"
SOURCE_FOOTBALL_DATA = "football_data_org"
SOURCE_OPENFOOTBALL = "openfootball_worldcup"
SOURCE_FIFA_MATCH_CENTRE = "fifa_match_centre"
SOURCE_FOTMOB_PUBLIC = "fotmob_public"
SOURCE_SOFASCORE_PUBLIC = "sofascore_public"
SOURCE_TWENTY_MIN_PUBLIC = "twenty_min_public"
SOURCE_PLAYER_IMPACT = "player_impact"
SOURCE_ML_OUTCOME = "ml_outcome"
SOURCE_PUBLIC_ANALYSIS = "public_analysis"
SOURCE_LINEUP_AVAILABILITY = "lineup_availability"
SOURCE_LIVE_CALIBRATION = "live_calibration"
SOURCE_SRF_EXPERTS = "srf_experts"
SOURCE_SRF_PUBLIC = "srf_public"
SOURCE_DYNAMIC_PUBLIC = "dynamic_public"
SOURCE_KAGGLE = "kaggle"
SOURCE_WIKIPEDIA = "wikipedia"
SOURCE_TRANSFERMARKT = "transfermarkt"
SOURCE_MARTJ42_RESULTS = "martj42_international_results"
SOURCE_AUTOMATIC_MATCH_NOTES = "automatic_match_notes"
SOURCE_MODEL_CALIBRATION = "model_calibration"

CONFIRMED_RESULT_MIN_SOURCES = 3
CONFIRMED_RESULT_HIGH_AUTHORITY_MIN_SOURCES = 2
# espn_scoreboard was removed 2026-07-10: 6,104 failed fetches and zero
# successes all tournament (blocked with 403 from day one).
HIGH_AUTHORITY_RESULT_SOURCES = (
    SOURCE_SRF_PUBLIC,
    SOURCE_FIFA_MATCH_CENTRE,
    SOURCE_FOOTBALL_DATA,
)
# Result observations whose kickoff differs from the canonical fixture by at
# most this window are treated as the same match for consensus, so sources
# that disagree on kickoff time cannot confirm a phantom duplicate fixture.
CONFIRMED_RESULT_KICKOFF_WINDOW_HOURS = 3

ENDPOINT_THE_ODDS_API_WORLD_CUP_ODDS = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/odds"
ENDPOINT_THE_ODDS_API_WORLD_CUP_EVENTS = "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/events"
ENDPOINT_THE_ODDS_API_SPORTS = "https://api.the-odds-api.com/v4/sports"
ENDPOINT_OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
ENDPOINT_NEWS_API_EVERYTHING = "https://newsapi.org/v2/everything"
ENDPOINT_FOOTBALL_DATA_COMPETITION = "https://api.football-data.org/v4/competitions/WC"
ENDPOINT_FOOTBALL_DATA_MATCH = "https://api.football-data.org/v4/matches"
ENDPOINT_OPENFOOTBALL_WORLDCUP_BASE = "https://raw.githubusercontent.com/openfootball/worldcup/master"
ENDPOINT_FIFA_CALENDAR_MATCHES = "https://api.fifa.com/api/v3/calendar/matches"
FIFA_WORLD_CUP_COMPETITION_ID = "17"
FIFA_WORLD_CUP_2026_SEASON_ID = "285023"
ENDPOINT_FIFA_WORLDCUP_2026_SCORES = "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/scores-fixtures"
ENDPOINT_FOTMOB_MATCH_SITEMAP = "https://www.fotmob.com/sitemap/en/matches.xml"
ENDPOINT_SOFASCORE_FOOTBALL = "https://www.sofascore.com/football"
ENDPOINT_TWENTY_MIN_TIPPSPIEL_DETAILS = "https://tippspiel.20min.ch/details"
ENDPOINT_MARTJ42_RESULTS = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
ENDPOINT_MARTJ42_SHOOTOUTS = "https://raw.githubusercontent.com/martj42/international_results/master/shootouts.csv"
ENDPOINT_KAGGLE_DATASETS_LIST = "https://www.kaggle.com/api/v1/datasets/list"
ENDPOINT_KAGGLE_DATASETS_DOWNLOAD = "https://www.kaggle.com/api/v1/datasets/download"
ENDPOINT_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
ENDPOINT_TRANSFERMARKT_SEARCH = "https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche"
SRF_BASE_URL = "https://wmtippspiel.srf.ch"

OPENFOOTBALL_WORLD_CUP_FILES = {
    "cup.txt": "2026--usa/cup.txt",
    "cup_finals.txt": "2026--usa/cup_finals.txt",
}

THE_ODDS_API_WORLD_CUP_SPORT = "soccer_fifa_world_cup"
THE_ODDS_API_MARKETS = "h2h,totals,spreads"
THE_ODDS_API_EVENT_MARKETS = "draw_no_bet,btts,team_totals,alternate_totals,alternate_spreads"
THE_ODDS_API_EVENT_MARKET_WINDOW_HOURS = 36
THE_ODDS_API_EVENT_MARKET_FIXTURE_LIMIT = 6
THE_ODDS_API_REGIONS = "eu,us,uk"

KAGGLE_DATASET_SEARCHES = {
    "transfermarkt_football_market_value": "transfermarkt football market value",
    "football_player_values": "football player market value",
    "world_cup_squads": "world cup squads football",
    "fifa_world_cup_2026": "fifa world cup 2026",
}

KAGGLE_SELECTED_DATASETS = {
    "davidcariboo/player-scores": "Transfermarkt-style player valuation dataset used for optional squad-value enrichment.",
}

NEWS_API_DEFAULT_PAGE_SIZE = 25

# Restrict NewsAPI /everything queries to reliable publishers at the API level. This
# raises precision and saves quota (junk results never count against the daily budget)
# while the per-article reliability scoring still runs on what is returned. A larger
# pageSize costs no extra requests on the free tier.
NEWS_API_RELIABLE_DOMAINS = (
    "reuters.com",
    "apnews.com",
    "bbc.co.uk",
    "bbc.com",
    "theguardian.com",
    "espn.com",
    "skysports.com",
    "cbssports.com",
    "nbcsports.com",
    "foxsports.com",
    "fourfourtwo.com",
    "theathletic.com",
    "goal.com",
)

SRF_EXPERT_URLS = {
    "kathrin_lehmann": "https://wmtippspiel.srf.ch/experts/kathrin-lehmann",
    "bruno_berner": "https://wmtippspiel.srf.ch/experts/bruno-berner",
    "lutz_pfannenstiel": "https://wmtippspiel.srf.ch/experts/lutz-pfannenstiel",
}

EXPECTED_DEBUG_SIGNAL_SOURCES = (
    SOURCE_MARKET_ODDS,
    SOURCE_OPEN_METEO,
    SOURCE_PUBLIC_ANALYSIS,
    SOURCE_LINEUP_AVAILABILITY,
    SOURCE_AUTOMATIC_MATCH_NOTES,
    SOURCE_PLAYER_IMPACT,
    SOURCE_ML_OUTCOME,
    SOURCE_LIVE_CALIBRATION,
    SOURCE_SRF_EXPERTS,
)

SIGNAL_TOTAL_GOALS_FACTOR_MIN = 0.82
SIGNAL_TOTAL_GOALS_FACTOR_MAX = 1.18
SIGNAL_TEAM_EXPECTED_GOALS_FACTOR_MIN = 0.80
SIGNAL_TEAM_EXPECTED_GOALS_FACTOR_MAX = 1.20
SIGNAL_MARKET_TOTAL_GOALS_MIN = 0.60
SIGNAL_MARKET_TOTAL_GOALS_MAX = 5.20
SIGNAL_MARKET_GOAL_DIFF_MIN = -4.0
SIGNAL_MARKET_GOAL_DIFF_MAX = 4.0
SIGNAL_GROUP_DRAW_PRESSURE_MIN = -0.08
SIGNAL_GROUP_DRAW_PRESSURE_MAX = 0.08
SIGNAL_LIVE_DRAW_ADJUSTMENT_MIN = -0.10
SIGNAL_LIVE_DRAW_ADJUSTMENT_MAX = 0.12
SIGNAL_LIVE_SCORE_TAIL_FACTOR_MIN = -0.06
SIGNAL_LIVE_SCORE_TAIL_FACTOR_MAX = 0.08
SIGNAL_LIVE_FAVORITE_OUTCOME_FACTOR_MIN = 0.92
SIGNAL_LIVE_FAVORITE_OUTCOME_FACTOR_MAX = 1.08

# Market is the strongest public predictor; weighted high but below a full overwrite so
# the model's score shape and other signals still contribute. Validated forward on the
# live tournament (no historical odds exist to backtest these).
SIGNAL_WEIGHT_MARKET_HDA = 0.95
SIGNAL_WEIGHT_MARKET_TOTAL_GOALS = 0.65
SIGNAL_WEIGHT_MARKET_GOAL_DIFF = 0.55
SIGNAL_WEIGHT_EXPERT_HDA = 0.20
SIGNAL_WEIGHT_ML_HDA = 0.18
SIGNAL_WEIGHT_LIVE_DRAW = 0.55
SIGNAL_WEIGHT_LIVE_SCORE_TAIL = 0.45
SIGNAL_WEIGHT_LIVE_FAVORITE = 0.35
# Base cap only: evaluation.signal_skill scales every H/D/A source by its
# outcome-scored skill each run. With the experts' current 100-match record
# (Brier edge -0.154 vs the published forecast) the learned multiplier puts
# their effective weight near 0.05; it recovers automatically if they improve.
SIGNAL_WEIGHT_SRF_EXPERT = 0.20
SIGNAL_WEIGHT_WEATHER = 1.0
SIGNAL_WEIGHT_AUTOMATIC_MATCH_NOTE = 0.25

# Source reliability is applied as a continuous weight, not a hard gate: pregame article
# signals already average each source's contribution by its reliability score, so the
# floor only drops clearly-untrusted spam. Unknown publishers start at a neutral score
# and contribute at reduced weight rather than being excluded outright.
RELIABILITY_SIGNAL_FLOOR = 0.40
DYNAMIC_SOURCE_INITIAL_REPUTATION = 0.50
DYNAMIC_SOURCE_REPUTATION_PRIOR_WEIGHT = 4.0
DYNAMIC_SOURCE_RESULT_MIN_DOMAINS = 3
DYNAMIC_SOURCE_RESULT_MIN_WEIGHTED_SUPPORT = 1.50
DYNAMIC_SOURCE_MARKET_MIN_CONFIDENCE = 0.60

SIGNAL_CONFIDENCE_WEATHER = 0.80
SIGNAL_CONFIDENCE_SRF_EXPERT_BASE = 0.45
SIGNAL_CONFIDENCE_SRF_EXPERT_PER_PICK = 0.12
SIGNAL_CONFIDENCE_SRF_EXPERT_MAX = 0.85

WEATHER_GOAL_FACTOR_MIN = 0.82
WEATHER_GOAL_FACTOR_MAX = 1.08

PUBLISHED_PREDICTION_LOCK_BUFFER_MINUTES = 5

VENUE_COORDINATES = {
    "atlanta stadion": (33.7554, -84.4008),
    "atlanta-stadion": (33.7554, -84.4008),
    "bc place vancouver": (49.2768, -123.1119),
    "boston stadion": (42.0909, -71.2643),
    "boston-stadion": (42.0909, -71.2643),
    "dallas stadion": (32.7473, -97.0945),
    "dallas-stadion": (32.7473, -97.0945),
    "guadalajara stadion": (20.6817, -103.4626),
    "guadalajara-stadion": (20.6817, -103.4626),
    "houston stadion": (29.6847, -95.4107),
    "houston-stadion": (29.6847, -95.4107),
    "kansas city stadion": (39.0489, -94.4839),
    "kansas-city-stadion": (39.0489, -94.4839),
    "los angeles stadion": (33.9535, -118.3392),
    "los-angeles-stadion": (33.9535, -118.3392),
    "mexico city stadion": (19.3029, -99.1505),
    "mexico-city-stadion": (19.3029, -99.1505),
    "miami stadion": (25.9580, -80.2389),
    "miami-stadion": (25.9580, -80.2389),
    "monterrey stadion": (25.6684, -100.2440),
    "monterrey-stadion": (25.6684, -100.2440),
    "new york new jersey stadion": (40.8135, -74.0745),
    "new-york-new-jersey-stadion": (40.8135, -74.0745),
    "philadelphia stadion": (39.9008, -75.1675),
    "philadelphia-stadion": (39.9008, -75.1675),
    "san francisco bay area stadion": (37.4030, -121.9700),
    "san-francisco-bay-area-stadion": (37.4030, -121.9700),
    "seattle stadion": (47.5952, -122.3316),
    "seattle-stadion": (47.5952, -122.3316),
    "toronto stadion": (43.6332, -79.4186),
    "toronto-stadion": (43.6332, -79.4186),
}
