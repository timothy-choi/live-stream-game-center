"""Canonical Madden 27 NFL team identities.

`team_id` is the stable code used in memory and PostgreSQL. Display names are
not identity. Left/right are scoreboard positions, not home/away.

Catalog support (32 teams) is not the same as visually validated recognition.
M10 visual templates cover only the teams observed on the Arcade fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

from lsgc.identity.errors import CatalogError


@dataclass(frozen=True)
class TeamIdentity:
    team_id: str
    """Stable canonical code, e.g. SF, NYG, GB. Primary identity."""

    display_name: str
    abbreviation: str
    aliases: tuple[str, ...] = ()
    """Uppercased exact aliases for the unvalidated text path."""


def _team(team_id: str, display_name: str, *aliases: str) -> TeamIdentity:
    extra = tuple(item.upper() for item in aliases)
    return TeamIdentity(
        team_id=team_id,
        display_name=display_name,
        abbreviation=team_id,
        aliases=extra,
    )


NFL_TEAMS: tuple[TeamIdentity, ...] = (
    _team("ARI", "Arizona Cardinals", "ARIZONA", "CARDINALS"),
    _team("ATL", "Atlanta Falcons", "ATLANTA", "FALCONS"),
    _team("BAL", "Baltimore Ravens", "BALTIMORE", "RAVENS"),
    _team("BUF", "Buffalo Bills", "BUFFALO", "BILLS"),
    _team("CAR", "Carolina Panthers", "CAROLINA", "PANTHERS"),
    _team("CHI", "Chicago Bears", "CHICAGO", "BEARS"),
    _team("CIN", "Cincinnati Bengals", "CINCINNATI", "BENGALS"),
    _team("CLE", "Cleveland Browns", "CLEVELAND", "BROWNS"),
    _team("DAL", "Dallas Cowboys", "DALLAS", "COWBOYS"),
    _team("DEN", "Denver Broncos", "DENVER", "BRONCOS"),
    _team("DET", "Detroit Lions", "DETROIT", "LIONS"),
    _team("GB", "Green Bay Packers", "GREEN BAY", "PACKERS", "GNB"),
    _team("HOU", "Houston Texans", "HOUSTON", "TEXANS"),
    _team("IND", "Indianapolis Colts", "INDIANAPOLIS", "COLTS"),
    _team("JAX", "Jacksonville Jaguars", "JACKSONVILLE", "JAGUARS", "JAC"),
    _team("KC", "Kansas City Chiefs", "KANSAS CITY", "CHIEFS", "KAN"),
    _team("LAC", "Los Angeles Chargers", "CHARGERS", "LA CHARGERS", "SAN DIEGO"),
    _team("LAR", "Los Angeles Rams", "RAMS", "LA RAMS", "ST LOUIS"),
    _team("LV", "Las Vegas Raiders", "RAIDERS", "LAS VEGAS", "OAKLAND", "OAK"),
    _team("MIA", "Miami Dolphins", "MIAMI", "DOLPHINS"),
    _team("MIN", "Minnesota Vikings", "MINNESOTA", "VIKINGS"),
    _team("NE", "New England Patriots", "NEW ENGLAND", "PATRIOTS", "NEP"),
    _team("NO", "New Orleans Saints", "NEW ORLEANS", "SAINTS", "NOR"),
    _team("NYG", "New York Giants", "GIANTS", "NY GIANTS"),
    _team("NYJ", "New York Jets", "JETS", "NY JETS"),
    _team("PHI", "Philadelphia Eagles", "PHILADELPHIA", "EAGLES"),
    _team("PIT", "Pittsburgh Steelers", "PITTSBURGH", "STEELERS"),
    _team("SEA", "Seattle Seahawks", "SEATTLE", "SEAHAWKS"),
    _team("SF", "San Francisco 49ers", "49ERS", "NINERS", "SAN FRANCISCO", "SFO"),
    _team("TB", "Tampa Bay Buccaneers", "TAMPA BAY", "BUCCANEERS", "TAM"),
    _team("TEN", "Tennessee Titans", "TENNESSEE", "TITANS"),
    _team("WAS", "Washington Commanders", "WASHINGTON", "COMMANDERS", "WSH"),
)


class TeamCatalog:
    """Lookup by canonical id or exact normalized alias."""

    def __init__(self, teams: tuple[TeamIdentity, ...] = NFL_TEAMS) -> None:
        if not teams:
            raise CatalogError("team catalog must not be empty")
        ids = [team.team_id for team in teams]
        if len(set(ids)) != len(ids):
            raise CatalogError("duplicate team_id in catalog")
        self.teams = teams
        self._by_id = {team.team_id: team for team in teams}
        aliases: dict[str, str] = {}
        for team in teams:
            keys = {team.team_id, team.abbreviation, *team.aliases, team.display_name.upper()}
            for key in keys:
                normalized = normalize_identity_text(key)
                if not normalized:
                    continue
                existing = aliases.get(normalized)
                if existing is not None and existing != team.team_id:
                    raise CatalogError(
                        f"alias {normalized!r} maps to both {existing} and {team.team_id}"
                    )
                aliases[normalized] = team.team_id
        self._aliases = aliases

    def get(self, team_id: str) -> TeamIdentity | None:
        return self._by_id.get(team_id)

    def require(self, team_id: str) -> TeamIdentity:
        team = self.get(team_id)
        if team is None:
            raise CatalogError(f"unknown team_id {team_id!r}")
        return team

    def resolve_text(self, text: str) -> str | None:
        """Exact alias match after conservative normalization. No fuzzy guess."""
        normalized = normalize_identity_text(text)
        if not normalized:
            return None
        return self._aliases.get(normalized)

    def known_ids(self) -> frozenset[str]:
        return frozenset(self._by_id)


def normalize_identity_text(text: str | None) -> str:
    """Uppercase, strip punctuation except spaces, collapse whitespace."""
    if not text:
        return ""
    chars: list[str] = []
    for char in text.upper():
        if char.isalnum() or char == " ":
            chars.append(char)
        elif char in "-_/.":
            chars.append(" ")
    collapsed = " ".join("".join(chars).split())
    return collapsed


DEFAULT_CATALOG = TeamCatalog()


def mentioned_team_ids(text: str | None, catalog: TeamCatalog | None = None) -> frozenset[str]:
    """Whole-alias hits in already-known metadata. Never used for recognition."""
    catalog = catalog or DEFAULT_CATALOG
    normalized = normalize_identity_text(text)
    if not normalized:
        return frozenset()
    remaining = f" {normalized} "
    found: set[str] = set()
    for alias, team_id in sorted(catalog._aliases.items(), key=lambda item: len(item[0]), reverse=True):
        padded = f" {alias} "
        if padded in remaining:
            found.add(team_id)
            remaining = remaining.replace(padded, " ")
    return frozenset(found)


def title_visual_disagreement(
    title: str | None,
    left_team_id: str,
    right_team_id: str,
    catalog: TeamCatalog | None = None,
) -> str | None:
    """Log-only: title mentions a team that is not the confirmed visual pair."""
    mentioned = mentioned_team_ids(title, catalog)
    visual = {left_team_id, right_team_id}
    extra = mentioned - visual
    if not extra:
        return None
    extras = ",".join(sorted(extra))
    return (
        f"title mentions {extras} which is not in confirmed visual "
        f"{left_team_id}/{right_team_id}"
    )
