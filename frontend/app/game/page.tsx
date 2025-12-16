"use client";

import { useState } from "react";

type Player = {
  id: number;
  name: string;
  team: string;
};

type CasePlayer = { id: number; name: string; team: string };

type GameCase = {
  case: number;
  tier: number;
  player: CasePlayer;
  score: number;
};

type CasePoolResponse = {
  season: string;
  seed: number;
  cases: GameCase[];
};

export default function GamePage() {
  const [query, setQuery] = useState("");
  const [players, setPlayers] = useState<Player[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [casePool, setCasePool] = useState<CasePoolResponse | null>(null);
  const [caseLoading, setCaseLoading] = useState(false);
  const [caseError, setCaseError] = useState<string | null>(null);

  async function generateCasePool(seed: number) {
    setCaseLoading(true);
    setCaseError(null);

    try {
      const res = await fetch(`http://127.0.0.1:8000/game/cases?seed=${seed}`);
      if (!res.ok) throw new Error(`Backend error: ${res.status}`);
      const data: CasePoolResponse = await res.json();
      setCasePool(data);
    } catch (e) {
      if (e instanceof Error) {
        setCaseError(e.message);
      } else {
        setCaseError("Failed to load case pool");
      }
      setCasePool(null);
    } finally {
      setCaseLoading(false);
    }
  }

  async function searchPlayers() {
    if (!query) return;

    setLoading(true);
    setError(null);

    try {
      const response = await fetch(
        `http://127.0.0.1:8000/players/search?q=${encodeURIComponent(query)}`
      );

      if (!response.ok) throw new Error(`Request failed: ${response.status}`);

      const data = await response.json();
      setPlayers(data.players ?? []);
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message);
      } else {
        setError("Something went wrong. Please try again.");
      }
      setPlayers([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen p-8">
      <div className="mx-auto max-w-4xl space-y-6">
        <h1 className="text-3xl font-bold">Deal or No Deal</h1>

        <p className="text-sm text-gray-400">
          NBA version: generate a 16-case pool (one player per tier) from
          2024-25 production, then play Deal or No Deal.
        </p>

        <div className="rounded-lg border border-gray-800 p-4 space-y-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => generateCasePool(7)}
              className="rounded-md bg-white px-4 py-2 text-black font-medium"
              disabled={caseLoading}
            >
              {caseLoading ? "Generating..." : "Generate 16 Cases"}
            </button>

            {casePool && (
              <div className="text-sm text-gray-400">
                Season: <span className="text-gray-200">{casePool.season}</span>{" "}
                | Seed: <span className="text-gray-200">{casePool.seed}</span>
              </div>
            )}
          </div>

          {caseError && <div className="text-sm text-red-400">{caseError}</div>}

          {casePool && (
            <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {casePool.cases
                .slice()
                .sort((a, b) => a.case - b.case)
                .map((c) => (
                  <div
                    key={c.case}
                    className="rounded-lg border border-gray-800 p-3"
                  >
                    <div className="text-xs text-gray-400">Case {c.case}</div>
                    <div className="font-semibold text-gray-100">
                      {c.player.name}
                    </div>
                    <div className="text-sm text-gray-300">{c.player.team}</div>
                    <div className="text-xs text-gray-400">
                      Tier {c.tier} | Score {c.score}
                    </div>
                  </div>
                ))}
            </div>
          )}
        </div>

        <div className="rounded-lg border border-gray-800 p-4 space-y-3">
          <label className="block text-sm font-medium">
            Player search
            <input
              className="mt-2 w-full rounded-md border border-gray-800 bg-black p-2"
              placeholder="Type a player name..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>

          <button
            className="rounded-md bg-white px-4 py-2 text-black font-medium"
            type="button"
            onClick={searchPlayers}
          >
            Search
          </button>

          <div className="rounded-md border border-gray-800 p-3 text-sm">
            {loading && <div className="text-gray-400">Searching…</div>}

            {error && <div className="text-red-400">{error}</div>}

            {!loading && !error && players.length === 0 && (
              <div className="text-gray-400">No results yet.</div>
            )}

            {!loading &&
              !error &&
              players.map((player) => (
                <div key={player.id}>
                  {player.name} — {player.team}
                </div>
              ))}
          </div>
        </div>
      </div>
    </main>
  );
}
