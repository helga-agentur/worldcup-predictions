from __future__ import annotations

import unittest

from worldcup_predictions.entities import load_country_registry, load_entity_registry, normalize_entity_text


class CountryRegistryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_country_registry()

    EXPECTED_FIFA_CODES = {
        "ALG",
        "ARG",
        "AUS",
        "AUT",
        "BEL",
        "BIH",
        "BRA",
        "CAN",
        "CIV",
        "COD",
        "COL",
        "CPV",
        "CRO",
        "CUW",
        "CZE",
        "ECU",
        "EGY",
        "ENG",
        "ESP",
        "FRA",
        "GER",
        "GHA",
        "HAI",
        "IRN",
        "IRQ",
        "JOR",
        "JPN",
        "KOR",
        "KSA",
        "MAR",
        "MEX",
        "NED",
        "NOR",
        "NZL",
        "PAN",
        "PAR",
        "POR",
        "QAT",
        "RSA",
        "SCO",
        "SEN",
        "SUI",
        "SWE",
        "TUN",
        "TUR",
        "URU",
        "USA",
        "UZB",
    }

    def test_registry_contains_all_participating_countries(self) -> None:
        self.assertEqual(len(self.registry.countries), 48)
        self.assertIn("GER", self.registry.countries)
        self.assertIn("SCO", self.registry.countries)
        self.assertIn("ENG", self.registry.countries)
        self.assertIn("URU", self.registry.countries)

    def test_registry_uses_expected_fifa_codes(self) -> None:
        self.assertEqual(set(self.registry.countries), self.EXPECTED_FIFA_CODES)
        for code, country in self.registry.countries.items():
            self.assertRegex(code, r"^[A-Z]{3}$")
            self.assertIn(code, country.codes)

    def test_resolves_fifa_code(self) -> None:
        result = self.registry.resolve("GER")

        self.assertIsNotNone(result)
        self.assertTrue(result.is_resolved)
        self.assertEqual(result.canonical_id, "GER")
        self.assertEqual(result.method, "code")

    def test_resolves_alternate_source_code_to_fifa_code(self) -> None:
        result = self.registry.resolve("URY")

        self.assertIsNotNone(result)
        self.assertTrue(result.is_resolved)
        self.assertEqual(result.canonical_id, "URU")
        self.assertEqual(result.method, "code")

    def test_resolves_german_alias(self) -> None:
        result = self.registry.resolve("Deutschland", locale="de")

        self.assertIsNotNone(result)
        self.assertEqual(result.canonical_id, "GER")
        self.assertEqual(result.matched_locale, "de")

    def test_resolves_english_alias_without_locale(self) -> None:
        result = self.registry.resolve("Switzerland")

        self.assertIsNotNone(result)
        self.assertEqual(result.canonical_id, "SUI")

    def test_normalization_handles_diacritics_and_punctuation(self) -> None:
        self.assertEqual(normalize_entity_text("Côte d’Ivoire"), "cote d ivoire")
        self.assertEqual(normalize_entity_text("Saudi-Arabien"), "saudi arabien")

        result = self.registry.resolve("Cote d Ivoire")

        self.assertIsNotNone(result)
        self.assertEqual(result.canonical_id, "CIV")

    def test_common_workflow_aliases(self) -> None:
        cases = {
            "Holland": "NED",
            "Kap Verde": "CPV",
            "Bosnien-Herzegowina": "BIH",
            "Suedkorea": "KOR",
            "Curacao": "CUW",
            "Usbekistan": "UZB",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                result = self.registry.resolve(value)
                self.assertIsNotNone(result)
                self.assertEqual(result.canonical_id, expected)

    def test_source_observed_aliases(self) -> None:
        cases = {
            "Bosnia-H.": "BIH",
            "Bosnien-Herzeg.": "BIH",
            "Desert Foxes": "ALG",
            "Les Éléphants": "CIV",
            "OFB-Team": "AUT",
            "Team USA": "USA",
            "La Nati": "SUI",
            "URU": "URU",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                result = self.registry.resolve(value)
                self.assertIsNotNone(result)
                self.assertEqual(result.canonical_id, expected)

    def test_ambiguous_alias_does_not_auto_assign(self) -> None:
        result = self.registry.resolve("Congo")

        self.assertIsNotNone(result)
        self.assertFalse(result.is_resolved)
        self.assertTrue(result.is_ambiguous)
        self.assertEqual(result.candidates, ("COD",))

    def test_unknown_text_returns_none(self) -> None:
        self.assertIsNone(self.registry.resolve("Atlantis"))

    def test_generic_entity_registry_resolves_i18n_aliases(self) -> None:
        registry = load_entity_registry()

        self.assertEqual(registry.resolve("Torwart", locale="de").canonical_id, "goalkeeper")
        self.assertEqual(registry.resolve("red card").canonical_id, "red_card")
        self.assertEqual(registry.resolve("20 Minuten").canonical_id, "20min.ch")

    def test_generic_entity_registry_detects_alias_mentions(self) -> None:
        registry = load_entity_registry()

        resolved = registry.detect_aliases("A striker is doubtful after heavy rain delayed training.")
        ids = {item.canonical_id for item in resolved}

        self.assertIn("forward", ids)
        self.assertIn("injury", ids)


if __name__ == "__main__":
    unittest.main()
