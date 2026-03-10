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


def _make_skill(name="profile-health-it-vendor"):
    Skill = _mod.Skill
    return Skill(
        name=name, description="", mode="vendor",
        max_tool_rounds=5,
        prompt_template="Research {entity}.",
    )


def _make_discover_skill():
    Skill = _mod.Skill
    return Skill(
        name="discover-health-it-vendor", description="", mode="vendor",
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
        skill.name = "profile-health-it-vendor"
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
        skill.name = "profile-health-it-vendor"
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


class TestLoadSkillYamlErrors:
    def _make_skill_dir(self, tmp_path, content):
        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(content)
        return tmp_path

    def test_empty_yaml_frontmatter_exits_cleanly(self, tmp_path):
        """Empty YAML frontmatter (---\\n---\\nbody) must sys.exit(1) with clear message, not KeyError."""
        base = self._make_skill_dir(tmp_path, "---\n---\nPrompt body here.\n")
        with patch.object(_mod, "__file__", str(base / "healthtech-intel.py")):
            with pytest.raises(SystemExit) as exc:
                _mod.load_skill("test-skill")
        assert exc.value.code != 0

    def test_missing_name_field_exits_cleanly(self, tmp_path):
        """YAML without 'name' key must sys.exit(1) with clear message, not KeyError."""
        base = self._make_skill_dir(tmp_path, "---\ndescription: foo\n---\nPrompt body.\n")
        with patch.object(_mod, "__file__", str(base / "healthtech-intel.py")):
            with pytest.raises(SystemExit) as exc:
                _mod.load_skill("test-skill")
        assert exc.value.code != 0


class TestDiscoverHealthSystems:
    def _mock_urlopen(self, mock_urlopen, csv_content: str):
        mock_resp = MagicMock()
        mock_resp.read.return_value = csv_content.encode("utf-8")
        mock_urlopen.return_value.__enter__ = lambda s: mock_resp
        mock_urlopen.return_value.__exit__ = lambda *a: None

    def test_missing_column_exits_cleanly(self):
        """If CMS CSV lacks 'Hospital Name' column, sys.exit(1) with clear message, not KeyError."""
        cms_data = "FacilityName,State\nSome Hospital,CA\n"
        with patch("urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, cms_data)
            with pytest.raises(SystemExit) as exc:
                _mod.discover_health_systems("CA")
        assert exc.value.code != 0

    def test_missing_state_column_exits_cleanly(self):
        """If CMS CSV lacks 'State' column, sys.exit(1) with clear message."""
        cms_data = "Hospital Name,Province\nSome Hospital,CA\n"
        with patch("urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, cms_data)
            with pytest.raises(SystemExit) as exc:
                _mod.discover_health_systems("CA")
        assert exc.value.code != 0

    def test_returns_filtered_names_for_state(self):
        """Returns only hospitals matching the given state, trimmed."""
        cms_data = (
            "Hospital Name,State\n"
            "Kaiser Permanente,CA\n"
            "Sutter Health,CA\n"
            "Mayo Clinic,MN\n"
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, cms_data)
            result = _mod.discover_health_systems("CA")
        assert result == ["Kaiser Permanente", "Sutter Health"]

    def test_case_insensitive_state_match(self):
        """State matching is case-insensitive."""
        cms_data = "Hospital Name,State\nGeneral Hospital,ca\n"
        with patch("urllib.request.urlopen") as mock_urlopen:
            self._mock_urlopen(mock_urlopen, cms_data)
            result = _mod.discover_health_systems("CA")
        assert result == ["General Hospital"]


class TestBatchPolling:
    def test_first_poll_interval_is_not_one_hour(self):
        """Initial poll interval must be much less than 3600s."""
        import inspect
        src = inspect.getsource(_mod._run_batches_api)
        assert "poll_interval = 3600" not in src, (
            "poll_interval is hardcoded to 3600s — first poll waits 1 full hour"
        )

    def test_poll_interval_is_30_seconds(self):
        """Initial poll interval must be 30 seconds."""
        import inspect
        src = inspect.getsource(_mod._run_batches_api)
        assert "poll_interval = 30" in src

    def test_poll_max_is_300_seconds(self):
        """Poll cap must be 300 seconds (5 minutes)."""
        import inspect
        src = inspect.getsource(_mod._run_batches_api)
        assert "poll_max" in src and "300" in src
