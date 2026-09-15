export const AUCTION_STORAGE_VERSION = 2;

const integer = (value, minimum = 0) =>
  Number.isInteger(Number(value)) && Number(value) >= minimum
    ? Number(value)
    : null;

export const playerIdKey = (id) => String(id);

const canonicalText = (value) =>
  String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

/** A compact fallback identity lets an auction survive a source replacing IDs. */
export const playerIdentity = (player) => ({
  name: canonicalText(player?.nome),
  team: canonicalText(player?.team_id || player?.squadra),
  role: String(player?.ruolo || ""),
});

const sameIdentity = (first, second) =>
  first?.name && first.name === second?.name && first.role === second?.role;

const resolvePlayer = (transaction, playersById, players) => {
  const byId = playersById.get(playerIdKey(transaction.playerId));
  if (byId) return byId;
  if (!transaction.identity?.name || !transaction.identity?.role) return null;
  const matches = players.filter((player) =>
    sameIdentity(transaction.identity, playerIdentity(player)),
  );
  if (matches.length === 1) return matches[0];
  const sameTeam = matches.filter(
    (player) => playerIdentity(player).team === transaction.identity.team,
  );
  return sameTeam.length === 1 ? sameTeam[0] : null;
};

export const emptyDraft = () => ({ playerId: null, query: "", price: "" });

export const draftPlayer = (draft, players) => {
  const id = draft?.playerId;
  if (id === null || id === undefined) return null;
  return (
    (players || []).find(
      (candidate) => playerIdKey(candidate.id) === playerIdKey(id),
    ) || null
  );
};

export const draftForQuery = (draft, players, query) => {
  const selected = draftPlayer(draft, players);
  return selected && query !== selected.nome
    ? { ...draft, playerId: null, query, price: "" }
    : { ...draft, query };
};

export const reconcileAuctionDraft = (draft, players, board) => {
  if (board?.storageReadOk === false) return draft;
  const selected = draftPlayer(draft, players);
  if (!selected)
    return draft?.playerId === null || draft?.playerId === undefined
      ? draft
      : emptyDraft();
  const assigned = board?.assigned?.[playerIdKey(selected.id)];
  const wrongRole = board?.activeRole && selected.ruolo !== board.activeRole;
  return assigned || wrongRole ? emptyDraft() : draft;
};

export const auctionPriceAtOrBelow = (value, rules) => {
  const minimum = rules.auction.minPrice;
  const increment = rules.auction.increment;
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric < minimum) return null;
  return minimum + Math.floor((numeric - minimum) / increment) * increment;
};

export const nearestAuctionPrice = (value, maximum, rules) => {
  const minimum = rules.auction.minPrice;
  const increment = rules.auction.increment;
  const ceiling = auctionPriceAtOrBelow(maximum, rules);
  if (ceiling == null) return null;
  const numeric = Number(value);
  const target = Number.isFinite(numeric) ? numeric : minimum;
  const snapped =
    minimum + Math.round((Math.max(minimum, target) - minimum) / increment) * increment;
  return Math.min(snapped, ceiling);
};

export const auctionStorageKey = (profileId) =>
  `fanta-auction-v${AUCTION_STORAGE_VERSION}:${encodeURIComponent(profileId || "default")}`;

const teamNames = (rules) =>
  Array.from({ length: rules.participants }, (_, index) =>
    String(
      rules.teamNames?.[index] ||
      (index === 0 ? "La mia squadra" : `Squadra ${index + 1}`),
    ),
  );

export const emptyAuction = (rules) => ({
  teams: teamNames(rules).map((name) => ({
    name,
    startingCredits: rules.startingCredits,
    credits: rules.startingCredits,
    roster: [],
  })),
  assigned: {},
  history: [],
  undone: [],
});

export const slotsLeft = (team, rules) =>
  Object.fromEntries(
    Object.entries(rules.rosterSlots).map(([role, count]) => [
      role,
      count - (team.roster || []).filter((player) => player.ruolo === role).length,
    ]),
  );

export const legalMaxBid = (team, rules) => {
  const openSlots = Object.values(slotsLeft(team, rules)).reduce(
    (sum, count) => sum + Math.max(0, count),
    0,
  );
  const raw = Math.max(
    0,
    team.credits - Math.max(0, openSlots - 1) * rules.auction.reserve,
  );
  return auctionPriceAtOrBelow(raw, rules) ?? 0;
};

export const isValidBid = (price, team, rules) => {
  const value = integer(price, rules.auction.minPrice);
  return (
    value != null &&
    (value - rules.auction.minPrice) % rules.auction.increment === 0 &&
    value <= legalMaxBid(team, rules)
  );
};

const transactionFrom = (item) => {
  const playerId = item?.playerId ?? item?.player?.id;
  const owner = integer(item?.owner);
  const price = integer(item?.price, 1);
  return playerId == null || owner == null || price == null
    ? null
    : {
      playerId,
      owner,
      price,
      identity:
        item?.identity && typeof item.identity === "object"
          ? {
            name: canonicalText(item.identity.name),
            team: canonicalText(item.identity.team),
            role: String(item.identity.role || ""),
          }
          : null,
    };
};

