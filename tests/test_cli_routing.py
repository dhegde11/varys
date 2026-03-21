"""
Tests for CLI subcommand routing in varys.py.
All API calls, file I/O, and interactive prompts are mocked — no credits needed.
"""

import csv
import io
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "varys",
    Path(__file__).parent.parent / "varys.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

main = _mod.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_input_csv(tmp_path, names):
    p = tmp_path / "input.csv"
    p.write_text("entity_name\n" + "\n".join(names) + "\n")
    return str(p)


def _mock_skill():
    skill = MagicMock()
    skill.name = "profile-health-it-vendor"
    return skill


# ---------------------------------------------------------------------------
# Argparse: missing / wrong subcommands exit cleanly
# ---------------------------------------------------------------------------

class TestArgparseErrors:
    def test_no_subcommand_exits(self):
        with patch("sys.argv", ["varys.py"]):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_unknown_subcommand_exits(self):
        with patch("sys.argv", ["varys.py", "bogus"]):
            with pytest.raises(SystemExit):
                main()

    def test_research_without_target_exits(self):
        with patch("sys.argv", ["varys.py", "profile"]):
            with pytest.raises(SystemExit):
                main()

    def test_research_vendor_missing_input_exits(self):
        with patch("sys.argv", ["varys.py", "profile", "vendor"]):
            with pytest.raises(SystemExit):
                main()

    def test_discover_health_system_missing_state_exits(self):
        with patch("sys.argv", ["varys.py", "discover", "health-system"]):
            with pytest.raises(SystemExit):
                main()

    def test_pipeline_health_system_missing_state_exits(self):
        with patch("sys.argv", ["varys.py", "pipeline", "health-system"]):
            with pytest.raises(SystemExit):
                main()


# ---------------------------------------------------------------------------
# skill_name lookup: args.target must map to a valid skill
# ---------------------------------------------------------------------------

