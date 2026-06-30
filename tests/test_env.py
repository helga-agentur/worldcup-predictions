from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from worldcup_predictions.core.env import env_value


class EnvLoadingTest(unittest.TestCase):
    def test_project_env_is_loaded_with_python_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                'export TEST_DOTENV_VALUE="from dotenv"\n',
                encoding="utf-8",
            )
            os.environ.pop("TEST_DOTENV_VALUE", None)

            self.assertEqual(env_value(root, "TEST_DOTENV_VALUE"), "from dotenv")

            os.environ.pop("TEST_DOTENV_VALUE", None)

    def test_existing_process_env_wins_over_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("TEST_DOTENV_PRECEDENCE=from-file\n", encoding="utf-8")
            os.environ["TEST_DOTENV_PRECEDENCE"] = "from-process"

            self.assertEqual(env_value(root, "TEST_DOTENV_PRECEDENCE"), "from-process")

            os.environ.pop("TEST_DOTENV_PRECEDENCE", None)


if __name__ == "__main__":
    unittest.main()