const hydrate = (seed, transactions, playersById, players, rules, { recover = false } = {}) => {
  const state = {
    ...seed,
    teams: seed.teams.map((team) => ({ ...team, roster: [] })),
    assigned: {},
    history: [],
    undone: [],
  };
  const unresolved = [];
  for (const transaction of transactions) {
    const player = resolvePlayer(transaction, playersById, players);
    const team = state.teams[transaction.owner];
    if (
      !player ||
      !team ||
      state.assigned[playerIdKey(transaction.playerId)] ||
      !Object.hasOwn(rules.rosterSlots, player.ruolo) ||
      slotsLeft(team, rules)[player.ruolo] < 1 ||
      !isValidBid(transaction.price, team, rules)
    ) {
      if (!recover) return null;
      unresolved.push(transaction);
      continue;
    }
    const record = {
      playerId: player.id,
      owner: transaction.owner,
      price: transaction.price,
      identity: playerIdentity(player),
    };
    team.credits -= transaction.price;
    team.roster.push(player);
    state.assigned[playerIdKey(player.id)] = { owner: transaction.owner, price: transaction.price };
    state.history.push(record);
  }
  return { state, unresolved };
};

/** Rebuilds runtime player references from compact, versioned transactions. */
export const rehydrateAuction = (saved, players, rules) => {
  if (!saved || typeof saved !== "object") return null;
  const isCurrent = saved.version === AUCTION_STORAGE_VERSION;
  const rawTeams = Array.isArray(saved.teams) ? saved.teams : null;
  const rawHistory = Array.isArray(saved.history) ? saved.history : null;
  const rawUndone = Array.isArray(saved.undone) ? saved.undone : [];
  if (!rawTeams || rawTeams.length !== rules.participants || !rawHistory) return null;
  const playersById = new Map((players || []).map((player) => [playerIdKey(player.id), player]));
  const transactions = rawHistory.map(transactionFrom);
  const undone = rawUndone.map(transactionFrom);
  if (transactions.some((item) => !item) || undone.some((item) => !item)) return null;
  const spent = rawTeams.map((_, index) =>
    transactions.reduce((sum, item) => sum + (item.owner === index ? item.price : 0), 0),
  );
  const teams = rawTeams.map((team, index) => {
    const credits = integer(isCurrent ? team?.startingCredits : Number(team?.credits) + spent[index], 0);
    return typeof team?.name === "string" && credits != null
      ? { name: team.name, startingCredits: credits, credits, roster: [] }
      : null;
  });
  if (teams.some((team) => !team)) return null;
  const hydrated = hydrate(
    { teams, assigned: {}, history: [], undone: [] },
    transactions,
    playersById,
    players || [],
    rules,
  );
  if (!hydrated) return null;
  const state = hydrated.state;
  const redoState = {
    ...state,
    teams: state.teams.map((team) => ({ ...team, roster: team.roster.slice() })),
    assigned: { ...state.assigned },
  };
  // Redo restores the newest undone transaction first, so validate that sequence.
  for (const item of undone.slice().reverse()) {
    const player = resolvePlayer(item, playersById, players || []);
    const team = redoState.teams[item.owner];
    if (
      !player ||
      !team ||
      redoState.assigned[playerIdKey(item.playerId)] ||
      !Object.hasOwn(rules.rosterSlots, player.ruolo) ||
      slotsLeft(team, rules)[player.ruolo] < 1 ||
      !isValidBid(item.price, team, rules)
    ) return null;
    team.credits -= item.price;
    team.roster.push(player);
    redoState.assigned[playerIdKey(player.id)] = { owner: item.owner, price: item.price };
  }
  state.undone = undone.map((item) => {
    const player = resolvePlayer(item, playersById, players || []);
    return { ...item, playerId: player.id, identity: playerIdentity(player) };
  });
  return state;
};

/** Restores every compatible transaction and reports only records requiring review. */
export const recoverAuction = (saved, players, rules) => {
  if (!saved || typeof saved !== "object") return null;
  const rawTeams = Array.isArray(saved.teams) ? saved.teams : null;
  const rawHistory = Array.isArray(saved.history) ? saved.history : null;
  if (!rawTeams || rawTeams.length !== rules.participants || !rawHistory) return null;
  const transactions = rawHistory.map(transactionFrom);
  if (transactions.some((item) => !item)) return null;
  const playersById = new Map((players || []).map((player) => [playerIdKey(player.id), player]));
  const teams = rawTeams.map((team) => {
    const credits = integer(team?.startingCredits, 0);
    return typeof team?.name === "string" && credits != null
      ? { name: team.name, startingCredits: credits, credits, roster: [] }
      : null;
  });
  if (teams.some((team) => !team)) return null;
  const hydrated = hydrate(
    { teams, assigned: {}, history: [], undone: [] },
    transactions,
    playersById,
    players || [],
    rules,
    { recover: true },
  );
  return hydrated && {
    state: hydrated.state,
    unresolved: hydrated.unresolved.map((item) => ({
      playerId: item.playerId,
      name: item.identity?.name || `ID ${item.playerId}`,
    })),
  };
};

export const serializeAuction = (state) => ({
  version: AUCTION_STORAGE_VERSION,
  teams: state.teams.map(({ name, startingCredits }) => ({ name, startingCredits })),
  history: state.history.map(({ playerId, owner, price, identity }) => ({ playerId, owner, price, identity })),
  undone: (state.undone || []).map(({ playerId, owner, price, identity }) => ({ playerId, owner, price, identity })),
});
