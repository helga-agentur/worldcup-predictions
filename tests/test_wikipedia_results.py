from __future__ import annotations

import unittest

from worldcup_predictions.plugins.sources.fixtures.wikipedia_results.plugin import (
    parse_wikipedia_footballbox_matches,
    wikipedia_result_records,
)
from worldcup_predictions.tournament import FixtureRecord, TeamResolver, build_tournament_state


def _footballbox(home: str, away: str, score: str, *, date: str = "9 July 2026", penalties: str | None = None) -> str:
    penalty_html = ""
    if penalties:
        penalty_html = (
            '<tr><td>Penalties</td></tr>'
            f'<tr><td class="fscore">{penalties}</td></tr>'
        )
    return (
        '<div class="footballbox">'
        f"<div>{date}</div>"
        "<table><tr>"
        f'<th class="fhome"><a href="/wiki/x">{home}</a></th>'
        f'<th class="fscore">{score}</th>'
        f'<th class="faway"><a href="/wiki/y">{away}</a></th>'
        "</tr></table>"
        f"{penalty_html}"
        "</div>"
    )


class WikipediaFootballboxParserTest(unittest.TestCase):
    def test_parses_en_dash_score_and_teams(self) -> None:
        html = _footballbox("France", "Morocco", "2–0")

        matches = parse_wikipedia_footballbox_matches(html)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["home_team"], "France")
        self.assertEqual(matches[0]["away_team"], "Morocco")
        self.assertEqual((matches[0]["home_score"], matches[0]["away_score"]), (2, 0))
        self.assertFalse(matches[0]["after_extra_time"])
        self.assertIsNone(matches[0]["penalties"])
        self.assertEqual(matches[0]["date"], "2026-07-09")

    def test_parses_extra_time_and_penalty_shootout(self) -> None:
        html = _footballbox("Norway", "England", "1–1 (a.e.t.)", penalties="4–2")

        matches = parse_wikipedia_footballbox_matches(html)

        self.assertEqual(len(matches), 1)
        self.assertTrue(matches[0]["after_extra_time"])
        self.assertEqual(matches[0]["penalties"], (4, 2))

    def test_ambiguous_blocks_are_skipped_not_guessed(self) -> None:
        upcoming = _footballbox("Spain", "Belgium", "19:00")  # kickoff time, not a score
        missing_team = _footballbox("Argentina", "", "1–0")
        level_shootout = _footballbox("Norway", "England", "1–1 (a.e.t.)", penalties="3–3")

        self.assertEqual(parse_wikipedia_footballbox_matches(upcoming), [])
        self.assertEqual(parse_wikipedia_footballbox_matches(missing_team), [])
        # A level shoot-out is impossible; the score row survives, penalties do not.
        matches = parse_wikipedia_footballbox_matches(level_shootout)
        self.assertEqual(len(matches), 1)
        self.assertIsNone(matches[0]["penalties"])

    def test_zero_matches_on_unrelated_html(self) -> None:
        self.assertEqual(parse_wikipedia_footballbox_matches("<html><body><p>No matches here.</p></body></html>"), [])


class WikipediaResultRecordsTest(unittest.TestCase):
    def _fixture(self, home: str, away: str, event_date: str) -> FixtureRecord:
        resolver = TeamResolver.default()
        return FixtureRecord(
            event_date=event_date,
            home_team=resolver.resolve(home),
            away_team=resolver.resolve(away),
            stage="Quarter-final",
        )

    def test_maps_parsed_match_onto_canonical_fixture(self) -> None:
        fixture = self._fixture("France", "Morocco", "2026-07-09T20:00:00Z")
        state = build_tournament_state([fixture], [])
        matches = parse_wikipedia_footballbox_matches(_footballbox("France", "Morocco", "2–0"))

        records, unmatched = wikipedia_result_records(matches, state=state, page_title="2026 FIFA World Cup knockout stage")

        self.assertEqual(unmatched, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].event_date, fixture.event_date)
        self.assertEqual((records[0].score.home, records[0].score.away), (2, 0))
        self.assertEqual(records[0].source, "wikipedia_results")

    def test_swapped_listing_reorients_score_to_canonical_fixture(self) -> None:
        fixture = self._fixture("France", "Morocco", "2026-07-09T20:00:00Z")
        state = build_tournament_state([fixture], [])
        matches = parse_wikipedia_footballbox_matches(_footballbox("Morocco", "France", "0–2"))

        records, unmatched = wikipedia_result_records(matches, state=state, page_title="p")

        self.assertEqual(unmatched, 0)
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0].score.home, records[0].score.away), (2, 0))
        self.assertEqual(records[0].metadata["orientation"], "swapped")

    def test_unknown_pairing_counts_as_unmatched_instead_of_guessing(self) -> None:
        fixture = self._fixture("Spain", "Belgium", "2026-07-10T19:00:00Z")
        state = build_tournament_state([fixture], [])
        matches = parse_wikipedia_footballbox_matches(_footballbox("France", "Morocco", "2–0"))

        records, unmatched = wikipedia_result_records(matches, state=state, page_title="p")

        self.assertEqual(records, [])
        self.assertEqual(unmatched, 1)

    def test_penalty_shootout_lands_in_metadata(self) -> None:
        fixture = self._fixture("Norway", "England", "2026-07-11T21:00:00Z")
        state = build_tournament_state([fixture], [])
        matches = parse_wikipedia_footballbox_matches(
            _footballbox("Norway", "England", "1–1 (a.e.t.)", date="11 July 2026", penalties="4–2")
        )

        records, unmatched = wikipedia_result_records(matches, state=state, page_title="p")

        self.assertEqual(unmatched, 0)
        self.assertEqual((records[0].score.home, records[0].score.away), (1, 1))
        self.assertEqual(records[0].metadata["home_penalty_score"], 4)
        self.assertEqual(records[0].metadata["away_penalty_score"], 2)
        self.assertTrue(records[0].metadata["after_extra_time"])


if __name__ == "__main__":
    unittest.main()
