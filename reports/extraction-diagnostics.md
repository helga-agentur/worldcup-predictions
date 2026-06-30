# Extraction Diagnostics

## Rejection Summary

| Source | Extractor | Reason | Rows |
| --- | --- | --- | ---: |
| public_analysis | public_analysis_v2 | `no_supported_signal_or_stat` | 14 |
| lineup_availability | lineup_availability_v1 | `no_unambiguous_team_side` | 7 |
| automatic_match_notes | automatic_match_notes_v1 | `not_pregame_analysis` | 5 |
| postmatch_stats | postmatch_stats_public_analysis_v1 | `no_parseable_xg_or_stat_fields` | 5 |
| public_analysis | public_analysis_v2 | `accepted` | 4 |
| lineup_availability | lineup_availability_v1 | `no_availability_signal` | 1 |
| public_analysis | public_analysis_v2 | `fixture_not_mentioned` | 1 |
| twenty_min_public | public_page_analysis_v1 | `accepted` | 1 |

## Fixture Details

| Fixture | Phase | Source | Status | Reason | Title |
| --- | --- | --- | --- | --- | --- |
| 2026-06-16T19:00:00Z\|FRA\|SEN | postgame | 20min public tippspiel page | accepted | `accepted` | 20 Minuten WM-Tippspiel 2026: Jetzt mittippen & Preise gewinnen! |
| 2026-06-16T19:00:00Z\|FRA\|SEN | postgame | 20min public tippspiel page | rejected | `no_parseable_xg_or_stat_fields` | 20 Minuten WM-Tippspiel 2026: Jetzt mittippen & Preise gewinnen! |
| 2026-06-16T19:00:00Z\|FRA\|SEN | postgame | 20min public tippspiel page | rejected | `not_pregame_analysis` | 20 Minuten WM-Tippspiel 2026: Jetzt mittippen & Preise gewinnen! |
| 2026-06-27T03:00:00Z\|EGY\|IRN | postgame | Fox Sports | accepted | `accepted` | Egypt Manager Hossam Hassan Addresses Concerns Over Mohamed Salah's Left Knee Injury |
| 2026-06-27T03:00:00Z\|EGY\|IRN | postgame | Fox Sports | rejected | `no_parseable_xg_or_stat_fields` | Egypt Manager Hossam Hassan Addresses Concerns Over Mohamed Salah's Left Knee Injury |
| 2026-06-27T03:00:00Z\|EGY\|IRN | postgame | Fox Sports | rejected | `not_pregame_analysis` | Egypt Manager Hossam Hassan Addresses Concerns Over Mohamed Salah's Left Knee Injury |
| 2026-06-27T21:00:00Z\|CRO\|GHA | postgame | BBC News | rejected | `no_supported_signal_or_stat` | England frustrated by Ghana: How far can they progress? |
| 2026-06-27T21:00:00Z\|PAN\|ENG | postgame | Sky Sports | accepted | `accepted` | Tuchel: Saka pain free and ready to start against Panama |
| 2026-06-27T21:00:00Z\|PAN\|ENG | postgame | Sky Sports | rejected | `no_parseable_xg_or_stat_fields` | Tuchel: Saka pain free and ready to start against Panama |
| 2026-06-27T21:00:00Z\|PAN\|ENG | postgame | BBC News | rejected | `no_supported_signal_or_stat` | England frustrated by Ghana: How far can they progress? |
| 2026-06-27T21:00:00Z\|PAN\|ENG | postgame | BBC News | rejected | `no_supported_signal_or_stat` | Scouting report on Panama - why England should be wary |
| 2026-06-27T21:00:00Z\|PAN\|ENG | postgame | BBC News | rejected | `no_supported_signal_or_stat` | Scouting report on Panama - why England should be wary |
| 2026-06-27T21:00:00Z\|PAN\|ENG | postgame | Sky Sports | rejected | `not_pregame_analysis` | Tuchel: Saka pain free and ready to start against Panama |
| 2026-06-28T02:00:00Z\|ALG\|AUT | postgame | BBC News | accepted | `accepted` | Algeria and Austria both qualify for last 32 after dramatic injury-time goals |
| 2026-06-28T02:00:00Z\|ALG\|AUT | postgame | BBC News | accepted | `accepted` | Austria score dramatic late equaliser against Algeria as both progress |
| 2026-06-28T02:00:00Z\|ALG\|AUT | postgame | BBC News | rejected | `no_parseable_xg_or_stat_fields` | Algeria and Austria both qualify for last 32 after dramatic injury-time goals |
| 2026-06-28T02:00:00Z\|ALG\|AUT | postgame | BBC News | rejected | `no_parseable_xg_or_stat_fields` | Austria score dramatic late equaliser against Algeria as both progress |
| 2026-06-28T02:00:00Z\|ALG\|AUT | postgame | BBC News | rejected | `not_pregame_analysis` | Algeria and Austria both qualify for last 32 after dramatic injury-time goals |
| 2026-06-28T02:00:00Z\|ALG\|AUT | postgame | BBC News | rejected | `not_pregame_analysis` | Austria score dramatic late equaliser against Algeria as both progress |
| 2026-06-28T19:00:00Z\|RSA\|CAN | postgame | CBS Sports | rejected | `no_supported_signal_or_stat` | Use DraftKings promo code for $200 in bonus bets by targeting Canada-South Africa, Red Sox-Yankees on Sunday |
| 2026-06-28T19:00:00Z\|RSA\|CAN | postgame | CBS Sports | rejected | `no_supported_signal_or_stat` | Use DraftKings promo code for $200 in bonus bets by targeting Canada-South Africa, Yankees-Red Sox on Sunday |
| 2026-07-01T16:00:00Z\|ENG\|COD | pregame | BBC News | rejected | `fixture_not_mentioned` | Witnessing the 'Hand of God' |
| 2026-07-01T16:00:00Z\|ENG\|COD | pregame | BBC News | rejected | `no_availability_signal` | Witnessing the 'Hand of God' |
| 2026-07-01T16:00:00Z\|ENG\|COD | pregame | CBS Sports | rejected | `no_supported_signal_or_stat` | 2026 World Cup picks, odds, predictions: Best bets for Portugal-DR Congo, England-Croatia on Wednesday |
| 2026-07-02T00:00:00Z\|USA\|BIH | pregame | CBS Sports | rejected | `no_supported_signal_or_stat` | 2026 World Cup picks, odds, predictions: Best bets for USA-Paraguay, Canada-Bosnia and Herzegovina on Friday |
| 2026-07-02T00:00:00Z\|USA\|BIH | pregame | CBS Sports | rejected | `no_supported_signal_or_stat` | 2026 World Cup parlay, best bets: Picks for USA vs. Paraguay, Canada vs. Bosnia and Herzegovina on Friday |
| 2026-07-02T00:00:00Z\|USA\|BIH | pregame | Fox Sports | rejected | `no_supported_signal_or_stat` | Why USA Fans Should 'Take A Deep Breath' After Loss vs. Türkiye |
| 2026-07-02T00:00:00Z\|USA\|BIH | pregame | CBS Sports | rejected | `no_unambiguous_team_side` | 2026 World Cup parlay, best bets: Picks for USA vs. Paraguay, Canada vs. Bosnia and Herzegovina on Friday |
| 2026-07-02T00:00:00Z\|USA\|BIH | pregame | Fox Sports | rejected | `no_unambiguous_team_side` | Why USA Fans Should 'Take A Deep Breath' After Loss vs. Türkiye |
| 2026-07-02T23:00:00Z\|POR\|CRO | pregame | CBS Sports | rejected | `no_supported_signal_or_stat` | 2026 World Cup parlay, best bets: Top picks for matches on Tuesday include England-Ghana, Portugal, Croatia |
| 2026-07-02T23:00:00Z\|POR\|CRO | pregame | BBC News | rejected | `no_supported_signal_or_stat` | England frustrated by Ghana: How far can they progress? |
| 2026-07-02T23:00:00Z\|POR\|CRO | pregame | CBS Sports | rejected | `no_supported_signal_or_stat` | 2026 World Cup picks, odds, predictions: Best bets for Portugal-DR Congo, England-Croatia on Wednesday |
| 2026-07-02T23:00:00Z\|POR\|CRO | pregame | CBS Sports | rejected | `no_unambiguous_team_side` | 2026 World Cup parlay, best bets: Top picks for matches on Tuesday include England-Ghana, Portugal, Croatia |
| 2026-07-02T23:00:00Z\|POR\|CRO | pregame | BBC News | rejected | `no_unambiguous_team_side` | England frustrated by Ghana: How far can they progress? |
| 2026-07-03T18:00:00Z\|AUS\|EGY | pregame | BBC News | rejected | `no_unambiguous_team_side` | Salah an injury doubt for Egypt's last-32 tie |
| 2026-07-03T18:00:00Z\|AUS\|EGY | pregame | BBC News | rejected | `no_unambiguous_team_side` | Salah an injury doubt for Egypt's last-32 tie |
| 2026-07-04T01:30:00Z\|COL\|GHA | pregame | Fox Sports | rejected | `no_supported_signal_or_stat` | 2026 World Cup Group L Scenarios, Standings: What England, Ghana, Croatia, Panama Need To Advance |
| 2026-07-04T01:30:00Z\|COL\|GHA | pregame | Fox Sports | rejected | `no_unambiguous_team_side` | 2026 World Cup Group L Scenarios, Standings: What England, Ghana, Croatia, Panama Need To Advance |
