import random
from typing import Dict, Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from cachetools import TTLCache
from nba_api.stats.static import players as nba_players
from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import (
    commonplayerinfo,
    commonteamroster,
    leagueleaders,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------
# Season
# -----------------------
SEASON_2024_25 = "2024-25"

# -----------------------
# Health
# -----------------------
@app.get("/health")
def health():
    return {"status": "ok"}


# -----------------------
# Player search (static list + team lookup)
# -----------------------
TEAM_CACHE: Dict[int, str] = {}

def get_team_name(player_id: int) -> str:
    if player_id in TEAM_CACHE:
        return TEAM_CACHE[player_id]

    try:
        info = commonplayerinfo.CommonPlayerInfo(player_id=player_id)
        df = info.get_data_frames()[0]
        team = df.loc[0, "TEAM_NAME"]
        team_name = str(team) if team else "Unknown"
    except Exception:
        team_name = "Unknown"

    TEAM_CACHE[player_id] = team_name
    return team_name


@app.get("/players/search")
def search_players(q: str = Query(..., min_length=1), limit: int = 10):
    query = q.lower().strip()
    all_players = nba_players.get_players()

    matches = [p for p in all_players if query in p["full_name"].lower()]
    matches.sort(key=lambda p: (not p.get("is_active", False), p["full_name"]))
    matches = matches[: min(max(limit, 1), 25)]

    results = []
    for p in matches:
        pid = int(p["id"])
        results.append(
            {
                "id": pid,
                "name": p["full_name"],
                "team": get_team_name(pid),
            }
        )

    return {"query": q, "count": len(results), "players": results}


# -----------------------
# Positions (from CommonTeamRoster)
# -----------------------
# player_id -> raw roster position ("G", "F", "C", "G-F", "F-C", ...)
POSITION_MAP: Dict[int, str] = {}

def normalize_position(pos: str) -> str:
    """
    Convert NBA roster position strings into one of: PG, SG, SF, PF, C.

    Roster positions are coarse. This mapping is deterministic and explainable:
    - G -> PG
    - G-F -> SG
    - F -> SF
    - F-C -> PF
    - C -> C
    """
    p = (pos or "").upper().strip()

    if p == "C":
        return "C"
    if p == "F-C":
        return "PF"
    if p == "G-F":
        return "SG"
    if p == "F":
        return "SF"
    if p == "G":
        return "PG"

    # Some rosters may have odd strings; default to wing
    return "SF"


def build_position_map(season: str):
    """
    Build a position map by calling CommonTeamRoster once per NBA team.
    This avoids slow per-player calls and keeps /game endpoints fast.
    """
    global POSITION_MAP
    POSITION_MAP = {}

    for t in nba_teams.get_teams():
        team_id = t["id"]
        try:
            roster = commonteamroster.CommonTeamRoster(team_id=team_id, season=season)
            df = roster.get_data_frames()[0]
        except Exception:
            continue

        for _, row in df.iterrows():
            try:
                pid = int(row.get("PLAYER_ID"))
                pos = str(row.get("POSITION", "")).strip()
                if pid and pos:
                    POSITION_MAP[pid] = pos
            except Exception:
                pass


# Build positions once on import/startup
build_position_map(SEASON_2024_25)


# -----------------------
# League leaders (PerGame) used for tiering
# -----------------------
LEADERS_CACHE = TTLCache(maxsize=2, ttl=60 * 60)  # 1 hour cache

def get_league_leaders_pergame_df():
    if SEASON_2024_25 in LEADERS_CACHE:
        return LEADERS_CACHE[SEASON_2024_25]

    ll = leagueleaders.LeagueLeaders(
        season=SEASON_2024_25,
        per_mode48="PerGame",
        scope="S",
        season_type_all_star="Regular Season",
        stat_category_abbreviation="PTS",
    )
    df = ll.get_data_frames()[0]
    LEADERS_CACHE[SEASON_2024_25] = df
    return df


def production_score_pergame(row) -> float:
    pts = float(row.get("PTS", 0))
    reb = float(row.get("REB", 0))
    ast = float(row.get("AST", 0))
    stl = float(row.get("STL", 0))
    blk = float(row.get("BLK", 0))
    tov = float(row.get("TOV", 0))
    return pts + 1.2 * reb + 1.5 * ast + 3.0 * stl + 3.0 * blk - 2.0 * tov


def build_case_pool_from_candidates(candidates, seed: int):
    candidates.sort(key=lambda x: x["score"], reverse=True)

    if len(candidates) < 16:
        raise HTTPException(status_code=400, detail=f"Not enough candidates. Found {len(candidates)}.")

    # 16 tiers by rank
    tiers = [[] for _ in range(16)]
    n = len(candidates)
    for i, player in enumerate(candidates):
        tier_idx = min(15, int(i * 16 / n))
        tiers[tier_idx].append(player)

    rng = random.Random(seed)

    # pick one from each tier
    chosen = []
    for tier_index, tier_players in enumerate(tiers, start=1):
        if not tier_players:
            raise HTTPException(status_code=500, detail=f"Tier {tier_index} ended up empty.")
        pick = rng.choice(tier_players)
        chosen.append({"tier": tier_index, "player": pick})

    # shuffle into case numbers
    rng.shuffle(chosen)

    cases = []
    for case_number, item in enumerate(chosen, start=1):
        p = item["player"]
        cases.append(
            {
                "case": case_number,
                "tier": item["tier"],
                "player": {"id": p["id"], "name": p["name"], "team": p["team"]},
                "score": round(p["score"], 2),
            }
        )
    return cases


# -----------------------
# Game endpoints
# -----------------------
@app.get("/game/cases")
def generate_cases(seed: int = 1):
    """
    Original behavior: 16 tiers (PerGame score), 16 random players (one per tier),
    shuffled into cases 1..16.
    """
    df = get_league_leaders_pergame_df()

    candidates = []
    for _, row in df.iterrows():
        try:
            pid = int(row["PLAYER_ID"])
            name = str(row["PLAYER"])
        except Exception:
            continue

        team = str(row.get("TEAM", row.get("TEAM_ABBREVIATION", "Unknown")))
        score = production_score_pergame(row)
        candidates.append({"id": pid, "name": name, "team": team, "score": score})

    cases = build_case_pool_from_candidates(candidates, seed)
    return {"season": SEASON_2024_25, "seed": seed, "cases": cases}


@app.get("/game/cases_by_slot")
def generate_cases_by_slot(seed: int = 1, slot: str = "PG"):
    """
    Position-specific pool:
    - Slot is derived from CommonTeamRoster POSITION (official roster listing)
    - Tiering uses PerGame score (unchanged)
    """
    slot = slot.upper().strip()
    if slot not in {"PG", "SG", "SF", "PF", "C"}:
        raise HTTPException(status_code=400, detail="slot must be one of PG, SG, SF, PF, C")

    df = get_league_leaders_pergame_df()

    candidates = []
    for _, row in df.iterrows():
        try:
            pid = int(row["PLAYER_ID"])
            name = str(row["PLAYER"])
        except Exception:
            continue

        raw_pos: Optional[str] = POSITION_MAP.get(pid)
        if not raw_pos:
            continue

        player_slot = normalize_position(raw_pos)
        if player_slot != slot:
            continue

        team = str(row.get("TEAM", row.get("TEAM_ABBREVIATION", "Unknown")))
        score = production_score_pergame(row)
        candidates.append({"id": pid, "name": name, "team": team, "score": score})

    cases = build_case_pool_from_candidates(candidates, seed)
    return {"season": SEASON_2024_25, "seed": seed, "slot": slot, "cases": cases}