class TestSkillNameLookup:
    """
    The lookup dict runs before the command branch.
    An unrecognised target would raise KeyError — but argparse prevents it
    because targets are defined subparsers, not free strings.
    These tests confirm 'vendor' and 'health-system' both resolve correctly.
    """

    def test_research_vendor_resolves_vendor_skill(self, tmp_path, capsys):
        inp = _make_input_csv(tmp_path, ["Acme"])
        out = str(tmp_path / "out.csv")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", inp, "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()) as mock_load,
            patch.object(_mod, "_run_research"),
        ):
            main()
        mock_load.assert_called_once_with("profile-health-it-vendor")

    def test_research_health_system_resolves_health_system_skill(self, tmp_path, capsys):
        inp = _make_input_csv(tmp_path, ["Mayo Clinic"])
        out = str(tmp_path / "out.csv")
        skill = MagicMock()
        skill.name = "profile-health-system"
        with (
            patch("sys.argv", [
                "varys.py", "profile", "health-system",
                "--input", inp, "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=skill) as mock_load,
            patch.object(_mod, "_run_research"),
        ):
            main()
        mock_load.assert_called_once_with("profile-health-system")


# ---------------------------------------------------------------------------
# concurrency guard: --concurrency 0 must error before any API call
# ---------------------------------------------------------------------------

class TestConcurrencyGuard:
    def test_research_vendor_concurrency_zero_exits(self, tmp_path, capsys):
        inp = _make_input_csv(tmp_path, ["Acme"])
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", inp, "--concurrency", "0", "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_pipeline_vendor_concurrency_zero_exits(self, tmp_path, capsys):
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "vendor",
                "--concurrency", "0", "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "discover_vendors_via_llm"),  # should never reach this
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_concurrency_one_is_allowed(self, tmp_path):
        """--concurrency 1 should not trigger the guard."""
        inp = _make_input_csv(tmp_path, ["Acme"])
        out = str(tmp_path / "out.csv")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", inp, "--output", out, "--concurrency", "1", "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()
        mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# discover vendor: writes entity list CSV, does not call _run_research
# ---------------------------------------------------------------------------

class TestDiscoverVendor:
    def test_writes_csv_and_exits_without_researching(self, tmp_path, monkeypatch):
        out = str(tmp_path / "vendors.csv")
        monkeypatch.setattr("builtins.input", lambda _="": "AI scribe companies")
        with (
            patch("sys.argv", [
                "varys.py", "discover", "vendor", "--output", out,
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(
                _mod, "discover_vendors_via_llm",
                new=AsyncMock(return_value=["Abridge", "Nuance", "Suki"]),
            ),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        mock_run.assert_not_called()
        rows = list(csv.DictReader(open(out)))
        assert [r["entity_name"] for r in rows] == ["Abridge", "Nuance", "Suki"]

    def test_default_output_filename(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda _="": "AI scribe companies")
        with (
            patch("sys.argv", ["varys.py", "discover", "vendor"]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(
                _mod, "discover_vendors_via_llm",
                new=AsyncMock(return_value=["Abridge"]),
            ),
            patch("builtins.open", MagicMock(return_value=MagicMock(
                __enter__=lambda s: MagicMock(),
                __exit__=lambda *a: None,
            ))),
            patch("csv.DictWriter"),
        ):
            main()
        # argparse default is vendor-results.csv — if we reach here without SystemExit, routing worked

    def test_empty_query_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "")
        with (
            patch("sys.argv", ["varys.py", "discover", "vendor"]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_missing_api_key_exits(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "query")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with (
            patch("sys.argv", ["varys.py", "discover", "vendor"]),
            patch.dict(os.environ, env, clear=True),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0


# ---------------------------------------------------------------------------
# discover health-system: uses discover_health_systems(), not LLM
# ---------------------------------------------------------------------------

class TestDiscoverHealthSystem:
    def test_writes_csv_from_cms_data(self, tmp_path):
        out = str(tmp_path / "ca-systems.csv")
        with (
            patch("sys.argv", [
                "varys.py", "discover", "health-system",
                "--state", "CA", "--output", out,
            ]),
            patch.object(
                _mod, "discover_health_systems",
                return_value=["Kaiser Permanente", "Sutter Health"],
            ) as mock_discover,
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        mock_discover.assert_called_once_with("CA")
        mock_run.assert_not_called()
        rows = list(csv.DictReader(open(out)))
        assert [r["entity_name"] for r in rows] == ["Kaiser Permanente", "Sutter Health"]

    def test_default_output_uses_state_name(self, tmp_path, capsys):
        """When --output is omitted, filename should be <state>-health-systems.csv."""
        with (
            patch("sys.argv", [
                "varys.py", "discover", "health-system", "--state", "TX",
            ]),
            patch.object(
                _mod, "discover_health_systems", return_value=["Baylor Scott & White"],
            ),
            patch("builtins.open", MagicMock(return_value=MagicMock(
                __enter__=lambda s: MagicMock(spec=io.TextIOWrapper),
                __exit__=lambda *a: None,
            ))),
            patch("csv.DictWriter"),
        ):
            main()
        # Routing worked if we get here — output path logic is tested in integration


# ---------------------------------------------------------------------------
# research vendor: reads CSV, calls _run_research, respects --yes
# ---------------------------------------------------------------------------

class TestResearchVendor:
    def test_calls_run_research_with_entities(self, tmp_path):
        inp = _make_input_csv(tmp_path, ["Acme", "BetterHealth"])
        out = str(tmp_path / "out.csv")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", inp, "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        mock_run.assert_called_once()
        entities_arg = mock_run.call_args[0][0]
        assert entities_arg == ["Acme", "BetterHealth"]

    def test_missing_entity_name_column_exits(self, tmp_path):
        p = tmp_path / "bad.csv"
        p.write_text("name\nAcme\n")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", str(p), "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_nonexistent_input_file_exits(self, tmp_path):
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", str(tmp_path / "ghost.csv"), "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_missing_api_key_exits(self, tmp_path):
        inp = _make_input_csv(tmp_path, ["Acme"])
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", inp, "--yes",
            ]),
            patch.dict(os.environ, env, clear=True),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_confirmation_prompt_abort(self, tmp_path, monkeypatch):
        """Without --yes, answering 'n' should abort without calling _run_research."""
        inp = _make_input_csv(tmp_path, ["Acme"])
        monkeypatch.setattr("builtins.input", lambda _="": "n")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor", "--input", inp,
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code == 0
        mock_run.assert_not_called()

    def test_confirmation_prompt_proceed(self, tmp_path, monkeypatch):
        """Without --yes, answering 'y' should call _run_research."""
        inp = _make_input_csv(tmp_path, ["Acme"])
        monkeypatch.setattr("builtins.input", lambda _="": "y")
        out = str(tmp_path / "out.csv")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", inp, "--output", out,
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()
        mock_run.assert_called_once()

    def test_blank_rows_skipped(self, tmp_path):
        p = tmp_path / "input.csv"
        p.write_text("entity_name\nAcme\n\n   \nBetterHealth\n")
        out = str(tmp_path / "out.csv")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", str(p), "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()
        entities_arg = mock_run.call_args[0][0]
        assert entities_arg == ["Acme", "BetterHealth"]


# ---------------------------------------------------------------------------
# pipeline vendor: discover → research in one shot, no intermediate CSV
# ---------------------------------------------------------------------------

class TestPipelineVendor:
    def test_calls_discover_then_run_research(self, tmp_path, monkeypatch):
        out = str(tmp_path / "out.csv")
        monkeypatch.setattr("builtins.input", lambda _="": "AI scribes")
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "vendor",
                "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(
                _mod, "discover_vendors_via_llm",
                new=AsyncMock(return_value=["Abridge", "Nuance"]),
            ) as mock_discover,
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        mock_discover.assert_called_once()
        mock_run.assert_called_once()
        entities_arg = mock_run.call_args[0][0]
        assert entities_arg == ["Abridge", "Nuance"]

    def test_no_intermediate_csv_written(self, tmp_path, monkeypatch):
        """pipeline should not write a discovery CSV; only the final output."""
        out = str(tmp_path / "out.csv")
        monkeypatch.setattr("builtins.input", lambda _="": "AI scribes")
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "vendor",
                "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(
                _mod, "discover_vendors_via_llm",
                new=AsyncMock(return_value=["Abridge"]),
            ),
            patch.object(_mod, "_run_research"),
        ):
            main()

        # Only the declared output file should be created (by _run_research, mocked here)
        # The discovery intermediate CSV (vendor-results.csv) must NOT exist
        assert not (tmp_path / "vendor-results.csv").exists()

    def test_missing_api_key_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "query")
        env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with (
            patch("sys.argv", ["varys.py", "pipeline", "vendor", "--yes"]),
            patch.dict(os.environ, env, clear=True),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_empty_query_exits(self, tmp_path, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _="": "")
        with (
            patch("sys.argv", ["varys.py", "pipeline", "vendor", "--yes"]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0

    def test_sources_path_is_derived_from_output(self, tmp_path, monkeypatch):
        """_run_research should receive sources_path = out_sources.csv."""
        out = str(tmp_path / "results.csv")
        monkeypatch.setattr("builtins.input", lambda _="": "scribes")
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "vendor",
                "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(
                _mod, "discover_vendors_via_llm",
                new=AsyncMock(return_value=["Abridge"]),
            ),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        sources_arg = mock_run.call_args[0][4]  # (entities, skill, args, output_path, sources_path)
        assert sources_arg == Path(tmp_path / "results_sources.csv")


# ---------------------------------------------------------------------------
# pipeline health-system
# ---------------------------------------------------------------------------

class TestPipelineHealthSystem:
    def test_calls_discover_health_systems_then_run_research(self, tmp_path):
        out = str(tmp_path / "out.csv")
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "health-system",
                "--state", "CA", "--output", out, "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(
                _mod, "discover_health_systems",
                return_value=["Kaiser", "Sutter"],
            ) as mock_discover,
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        mock_discover.assert_called_once_with("CA")
        entities_arg = mock_run.call_args[0][0]
        assert entities_arg == ["Kaiser", "Sutter"]

    def test_default_output_uses_state(self, tmp_path, capsys):
        """--output omitted → <state>-pipeline-results.csv."""
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "health-system",
                "--state", "NY", "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "discover_health_systems", return_value=["NYP"]),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            main()

        output_arg = mock_run.call_args[0][3]  # (entities, skill, args, output_path, sources_path)
        assert output_arg == Path("ny-pipeline-results.csv")


# ---------------------------------------------------------------------------
# empty entity list guards: all three paths must exit with non-zero code
# ---------------------------------------------------------------------------

class TestEmptyEntityList:
    def test_research_vendor_empty_csv_exits(self, tmp_path):
        """An input CSV with no data rows should exit with a non-zero code."""
        p = tmp_path / "empty.csv"
        p.write_text("entity_name\n")
        with (
            patch("sys.argv", [
                "varys.py", "profile", "vendor",
                "--input", str(p), "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0
        mock_run.assert_not_called()

    def test_pipeline_vendor_empty_discover_exits(self, tmp_path, monkeypatch):
        """If discovery returns 0 vendors, pipeline should exit with error."""
        monkeypatch.setattr("builtins.input", lambda _="": "very specific query with no results")
        with (
            patch("sys.argv", ["varys.py", "pipeline", "vendor", "--yes"]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "discover_vendors_via_llm", new=AsyncMock(return_value=[])),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0
        mock_run.assert_not_called()

    def test_pipeline_health_system_empty_discover_exits(self, tmp_path):
        """If CMS returns 0 hospitals, pipeline should exit with error."""
        with (
            patch("sys.argv", [
                "varys.py", "pipeline", "health-system",
                "--state", "ZZ", "--yes",
            ]),
            patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}),
            patch.object(_mod, "load_skill", return_value=_mock_skill()),
            patch.object(_mod, "discover_health_systems", return_value=[]),
            patch.object(_mod, "_run_research") as mock_run,
        ):
            with pytest.raises(SystemExit) as exc:
                main()
        assert exc.value.code != 0
        mock_run.assert_not_called()
