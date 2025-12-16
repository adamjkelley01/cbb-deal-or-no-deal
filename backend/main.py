import random
from typing import Dict, Optional, List

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from cachetools import TTLCache
from nba_api.stats.static import players as nba_players
from nba_api.stats.static import teams as nba_teams
from nba_api.stats.endpoints import (
    commonplayerinfo,
    commonteamroster,
    leaguedashplayerstats,
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SEASON_2024_25 = "2024-25"

# -----------------------
# Basic health
# -----------------------
@app.get("/health")
def health():
    return {"status": "ok", "season": SEASON_2024_25}


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
        results.append({"id": pid, "name": p["full_name"], "team": get_team_name(pid)})

    return {"query": q, "count": len(results), "players": results}


# -----------------------
# Rosters and positions (source of truth for eligibility)
# -----------------------
# ROSTER_POSITION_MAP: player_id -> raw roster position ("G", "F", "C", "G-F", "F-C")
ROSTER_POSITION_MAP: Dict[int, str] = {}
# ROSTER_META: player_id -> {"name": ..., "team": ..., "raw_pos": ...}
ROSTER_META: Dict[int, Dict[str, str]] = {}

def normalize_position(pos: str) -> str:
    """
    Convert NBA roster position strings into one of: PG, SG, SF, PF, C.

    Note: Team rosters use coarse labels, not strict 1-5 positions.
    This mapping is deterministic and easy to adjust if needed.
    """
    p = (pos or "").upper().strip()

    if p == "C":
        return "C"
    if p == "F-C":
        return "C"   # treat F-C as center so C pool is large enough
    if p == "G-F":
        return "SG"
    if p == "F":
        return "SF"
    if p == "G":
        return "PG"

    return "SF"


def build_roster_maps(season: str) -> Dict[str, int]:
    """
    Calls CommonTeamRoster once per NBA team.
    Builds:
      - ROSTER_POSITION_MAP
      - ROSTER_META (name, team, raw_pos)

    This defines who is eligible to appear in the game.
    """
    global ROSTER_POSITION_MAP, ROSTER_META
    ROSTER_POSITION_MAP = {}
    ROSTER_META = {}

    teams = nba_teams.get_teams()
    for t in teams:
        team_id = t["id"]
        team_name = t.get("full_name", t.get("abbreviation", "Unknown"))

        try:
            roster = commonteamroster.CommonTeamRoster(team_id=team_id, season=season)
            df = roster.get_data_frames()[0]
        except Exception:
            continue

        for _, row in df.iterrows():
            try:
                pid = int(row.get("PLAYER_ID"))
                name = str(row.get("PLAYER", "")).strip()
                raw_pos = str(row.get("POSITION", "")).strip()
            except Exception:
                continue

            if not pid or not name:
                continue

            if raw_pos:
                ROSTER_POSITION_MAP[pid] = raw_pos
            else:
                ROSTER_POSITION_MAP[pid] = ""

            ROSTER_META[pid] = {
                "name": name,
                "team": str(team_name),
                "raw_pos": raw_pos,
            }

    return {"players": len(ROSTER_META), "positions": len(ROSTER_POSITION_MAP), "teams": len(teams)}


# -----------------------
# Season stats (source of truth for tiering)
# Exclude players with no stats by definition (not present in stats table or 0 GP / 0 MIN)
# -----------------------
STATS_CACHE = TTLCache(maxsize=2, ttl=60 * 60)  # 1 hour cache

def get_season_stats_df():
    """
    LeagueDashPlayerStats returns a league-wide table of player stats for the season.
    We use PerGame for tier score (same idea as your original approach).
    """
    if SEASON_2024_25 in STATS_CACHE:
        return STATS_CACHE[SEASON_2024_25]

    dash = leaguedashplayerstats.LeagueDashPlayerStats(
        season=SEASON_2024_25,
        season_type_all_star="Regular Season",
        per_mode_detailed="PerGame",
    )
    df = dash.get_data_frames()[0]
    STATS_CACHE[SEASON_2024_25] = df
    return df


def production_score_pergame(row) -> float:
    pts = float(row.get("PTS", 0))
    reb = float(row.get("REB", 0))
    ast = float(row.get("AST", 0))
    stl = float(row.get("STL", 0))
    blk = float(row.get("BLK", 0))
    tov = float(row.get("TOV", 0))
    return pts + 1.2 * reb + 1.5 * ast + 3.0 * stl + 3.0 * blk - 2.0 * tov


def build_case_pool_from_candidates(candidates: List[dict], seed: int):
    candidates.sort(key=lambda x: x["score"], reverse=True)

    if len(candidates) < 16:
        raise HTTPException(status_code=400, detail=f"Not enough candidates. Found {len(candidates)}.")

    tiers = [[] for _ in range(16)]
    n = len(candidates)
    for i, player in enumerate(candidates):
        tier_idx = min(15, int(i * 16 / n))
        tiers[tier_idx].append(player)

    rng = random.Random(seed)

    chosen = []
    for tier_index, tier_players in enumerate(tiers, start=1):
        if not tier_players:
            raise HTTPException(status_code=500, detail=f"Tier {tier_index} ended up empty.")
        pick = rng.choice(tier_players)
        chosen.append({"tier": tier_index, "player": pick})

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


def build_candidates(slot: Optional[str] = None) -> List[dict]:
    """
    Candidate pool rules:
      1) Must have season stats (LeagueDashPlayerStats row)
      2) Must be on a 2024-25 team roster (CommonTeamRoster)
      3) If slot provided, must match normalized roster position
      4) Exclude players with no stats: enforce GP > 0 and MIN > 0
    """
    df = get_season_stats_df()

    slot_norm: Optional[str] = None
    if slot is not None:
        s = slot.upper().strip()
        if s not in {"PG", "SG", "SF", "PF", "C"}:
            raise HTTPException(status_code=400, detail="slot must be one of PG, SG, SF, PF, C")
        slot_norm = s

    if len(ROSTER_META) == 0:
        raise HTTPException(
            status_code=503,
            detail="Roster map is empty. Call POST /admin/refresh_rosters then retry.",
        )

    candidates: List[dict] = []

    for _, row in df.iterrows():
        try:
            pid = int(row.get("PLAYER_ID"))
        except Exception:
            continue

        # must be on roster
        meta = ROSTER_META.get(pid)
        if not meta:
            continue

        # must have actually played (exclude no-stats and zero-minute rows)
        gp = float(row.get("GP", 0))
        min_pg = float(row.get("MIN", 0))
        if gp <= 0 or min_pg <= 0:
            continue

        # slot filter (based on roster position)
        if slot_norm is not None:
            raw_pos = ROSTER_POSITION_MAP.get(pid, "")
            player_slot = normalize_position(raw_pos)
            if player_slot != slot_norm:
                continue

        score = production_score_pergame(row)

        candidates.append(
            {
                "id": pid,
                "name": meta["name"],
                "team": meta["team"],
                "score": score,
            }
        )

    return candidates


# -----------------------
# Admin endpoints to refresh caches
# -----------------------
@app.post("/admin/refresh_rosters")
def refresh_rosters():
    info = build_roster_maps(SEASON_2024_25)
    return {"season": SEASON_2024_25, **info}

@app.get("/admin/roster_count")
def roster_count():
    return {"season": SEASON_2024_25, "players": len(ROSTER_META)}

@app.post("/admin/refresh_stats")
def refresh_stats():
    # forces stats fetch now
    df = get_season_stats_df()
    return {"season": SEASON_2024_25, "rows": int(df.shape[0])}


# -----------------------
# Startup: try to build rosters once, but never block server startup if it fails
# -----------------------
@app.on_event("startup")
def on_startup():
    try:
        info = build_roster_maps(SEASON_2024_25)
        print(f"[startup] roster loaded: {info}")
    except Exception as e:
        print(f"[startup] roster load failed: {e}")


# -----------------------
# Game endpoints
# -----------------------
@app.get("/game/cases")
def generate_cases(seed: int = 1):
    """
    16 tiers across ALL eligible roster players who have stats in 2024-25.
    """
    candidates = build_candidates(slot=None)
    cases = build_case_pool_from_candidates(candidates, seed)
    return {"season": SEASON_2024_25, "seed": seed, "cases": cases}


@app.get("/game/cases_by_slot")
def generate_cases_by_slot(seed: int = 1, slot: str = "PG"):
    """
    16 tiers within a position slot, using roster position for slot eligibility,
    and season per-game stats for tier score.
    Excludes players with no stats automatically.
    """
    candidates = build_candidates(slot=slot)
    cases = build_case_pool_from_candidates(candidates, seed)
    return {"season": SEASON_2024_25, "seed": seed, "slot": slot.upper().strip(), "cases": cases}
