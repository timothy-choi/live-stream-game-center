import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { conflictGame, endedGame, liveGame, upcomingGame } from "../test/fixtures";
import { GameCard } from "./GameCard";

function show(game: typeof liveGame) {
  return render(
    <MemoryRouter>
      <GameCard game={game} />
    </MemoryRouter>,
  );
}

test("live confirmed score", () => {
  show(liveGame);
  expect(screen.getByText("LIVE")).toBeInTheDocument();
  expect(screen.getByText("21")).toBeInTheDocument();
  expect(screen.getByText("14")).toBeInTheDocument();
  expect(screen.getByText("San Francisco 49ers vs Green Bay Packers")).toBeInTheDocument();
  expect(screen.queryByText("FINAL")).not.toBeInTheDocument();
});

test("unconfirmed score is not zero", () => {
  show({
    ...liveGame,
    score: { status: "UNCONFIRMED", left: null, right: null, revision: 0 },
  });
  expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

test("upcoming TBD teams", () => {
  show(upcomingGame);
  expect(screen.getByText("UPCOMING")).toBeInTheDocument();
  expect(screen.getAllByText("TBD")).toHaveLength(2);
  expect(screen.getByText("Later Arcade broadcast")).toBeInTheDocument();
});

test("ended latest trusted score is not FINAL", () => {
  show(endedGame);
  expect(screen.getByText("ENDED")).toBeInTheDocument();
  expect(screen.getByText("Latest tracked score")).toBeInTheDocument();
  expect(screen.getByText("28")).toBeInTheDocument();
  expect(screen.queryByText("FINAL")).not.toBeInTheDocument();
});

test("matchup conflict keeps confirmed teams", () => {
  show(conflictGame);
  expect(screen.getByText("Identity needs review")).toBeInTheDocument();
  expect(screen.getByText("SF")).toBeInTheDocument();
  expect(screen.getByText("GB")).toBeInTheDocument();
  expect(screen.queryByText("LAC")).not.toBeInTheDocument();
});
