import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ds4codex import config


class InitConfigTests(unittest.TestCase):
    def test_init_all_configs_writes_requested_port(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "config.toml"
            catalog_path = Path(td) / "catalog.json"

            with patch("ds4codex.config.load_codex_template_model", return_value=config.FALLBACK_TEMPLATE_MODEL):
                result = config.init_all_configs(
                    codex_config_path=config_path,
                    model_catalog_path=catalog_path,
                    port=9123,
                    apikey="sk-test",
                    force=True,
                )

            self.assertTrue(result.updated_codex_config)
            text = config_path.read_text(encoding="utf-8")
            self.assertIn("port = 9123", text)
            self.assertIn('base_url = "http://127.0.0.1:9123/v1"', text)
            self.assertIn('model_catalog_json = "' + str(catalog_path) + '"', text)


if __name__ == "__main__":
    unittest.main()
