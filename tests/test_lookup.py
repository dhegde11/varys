"""
Unit tests for lookup.py pure Python functions.
No API calls required — all tests run offline.
"""

import json
import sys
import textwrap
from pathlib import Path

import pytest

# Make lookup importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from lookup import (
    SKILL_FIELDS,
    _execute_read_file,
    parse_json_response,
    profile_to_sources_row,
    sources_fieldnames,
    to_clean_row,
)


# ---------------------------------------------------------------------------
# parse_json_response
# ---------------------------------------------------------------------------

class TestParseJsonResponse:
    def test_plain_json(self):
        raw = '{"entity_name": "Acme", "foo": {"value": 1}}'
        data = parse_json_response(raw, "Acme")
        assert data["entity_name"] == "Acme"
        assert data["foo"]["value"] == 1

    def test_markdown_fenced(self):
        raw = "```json\n{\"entity_name\": \"Acme\"}\n```"
        data = parse_json_response(raw, "Acme")
        assert data["entity_name"] == "Acme"

    def test_leading_text_before_json(self):
        raw = "Here is the result:\n{\"entity_name\": \"Acme\", \"x\": 1}"
        data = parse_json_response(raw, "Acme")
        assert data["x"] == 1

    def test_entity_name_overwritten_from_arg(self):
        # entity_name in the JSON is overwritten by the argument
        raw = '{"entity_name": "wrong"}'
        data = parse_json_response(raw, "correct")
        assert data["entity_name"] == "correct"

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parse_json_response("no json here at all", "Acme")

    def test_whitespace_around_json(self):
        raw = "   \n  {\"entity_name\": \"Acme\"}  \n  "
        data = parse_json_response(raw, "Acme")
        assert data["entity_name"] == "Acme"


# ---------------------------------------------------------------------------
# sources_fieldnames
# ---------------------------------------------------------------------------

class TestSourcesFieldnames:
    def test_vendor_fields_expanded(self):
        fields = SKILL_FIELDS["researching-health-it-vendor"]
        names = sources_fieldnames(fields)
        # entity_name appears once at start
        assert names[0] == "entity_name"
        # Each non-entity field expands to 3 columns
        non_entity = fields[1:]
        assert len(names) == 1 + len(non_entity) * 3 + 1  # +1 for research_notes
        # Spot-check a triple
        assert "product_category" in names
        assert "product_category_source" in names
        assert "product_category_confidence" in names
        # research_notes is last
        assert names[-1] == "research_notes"

    def test_health_system_fields_expanded(self):
        fields = SKILL_FIELDS["researching-health-system"]
        names = sources_fieldnames(fields)
        assert "ehr_vendor" in names
        assert "ehr_vendor_source" in names
        assert "ehr_vendor_confidence" in names
        assert names[-1] == "research_notes"


# ---------------------------------------------------------------------------
# profile_to_sources_row
# ---------------------------------------------------------------------------

VENDOR_FIELDS = SKILL_FIELDS["researching-health-it-vendor"]

def _full_vendor_profile():
    return {
        "entity_name": "Acme Health",
        "product_category": {"value": "AI Scribe", "source_url": "https://acme.com", "confidence": "high"},
        "primary_customer": {"value": "Provider", "source_url": "https://acme.com/about", "confidence": "high"},
        "ehr_integrations": {"value": "Epic, Oracle Health", "source_url": None, "confidence": "low"},
        "notable_health_system_customers": {"value": None, "source_url": None, "confidence": "low"},
        "business_model": {"value": "SaaS", "source_url": "https://acme.com/pricing", "confidence": "high"},
        "fda_status": {"value": "Not Required", "source_url": None, "confidence": "low"},
        "clinical_evidence": {"value": False, "source_url": None, "confidence": "low"},
        "funding_stage": {"value": "Series B", "source_url": "https://crunchbase.com/acme", "confidence": "high"},
        "total_funding": {"value": "$45M", "source_url": "https://crunchbase.com/acme", "confidence": "high"},
        "key_investors": {"value": "a16z, General Catalyst", "source_url": None, "confidence": "low"},
        "num_employees": {"value": 120, "source_url": "https://linkedin.com/acme", "confidence": "low"},
        "headquarters": {"value": "San Francisco, CA", "source_url": "https://acme.com", "confidence": "high"},
        "founded_year": {"value": 2019, "source_url": None, "confidence": "low"},
        "research_notes": "Strong primary sources for funding.",
    }


