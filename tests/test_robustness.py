"""
Tests for robustness / error-handling behaviour in healthtech-intel.py.
All API calls mocked — no credits needed.
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "healthtech_intel",
    Path(__file__).parent.parent / "healthtech-intel.py",
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

discover_vendors_via_llm = _mod.discover_vendors_via_llm
research_entity_async    = _mod.research_entity_async


def _make_skill(name="researching-health-it-vendor"):
    Skill = _mod.Skill
    return Skill(
        name=name, description="", mode="vendor",
        max_tool_rounds=5,
        prompt_template="Research {entity}.",
    )


def _make_discover_skill():
    Skill = _mod.Skill
    return Skill(
        name="discovering-health-it-competitors", description="", mode="vendor",
        max_tool_rounds=5,
        prompt_template="Find companies for: {query}.",
    )


def _make_response(stop_reason, text=None):
    r = MagicMock()
    r.stop_reason = stop_reason
    r.container = None
    if text:
        tb = MagicMock(); tb.type = "text"; tb.text = text; tb.name = None
        r.content = [tb]
    else:
        r.content = []
    return r


class TestDiscoverVendorsUnknownStopReason:
    def test_unknown_stop_reason_raises_immediately(self):
        """stop_reason other than end_turn/pause_turn must raise immediately, not loop to max_rounds."""
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(
            return_value=_make_response("max_tokens")
        )
        with (
            patch.object(_mod, "AsyncAnthropic", return_value=mock_client),
            patch.object(_mod, "load_skill", return_value=_make_discover_skill()),
        ):
            with pytest.raises(RuntimeError, match="max_tokens"):
                asyncio.run(discover_vendors_via_llm("AI scribes", "test-model"))

        # Must have called create() exactly once — not looped through all rounds
        assert mock_client.messages.create.call_count == 1

    def test_end_turn_still_works(self):
        """Normal end_turn path must still work after the fix."""
        end_response = _make_response("end_turn", text='{"companies": ["Abridge", "Nuance"], "rationale": "Found them."}')
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=end_response)
        with (
            patch.object(_mod, "AsyncAnthropic", return_value=mock_client),
            patch.object(_mod, "load_skill", return_value=_make_discover_skill()),
        ):
            result = asyncio.run(discover_vendors_via_llm("AI scribes", "test-model"))
        assert result == ["Abridge", "Nuance"]

    def test_pause_turn_still_loops(self):
        """pause_turn must still cause a retry (not raise)."""
        pause_response = _make_response("pause_turn")
        pause_response.content = [MagicMock(type="server_tool_use")]
        end_response = _make_response("end_turn", text='{"companies": ["Abridge"], "rationale": "ok"}')
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(side_effect=[pause_response, end_response])
        with (
            patch.object(_mod, "AsyncAnthropic", return_value=mock_client),
            patch.object(_mod, "load_skill", return_value=_make_discover_skill()),
        ):
            result = asyncio.run(discover_vendors_via_llm("AI scribes", "test-model"))
        assert result == ["Abridge"]
        assert mock_client.messages.create.call_count == 2


class TestResearchEntityUnknownStopReason:
    def _make_end_response(self):
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = '{"entity_name": "Acme", "research_notes": "ok"}'
        r = MagicMock()
        r.stop_reason = "end_turn"
        r.content = [text_block]
        r.container = None
        return r

    def _make_unknown_response(self, stop_reason="max_tokens"):
        r = MagicMock()
        r.stop_reason = stop_reason
        r.content = []
        r.container = None
        return r

    def test_unknown_stop_reason_raises_immediately(self):
        """stop_reason other than end_turn/pause_turn must raise on first occurrence."""
        skill = _make_skill()
        client = MagicMock()
        client.messages.create = AsyncMock(
            return_value=self._make_unknown_response("max_tokens")
        )
        with pytest.raises(RuntimeError, match="max_tokens"):
            asyncio.run(research_entity_async(client, "Acme", skill, "test-model"))

        # Must have called create() exactly once — not looped through all rounds
        assert client.messages.create.call_count == 1

    def test_end_turn_still_works(self):
        """Normal end_turn path must still work after the fix."""
        skill = _make_skill()
        client = MagicMock()
        client.messages.create = AsyncMock(return_value=self._make_end_response())
        result = asyncio.run(research_entity_async(client, "Acme", skill, "test-model"))
        assert result["entity_name"] == "Acme"


class TestExtrasactionRaises:
    def test_unexpected_field_in_sources_row_raises(self, tmp_path):
        """
        If _run_research receives a row with an unexpected column it must raise,
        not silently drop it. This guards against field-name typos in callers.
        """
        skill = MagicMock()
        skill.name = "researching-health-it-vendor"
        skill.mode = "vendor"

        async def fake_sequential(entities, skill, model, clean_writer, sources_writer, clean_f, src_f):
            sources_row = {f: "" for f in _mod.sources_fieldnames(_mod.SKILL_FIELDS[skill.name])}
            sources_row["ghost_column"] = "should cause raise"
            sources_writer.writerow(sources_row)  # must raise with extrasaction="raise"
            return (1, 0)

        args = MagicMock()
        args.batch = False
        args.concurrency = 1
        args.model = "model"

        out = tmp_path / "out.csv"
        src = tmp_path / "out_sources.csv"

        with patch.object(_mod, "_run_sequential", new=fake_sequential):
            with pytest.raises(ValueError):
                _mod._run_research(["Acme"], skill, args, out, src)

    def test_unexpected_field_in_clean_row_raises(self, tmp_path):
        """Same guard for the clean (non-sources) writer."""
        skill = MagicMock()
        skill.name = "researching-health-it-vendor"
        skill.mode = "vendor"

        async def fake_sequential(entities, skill, model, clean_writer, sources_writer, clean_f, src_f):
            clean_row = {f: "" for f in _mod.SKILL_FIELDS[skill.name]}
            clean_row["ghost_column"] = "should cause raise"
            clean_writer.writerow(clean_row)
            return (1, 0)

        args = MagicMock()
        args.batch = False
        args.concurrency = 1
        args.model = "model"

        out = tmp_path / "out.csv"
        src = tmp_path / "out_sources.csv"

        with patch.object(_mod, "_run_sequential", new=fake_sequential):
            with pytest.raises(ValueError):
                _mod._run_research(["Acme"], skill, args, out, src)
