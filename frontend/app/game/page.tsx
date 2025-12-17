"use client";

import { useEffect, useMemo, useState } from "react";

type Slot = "PG" | "SG" | "SF" | "PF" | "C";

type CasePlayer = {
  id: number;
  name: string;
  team: string;
};

type GameCase = {
  case: number;
  tier: number;
  player: CasePlayer;
  score: number;
};

type CasePoolResponse = {
  season: string;
  seed: number;
  slot: Slot;
  cases: GameCase[];
};

type BankerOfferResponse = {
  season: string;
  slot: Slot;
  target_tier: number;
  picked_tier: number;
  player: CasePlayer;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

// Opening schedule: 4 then 3 then 3 then 2 then 2, then final choice
const OPEN_SCHEDULE = [4, 3, 3, 2, 2] as const;

type Phase =
  | "idle"
  | "pick_reserved"
  | "open_round"
  | "banker_offer"
  | "final_choice"
  | "done";

export default function GamePage() {
  useEffect(() => {
    console.log("NEXT_PUBLIC_API_BASE =", process.env.NEXT_PUBLIC_API_BASE);
  }, []);

  const [slot, setSlot] = useState<Slot>("PG");
  const [seed, setSeed] = useState<number>(7);

  const [casePool, setCasePool] = useState<CasePoolResponse | null>(null);
  const [loadingCases, setLoadingCases] = useState(false);
  const [casesError, setCasesError] = useState<string | null>(null);

  const [openedCaseNumbers, setOpenedCaseNumbers] = useState<Set<number>>(
    new Set()
  );
  const [reservedCaseNumber, setReservedCaseNumber] = useState<number | null>(
    null
  );

  const [phase, setPhase] = useState<Phase>("idle");
  const [roundIndex, setRoundIndex] = useState<number>(0);
  const [remainingToOpen, setRemainingToOpen] = useState<number>(0);

  const [roundMessageOpen, setRoundMessageOpen] = useState(false);

  const [lastOpened, setLastOpened] = useState<GameCase | null>(null);

  const [startingFive, setStartingFive] = useState<
    Partial<Record<Slot, CasePlayer>>
  >({});

  const [bankerOffer, setBankerOffer] = useState<BankerOfferResponse | null>(
    null
  );
  const [bankerOfferError, setBankerOfferError] = useState<string | null>(null);
  const [offerRevealed, setOfferRevealed] = useState(false);
  const [offerLoading, setOfferLoading] = useState(false);

  // slots already completed
  const playedSlots = useMemo(() => {
    const keys = Object.keys(startingFive) as Slot[];
    return new Set<Slot>(keys.filter((k) => startingFive[k] != null));
  }, [startingFive]);

  const casesByCaseNumber = useMemo(() => {
    if (!casePool) return [];
    return casePool.cases.slice().sort((a, b) => a.case - b.case);
  }, [casePool]);

  const tierList = useMemo(() => {
    if (!casePool) return [];
    return casePool.cases.slice().sort((a, b) => a.tier - b.tier);
  }, [casePool]);

  const unopenedCases = useMemo(() => {
    if (!casePool) return [];
    return casePool.cases.filter((c) => !openedCaseNumbers.has(c.case));
  }, [casePool, openedCaseNumbers]);

  const remainingUnopenedCount = unopenedCases.length;

  const lastOtherUnopenedCase = useMemo(() => {
    if (!casePool) return null;
    const remaining = casePool.cases.filter(
      (c) => !openedCaseNumbers.has(c.case) && c.case !== reservedCaseNumber
    );
    if (remaining.length === 1) return remaining[0];
    return null;
  }, [casePool, openedCaseNumbers, reservedCaseNumber]);

  const initialGamePlayerIds = useMemo(() => {
    if (!casePool) return [];
    return casePool.cases.map((c) => c.player.id);
  }, [casePool]);

  function resetEverything() {
    setCasePool(null);
    setOpenedCaseNumbers(new Set());
    setReservedCaseNumber(null);
    setPhase("idle");
    setRoundIndex(0);
    setRemainingToOpen(0);
    setRoundMessageOpen(false);
    setLastOpened(null);
    setCasesError(null);

    setBankerOffer(null);
    setBankerOfferError(null);
    setOfferRevealed(false);
    setOfferLoading(false);

    setStartingFive({});
    setSlot("PG");
    setSeed(7);
  }

  function resetCurrentGameKeepRoster() {
    setCasePool(null);
    setOpenedCaseNumbers(new Set());
    setReservedCaseNumber(null);
    setPhase("idle");
    setRoundIndex(0);
    setRemainingToOpen(0);
    setRoundMessageOpen(false);
    setLastOpened(null);
    setCasesError(null);

    setBankerOffer(null);
    setBankerOfferError(null);
    setOfferRevealed(false);
    setOfferLoading(false);
  }

  async function loadCases() {
    setLoadingCases(true);
    setCasesError(null);

    setOpenedCaseNumbers(new Set());
    setReservedCaseNumber(null);
    setPhase("idle");
    setRoundIndex(0);
    setRemainingToOpen(0);
    setRoundMessageOpen(false);
    setLastOpened(null);

    setBankerOffer(null);
    setBankerOfferError(null);
    setOfferRevealed(false);
    setOfferLoading(false);

    try {
      const url = `${API_BASE}/game/cases_by_slot?seed=${encodeURIComponent(
        seed
      )}&slot=${encodeURIComponent(slot)}`;

      console.log("Fetching:", url);

      const res = await fetch(url);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "Request failed");
      }

      const data = (await res.json()) as CasePoolResponse;
      setCasePool(data);
      setPhase("pick_reserved");
    } catch {
      setCasePool(null);
      setPhase("idle");
      setCasesError(
        "Could not load cases. Backend may be sleeping, try again."
      );
    } finally {
      setLoadingCases(false);
    }
  }

  function startAfterReservedPick(caseNumber: number) {
    setReservedCaseNumber(caseNumber);

    const first = OPEN_SCHEDULE[0];
    setRoundIndex(0);
    setRemainingToOpen(first);
    setPhase("open_round");

    setRoundMessageOpen(true);
  }

  function openCase(caseNumber: number) {
    if (!casePool) return;
    if (reservedCaseNumber != null && caseNumber === reservedCaseNumber) return;
    if (openedCaseNumbers.has(caseNumber)) return;

    const found = casePool.cases.find((c) => c.case === caseNumber);
    if (!found) return;

    setOpenedCaseNumbers((prev) => {
      const next = new Set(prev);
      next.add(caseNumber);
      return next;
    });

    setLastOpened(found);
    setRemainingToOpen((prev) => Math.max(0, prev - 1));
  }

  // round end -> banker_offer (no setState during render)
  useEffect(() => {
    if (
      phase === "open_round" &&
      remainingToOpen === 0 &&
      reservedCaseNumber != null
    ) {
      setPhase("banker_offer");
      setBankerOffer(null);
      setBankerOfferError(null);
      setOfferRevealed(false);
      setOfferLoading(false);
    }
  }, [phase, remainingToOpen, reservedCaseNumber]);

  async function revealOffer() {
    if (!casePool) return;
    if (phase !== "banker_offer") return;
    if (offerLoading) return;
    if (offerRevealed && bankerOffer) return;

    setOfferLoading(true);
    setBankerOffer(null);
    setBankerOfferError(null);

    const remaining = casePool.cases.filter(
      (c) => !openedCaseNumbers.has(c.case)
    ); // includes reserved

    if (remaining.length === 0) {
      setBankerOfferError("No remaining cases to compute an offer.");
      setOfferLoading(false);
      return;
    }

    const avgTierRaw =
      remaining.reduce((sum, c) => sum + c.tier, 0) / remaining.length;

    const targetTier = Math.max(1, Math.min(16, Math.round(avgTierRaw)));
    const exclude = initialGamePlayerIds.join(",");
    const offerSeed = seed * 100 + roundIndex + 1;

    try {
      const url = `${API_BASE}/game/banker_offer?slot=${encodeURIComponent(
        slot
      )}&target_tier=${encodeURIComponent(
        targetTier
      )}&seed=${encodeURIComponent(offerSeed)}&exclude_ids=${encodeURIComponent(
        exclude
      )}`;

      const res = await fetch(url);
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text || "Offer request failed");
      }

      const data = (await res.json()) as BankerOfferResponse;
      setBankerOffer(data);
      setOfferRevealed(true);
    } catch {
      setBankerOfferError("Could not load banker offer.");
    } finally {
      setOfferLoading(false);
    }
  }

  function handleNoDeal() {
    setBankerOffer(null);
    setBankerOfferError(null);
    setOfferRevealed(false);
    setOfferLoading(false);

    const nextRound = roundIndex + 1;

    if (nextRound >= OPEN_SCHEDULE.length) {
      setPhase("final_choice");
      return;
    }

    setRoundIndex(nextRound);
    setRemainingToOpen(OPEN_SCHEDULE[nextRound]);
    setPhase("open_round");
    setRoundMessageOpen(true);
  }

  function acceptDeal() {
    if (!bankerOffer) return;

    setStartingFive((prev) => ({
      ...prev,
      [slot]: bankerOffer.player,
    }));

    setPhase("done");
  }

  function finalizeWithCase(chosenCase: GameCase) {
    setOpenedCaseNumbers((prev) => {
      const next = new Set(prev);
      next.add(chosenCase.case);
      return next;
    });

    setStartingFive((prev) => ({
      ...prev,
      [slot]: chosenCase.player,
    }));

    setLastOpened(chosenCase);
    setPhase("done");
  }

  function keepReserved() {
    if (!casePool || reservedCaseNumber == null) return;
    const c = casePool.cases.find((x) => x.case === reservedCaseNumber);
    if (!c) return;
    finalizeWithCase(c);
  }

  function switchToOther() {
    if (!lastOtherUnopenedCase) return;
    finalizeWithCase(lastOtherUnopenedCase);
  }

  const instructionText = useMemo(() => {
    if (!casePool) return "Generate cases to begin.";
    if (phase === "pick_reserved")
      return "Pick 1 case to set aside until the end.";
    if (phase === "open_round")
      return `Open ${remainingToOpen} case(s) this round.`;
    if (phase === "banker_offer") return "Banker offer time.";
    if (phase === "final_choice")
      return "Final choice: keep your case or switch.";
    if (phase === "done")
      return "Game complete. Your player has been added to your Starting Five.";
    return "";
  }, [casePool, phase, remainingToOpen]);

  const canClickCase = phase === "pick_reserved" || phase === "open_round";
  const canPickSlot =
    phase === "idle" || phase === "pick_reserved" || phase === "done";

  // ---------- Styling helpers ----------
  const panel =
    "rounded-xl border border-slate-700/60 bg-slate-900/70 shadow-lg backdrop-blur";
  const label = "block text-sm text-slate-200";
  const input =
    "mt-2 w-full rounded-md border border-slate-700 bg-slate-950/80 text-slate-100 p-2 outline-none focus:ring-2 focus:ring-slate-500/50 disabled:opacity-60";
  const btnBase =
    "rounded-md px-4 py-2 text-sm font-semibold transition disabled:opacity-50 disabled:cursor-not-allowed";
  const btnPrimary =
    btnBase + " bg-emerald-500 text-emerald-950 hover:bg-emerald-400";
  const btnSecondary =
    btnBase +
    " bg-slate-700 text-slate-100 hover:bg-slate-600 border border-slate-600";
  const btnInfo = btnBase + " bg-sky-500 text-sky-950 hover:bg-sky-400";
  const btnDanger = btnBase + " bg-rose-500 text-rose-950 hover:bg-rose-400";
  const btnOutline =
    btnBase + " border border-slate-600 text-slate-100 hover:bg-slate-800";

  return (
    <main className="min-h-screen bg-gradient-to-b from-slate-950 via-slate-950 to-slate-900 text-slate-100 p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold tracking-tight">
              Deal or No Deal
            </h1>
            <p className="text-sm text-slate-300 mt-1">
              Pick a position, generate 16 cases, then play through banker
              offers.
            </p>
          </div>

          <div className="flex gap-2">
            <button
              className={btnSecondary}
              type="button"
              onClick={resetCurrentGameKeepRoster}
            >
              New game
            </button>

            <button
              className={btnDanger}
              type="button"
              onClick={resetEverything}
            >
              Reset everything
            </button>
          </div>
        </div>

        {/* Round message modal */}
        {casePool && phase === "open_round" && roundMessageOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
            <div className="w-full max-w-md rounded-xl border border-slate-700 bg-slate-950 p-5 space-y-3 shadow-2xl">
              <div className="text-sm font-semibold text-slate-100">
                Round {roundIndex + 1}
              </div>
              <div className="text-sm text-slate-200">
                Select and open{" "}
                <span className="font-bold text-emerald-400">
                  {remainingToOpen}
                </span>{" "}
                case(s).
              </div>
              <button
                className={btnPrimary}
                type="button"
                onClick={() => setRoundMessageOpen(false)}
              >
                OK
              </button>
            </div>
          </div>
        )}

        {/* Starting Five */}
        <div className={panel + " p-4"}>
          <div className="mb-2 text-sm font-semibold text-slate-100">
            Starting Five
          </div>
          <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
            {(["PG", "SG", "SF", "PF", "C"] as Slot[]).map((s) => {
              const p = startingFive[s];
              return (
                <div
                  key={s}
                  className="rounded-lg border border-slate-700/60 bg-slate-950/40 p-3 text-sm"
                >
                  <div className="text-xs text-slate-300">{s}</div>
                  {p ? (
                    <div className="mt-1">
                      <div className="font-semibold text-slate-100">
                        {p.name}
                      </div>
                      <div className="text-slate-300">{p.team}</div>
                    </div>
                  ) : (
                    <div className="mt-1 text-slate-400">Empty</div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          {/* Left */}
          <div className={panel + " lg:col-span-2 p-4 space-y-4"}>
            <div className="flex flex-col gap-3 md:flex-row md:items-end">
              <label className={label}>
                Position
                <select
                  className={input}
                  value={slot}
                  onChange={(e) => setSlot(e.target.value as Slot)}
                  disabled={!canPickSlot}
                >
                  <option value="PG" disabled={playedSlots.has("PG")}>
                    PG {playedSlots.has("PG") ? "(completed)" : ""}
                  </option>
                  <option value="SG" disabled={playedSlots.has("SG")}>
                    SG {playedSlots.has("SG") ? "(completed)" : ""}
                  </option>
                  <option value="SF" disabled={playedSlots.has("SF")}>
                    SF {playedSlots.has("SF") ? "(completed)" : ""}
                  </option>
                  <option value="PF" disabled={playedSlots.has("PF")}>
                    PF {playedSlots.has("PF") ? "(completed)" : ""}
                  </option>
                  <option value="C" disabled={playedSlots.has("C")}>
                    C {playedSlots.has("C") ? "(completed)" : ""}
                  </option>
                </select>
              </label>

              <label className={label}>
                Seed
                <input
                  className={input}
                  type="number"
                  value={seed}
                  onChange={(e) => setSeed(Number(e.target.value))}
                  disabled={!canPickSlot}
                />
              </label>

              <button
                className={btnPrimary}
                type="button"
                onClick={loadCases}
                disabled={loadingCases || playedSlots.has(slot)}
              >
                {loadingCases ? "Loading..." : "Generate 16 cases"}
              </button>
            </div>

            {casesError && (
              <div className="rounded-lg border border-rose-800/60 bg-rose-950/40 p-3 text-sm text-rose-200">
                {casesError}
              </div>
            )}

            <div className="rounded-lg border border-slate-700/60 bg-slate-950/40 p-3 text-sm">
              <div className="font-semibold text-slate-100">Status</div>
              <div className="text-slate-200 mt-1">{instructionText}</div>
              {casePool && (
                <div className="text-slate-300 mt-1">
                  Remaining unopened: {remainingUnopenedCount}
                  {reservedCaseNumber != null
                    ? ` | Your case: ${reservedCaseNumber}`
                    : ""}
                  {phase === "open_round"
                    ? ` | Round ${roundIndex + 1}/${OPEN_SCHEDULE.length}`
                    : ""}
                </div>
              )}
            </div>

            {/* Cases grid */}
            {casePool && (
              <div className="grid grid-cols-4 gap-3 md:grid-cols-8">
                {casesByCaseNumber.map((c) => {
                  const opened = openedCaseNumbers.has(c.case);
                  const isReserved = reservedCaseNumber === c.case;

                  if (opened) return null;

                  const disabled =
                    !canClickCase || (phase === "open_round" && isReserved);

                  const className = [
                    "rounded-lg border px-3 py-3 text-sm font-semibold",
                    "bg-slate-950/40 text-slate-100",
                    isReserved
                      ? "border-emerald-400/70"
                      : "border-slate-700/60 hover:border-slate-500",
                    disabled
                      ? "opacity-50 cursor-not-allowed"
                      : "cursor-pointer",
                  ].join(" ");

                  return (
                    <button
                      key={c.case}
                      type="button"
                      className={className}
                      onClick={() => {
                        if (disabled) return;

                        if (phase === "pick_reserved") {
                          startAfterReservedPick(c.case);
                          return;
                        }

                        if (phase === "open_round") {
                          if (remainingToOpen <= 0) return;
                          openCase(c.case);
                        }
                      }}
                      title={
                        phase === "pick_reserved"
                          ? "Pick this as your case"
                          : isReserved
                          ? "This is your case (saved for the end)"
                          : ""
                      }
                    >
                      {isReserved ? `Your case ${c.case}` : `Case ${c.case}`}
                    </button>
                  );
                })}
              </div>
            )}

            {/* Last opened */}
            {casePool && lastOpened && (
              <div className="rounded-xl border border-slate-700/60 bg-slate-950/40 p-4 space-y-1">
                <div className="text-sm font-semibold text-slate-100">
                  Last opened
                </div>
                <div className="text-sm text-slate-300">
                  Case {lastOpened.case} (tier {lastOpened.tier})
                </div>
                <div className="text-lg font-bold text-slate-100">
                  {lastOpened.player.name}{" "}
                  <span className="text-slate-300 font-semibold">
                    - {lastOpened.player.team}
                  </span>
                </div>
              </div>
            )}

            {/* Banker offer */}
            {casePool && phase === "banker_offer" && (
              <div className="rounded-xl border border-slate-700/60 bg-slate-950/40 p-4 space-y-3">
                <div className="text-sm font-semibold text-slate-100">
                  Banker offer
                </div>

                {!offerRevealed ? (
                  <div className="text-sm text-slate-200">
                    The banker has an offer ready.
                  </div>
                ) : bankerOffer ? (
                  <div className="text-sm text-slate-200">
                    The banker offers{" "}
                    <span className="font-bold text-emerald-300">
                      {bankerOffer.player.name}
                    </span>{" "}
                    <span className="text-slate-300">
                      ({bankerOffer.player.team})
                    </span>{" "}
                    from tier{" "}
                    <span className="font-bold text-sky-300">
                      {bankerOffer.picked_tier}
                    </span>
                    .
                  </div>
                ) : null}

                {bankerOfferError && (
                  <div className="text-sm text-rose-200">
                    {bankerOfferError}
                  </div>
                )}

                <div className="flex gap-3 flex-wrap">
                  <button
                    className={btnInfo}
                    type="button"
                    onClick={revealOffer}
                    disabled={offerLoading || offerRevealed}
                  >
                    {offerLoading
                      ? "Revealing..."
                      : offerRevealed
                      ? "Offer revealed"
                      : "Reveal offer"}
                  </button>

                  <button
                    className={btnPrimary}
                    type="button"
                    onClick={acceptDeal}
                    disabled={!bankerOffer}
                  >
                    Deal
                  </button>

                  <button
                    className={btnOutline}
                    type="button"
                    onClick={handleNoDeal}
                  >
                    No deal
                  </button>
                </div>
              </div>
            )}

            {/* Final choice */}
            {casePool &&
              phase === "final_choice" &&
              reservedCaseNumber != null && (
                <div className="rounded-xl border border-slate-700/60 bg-slate-950/40 p-4 space-y-3">
                  <div className="text-sm font-semibold text-slate-100">
                    Final choice
                  </div>

                  {!lastOtherUnopenedCase ? (
                    <div className="text-sm text-slate-200">
                      Waiting for exactly one other unopened case besides your
                      case.
                    </div>
                  ) : (
                    <>
                      <div className="text-sm text-slate-200">
                        Keep your case{" "}
                        <span className="font-bold text-emerald-300">
                          #{reservedCaseNumber}
                        </span>{" "}
                        or switch to{" "}
                        <span className="font-bold text-sky-300">
                          #{lastOtherUnopenedCase.case}
                        </span>
                        ?
                      </div>

                      <div className="flex gap-3">
                        <button
                          className={btnPrimary}
                          type="button"
                          onClick={keepReserved}
                        >
                          Keep my case
                        </button>

                        <button
                          className={btnOutline}
                          type="button"
                          onClick={switchToOther}
                        >
                          Switch
                        </button>
                      </div>
                    </>
                  )}
                </div>
              )}

            {/* Done */}
            {casePool && phase === "done" && (
              <div className="rounded-xl border border-emerald-700/40 bg-emerald-950/20 p-4 space-y-2">
                <div className="text-sm font-semibold text-emerald-200">
                  Winner added
                </div>
                <div className="text-sm text-emerald-100/90">
                  Your chosen player has been added to {slot}. Start a new game
                  with a different position.
                </div>
              </div>
            )}
          </div>

          {/* Right list */}
          <div className={panel + " p-4 space-y-3"}>
            <div className="text-sm font-semibold text-slate-100">
              Generated players (ordered by tier)
            </div>

            {!casePool && (
              <div className="text-sm text-slate-300">
                Generate cases to see the 16 players here.
              </div>
            )}

            {casePool && (
              <div className="space-y-2 text-sm">
                {tierList.map((c) => {
                  const opened = openedCaseNumbers.has(c.case);
                  return (
                    <div
                      key={c.case}
                      className={[
                        "rounded-lg border p-2",
                        "border-slate-700/60 bg-slate-950/40",
                        opened ? "opacity-60" : "",
                      ].join(" ")}
                    >
                      <div className="text-xs text-slate-300">
                        Tier {c.tier}
                      </div>

                      <div
                        className={[
                          "font-semibold text-slate-100",
                          opened ? "line-through text-slate-300" : "",
                        ].join(" ")}
                      >
                        {c.player.name}
                      </div>

                      <div className="text-slate-300">{c.player.team}</div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        <div className="text-xs text-slate-400">
          Backend expected at {API_BASE}.
        </div>
      </div>
    </main>
  );
}