class TestProfileToSourcesRow:
    def test_full_profile_flattened(self):
        data = _full_vendor_profile()
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["entity_name"] == "Acme Health"
        assert row["product_category"] == "AI Scribe"
        assert row["product_category_source"] == "https://acme.com"
        assert row["product_category_confidence"] == "high"

    def test_null_value_becomes_empty_string(self):
        data = _full_vendor_profile()
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["notable_health_system_customers"] == ""
        assert row["notable_health_system_customers_source"] == ""

    def test_boolean_false_becomes_string_false(self):
        data = _full_vendor_profile()
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["clinical_evidence"] == "False"

    def test_integer_becomes_string(self):
        data = _full_vendor_profile()
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["num_employees"] == "120"
        assert row["founded_year"] == "2019"

    def test_missing_field_returns_empty_strings(self):
        data = {"entity_name": "Acme", "research_notes": ""}
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["product_category"] == ""
        assert row["product_category_source"] == ""
        assert row["product_category_confidence"] == ""

    def test_non_dict_field_returns_empty_strings(self):
        data = _full_vendor_profile()
        data["product_category"] = "AI Scribe"  # plain string instead of dict
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["product_category"] == ""
        assert row["product_category_source"] == ""

    def test_research_notes_preserved(self):
        data = _full_vendor_profile()
        row = profile_to_sources_row(data, VENDOR_FIELDS)
        assert row["research_notes"] == "Strong primary sources for funding."


# ---------------------------------------------------------------------------
# to_clean_row
# ---------------------------------------------------------------------------

class TestToCleanRow:
    def test_strips_source_and_confidence_columns(self):
        sources_row = {
            "entity_name": "Acme",
            "product_category": "AI Scribe",
            "product_category_source": "https://acme.com",
            "product_category_confidence": "high",
        }
        clean = to_clean_row(sources_row, VENDOR_FIELDS)
        assert "product_category_source" not in clean
        assert "product_category_confidence" not in clean
        assert clean["product_category"] == "AI Scribe"

    def test_only_declared_fields_present(self):
        sources_row = {
            "entity_name": "Acme",
            "product_category": "AI Scribe",
            "product_category_source": "https://acme.com",
            "product_category_confidence": "high",
            "extra_column": "should be dropped",
        }
        clean = to_clean_row(sources_row, VENDOR_FIELDS)
        assert "extra_column" not in clean
        assert set(clean.keys()) == set(VENDOR_FIELDS)

    def test_missing_fields_default_to_empty_string(self):
        clean = to_clean_row({"entity_name": "Acme"}, VENDOR_FIELDS)
        assert clean["product_category"] == ""
        assert clean["funding_stage"] == ""


# ---------------------------------------------------------------------------
# _execute_read_file
# ---------------------------------------------------------------------------

SKILLS_ROOT = Path(__file__).parent.parent / ".claude" / "skills"


class TestExecuteReadFile:
    def test_valid_skill_reference_file(self):
        result = _execute_read_file(
            ".claude/skills/researching-health-it-vendor/references/field-definitions.md"
        )
        assert "ERROR" not in result
        assert len(result) > 0

    def test_path_outside_whitelist_blocked(self):
        result = _execute_read_file("/etc/passwd")
        assert result.startswith("ERROR")
        assert "restricted" in result.lower() or ".claude/skills" in result

    def test_relative_traversal_blocked(self):
        result = _execute_read_file(".claude/skills/../../lookup.py")
        assert result.startswith("ERROR")

    def test_nonexistent_file_returns_error(self):
        result = _execute_read_file(
            ".claude/skills/researching-health-it-vendor/references/nonexistent.md"
        )
        assert result.startswith("ERROR")
        assert "not found" in result.lower() or "File not found" in result
