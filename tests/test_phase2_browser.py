"""Phase 2 Browser Integration Tests.

Tests for the browser automation layer and observation system.
These tests work both with and without Playwright installed.
"""

import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure repo root is on sys.path
REPO_ROOT = str(Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ──────────────────────────────────────────────
# Helper utilities
# ──────────────────────────────────────────────

def _skip_if_no_playwright():
    """Skip test if Playwright is not available."""
    try:
        from core.browser import PLAYWRIGHT_AVAILABLE
        if not PLAYWRIGHT_AVAILABLE:
            pytest.skip("Playwright not installed")
    except ImportError:
        pytest.skip("Browser module not available")


# ══════════════════════════════════════════════════════════════
# SECTION 1: Browser Module Import & Structure Tests
# ══════════════════════════════════════════════════════════════

class TestBrowserModuleStructure:
    """Test that browser module has correct structure."""

    def test_browser_module_importable(self):
        """Browser module should be importable."""
        try:
            import core.browser
            assert hasattr(core.browser, 'browser_open')
            assert hasattr(core.browser, 'browser_click')
            assert hasattr(core.browser, 'browser_type')
            assert hasattr(core.browser, 'browser_extract')
            assert hasattr(core.browser, 'browser_screenshot')
            assert hasattr(core.browser, 'browser_wait')
            assert hasattr(core.browser, 'browser_back')
            assert hasattr(core.browser, 'browser_forward')
        except ImportError:
            pytest.skip("Browser module not available")

    def test_browser_safety_config(self):
        """Browser should have safety configuration."""
        try:
            from core.browser import (
                MAX_NAVIGATION_DEPTH,
                MAX_ACTIONS_PER_PAGE,
                MAX_TOTAL_ACTIONS,
                BLOCKED_ACTIONS,
            )
            assert MAX_NAVIGATION_DEPTH > 0
            assert MAX_ACTIONS_PER_PAGE > 0
            assert MAX_TOTAL_ACTIONS > 0
            assert len(BLOCKED_ACTIONS) > 0
        except ImportError:
            pytest.skip("Browser module not available")

    def test_browser_state_manager(self):
        """BrowserStateManager should track state correctly."""
        try:
            from core.browser import BrowserStateManager, MAX_NAVIGATION_DEPTH
            
            state = BrowserStateManager()
            assert state.navigation_depth == 0
            assert state.total_actions == 0
            
            # Record some actions
            state.record_action("click")
            assert state.total_actions == 1
            assert state.actions_per_page == 1
            
            state.record_navigation("http://example.com")
            assert state.navigation_depth == 1
            assert state.total_actions == 2
            
            # Check limits
            ok, _ = state.check_limits()
            assert ok is True
            
            # Simulate exceeding limits
            state.navigation_depth = MAX_NAVIGATION_DEPTH + 1
            ok, reason = state.check_limits()
            assert ok is False
            assert "navigation depth" in reason.lower()
        except ImportError:
            pytest.skip("Browser module not available")


# ══════════════════════════════════════════════════════════════
# SECTION 2: Observation System Tests
# ══════════════════════════════════════════════════════════════

class TestObservationSystem:
    """Test the observation system."""

    def test_observation_creation(self):
        """Observation should be creatable with required fields."""
        try:
            from core.observation import Observation
            
            obs = Observation(
                url="http://example.com",
                title="Example Page",
                visible_text="Hello World",
                elements={"buttons": [], "inputs": [], "links": []},
                errors=[],
                timestamp=1234567890.0,
            )
            
            assert obs.url == "http://example.com"
            assert obs.title == "Example Page"
            assert obs.visible_text == "Hello World"
            assert obs.observation_id.startswith("obs_")
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_to_dict(self):
        """Observation should serialize to dict correctly."""
        try:
            from core.observation import Observation
            
            obs = Observation(
                url="http://example.com",
                title="Example",
                visible_text="Text",
                elements={"buttons": [], "inputs": [], "links": []},
                errors=[],
                timestamp=1234567890.0,
            )
            
            d = obs.to_dict()
            assert d["url"] == obs.url
            assert d["title"] == obs.title
            assert "observation_id" in d
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_from_dict(self):
        """Observation should deserialize from dict correctly."""
        try:
            from core.observation import Observation
            
            d = {
                "url": "http://example.com",
                "title": "Example",
                "visible_text": "Text",
                "elements": {"buttons": [], "inputs": [], "links": []},
                "errors": [],
                "timestamp": 1234567890.0,
            }
            
            obs = Observation.from_dict(d)
            assert obs.url == d["url"]
            assert obs.title == d["title"]
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_find_element_by_text(self):
        """Should find elements by text content."""
        try:
            from core.observation import Observation
            
            obs = Observation(
                url="http://example.com",
                title="Example",
                visible_text="Text",
                elements={
                    "buttons": [{"text": "Click Me", "selector": "#btn1"}],
                    "inputs": [{"name": "search", "selector": "#search"}],
                    "links": [{"text": "Learn More", "selector": "#link1"}],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            # Find button by text
            btn = obs.find_element_by_text("click me")
            assert btn is not None
            assert btn["selector"] == "#btn1"
            
            # Find link by text
            link = obs.find_element_by_text("learn more")
            assert link is not None
            assert link["selector"] == "#link1"
            
            # Not found
            not_found = obs.find_element_by_text("nonexistent")
            assert not_found is None
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_history(self):
        """ObservationHistory should track observations correctly."""
        try:
            from core.observation import Observation, ObservationHistory
            
            history = ObservationHistory(max_size=10)
            
            obs1 = Observation(
                url="http://example.com",
                title="Example",
                visible_text="Text1",
                elements={"buttons": [], "inputs": [], "links": []},
                errors=[],
                timestamp=1234567890.0,
            )
            history.add(obs1)
            
            assert history.get_latest() == obs1
            assert history.get_previous() is None
            
            obs2 = Observation(
                url="http://example.com/page2",
                title="Page 2",
                visible_text="Text2",
                elements={"buttons": [], "inputs": [], "links": []},
                errors=[],
                timestamp=1234567891.0,
            )
            history.add(obs2)
            
            assert history.get_latest() == obs2
            assert history.get_previous() == obs1
            assert len(history.get_all()) == 2
            assert len(history.get_urls_visited()) == 2
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_history_loop_detection(self):
        """Should detect navigation loops."""
        try:
            from core.observation import Observation, ObservationHistory
            
            history = ObservationHistory(max_size=10)
            
            # Add observations in a loop pattern
            urls = ["a.com", "b.com", "c.com", "a.com", "b.com", "c.com"]
            for i, url in enumerate(urls):
                obs = Observation(
                    url=url,
                    title=f"Page {i}",
                    visible_text=f"Text {i}",
                    elements={"buttons": [], "inputs": [], "links": []},
                    errors=[],
                    timestamp=1234567890.0 + i,
                )
                history.add(obs)
            
            # Should detect loop
            assert history.detect_loop(window_size=3) is True
        except ImportError:
            pytest.skip("Observation module not available")

    def test_compare_observations(self):
        """Should compare observations correctly."""
        try:
            from core.observation import Observation, compare_observations
            
            obs1 = Observation(
                url="http://example.com",
                title="Example",
                visible_text="Hello World",
                elements={
                    "buttons": [{"selector": "#btn1"}],
                    "inputs": [],
                    "links": [],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            obs2 = Observation(
                url="http://example.com",
                title="Example",
                visible_text="Hello World Updated",
                elements={
                    "buttons": [{"selector": "#btn1"}],
                    "inputs": [{"selector": "#input1"}],
                    "links": [],
                },
                errors=[],
                timestamp=1234567891.0,
            )
            
            diff = compare_observations(obs1, obs2)
            assert diff["same_url"] is True
            assert diff["same_title"] is True
            assert diff["elements_changed"] is True
            assert diff["text_changed"] is True
            assert diff["similarity_score"] >= 0.5
        except ImportError:
            pytest.skip("Observation module not available")


# ══════════════════════════════════════════════════════════════
# SECTION 3: Browser Tool Registration Tests
# ══════════════════════════════════════════════════════════════

class TestBrowserToolRegistration:
    """Test that browser tools are properly registered."""

    def test_tool_descriptions_include_browser_tools(self):
        """Tool descriptions should include browser tools."""
        try:
            from tools.tool_registry import get_tool_descriptions
            
            desc = get_tool_descriptions()
            # Browser tools should be mentioned
            assert "browser" in desc.lower() or "Browser" in desc
        except ImportError:
            pytest.skip("Tool registry not available")

    def test_browser_tools_in_tool_descriptions(self):
        """Browser tool descriptions should list all browser tools."""
        try:
            from core.browser import get_browser_tool_descriptions
            
            desc = get_browser_tool_descriptions()
            assert "browser_open" in desc
            assert "browser_click" in desc
            assert "browser_type" in desc
            assert "browser_extract" in desc
            assert "browser_screenshot" in desc
            assert "browser_wait" in desc
            assert "browser_back" in desc
            assert "browser_forward" in desc
        except ImportError:
            pytest.skip("Browser module not available")


# ══════════════════════════════════════════════════════════════
# SECTION 4: Browser Tool Execution Tests (Mocked)
# ══════════════════════════════════════════════════════════════

class TestBrowserToolExecution:
    """Test browser tool execution with mocks."""

    def test_execute_browser_tool_unknown_tool(self):
        """Unknown browser tool should return failure."""
        try:
            from core.browser import execute_browser_tool
            
            result = execute_browser_tool("unknown_browser_tool", {})
            assert result["ok"] is False
            assert "Unknown" in result["error"]
        except ImportError:
            pytest.skip("Browser module not available")

    def test_browser_open_returns_structured_output(self):
        """browser_open should return structured output."""
        try:
            from core.browser import browser_open
            
            # Mock the browser internals
            with patch('core.browser._browser') as mock_browser:
                mock_page = MagicMock()
                mock_page.url = "http://example.com"
                mock_page.title.return_value = "Example"
                mock_page.evaluate.return_value = "Hello World"
                mock_browser.page = mock_page
                mock_browser.ensure_page.return_value = (True, "")
                
                with patch('core.browser._state.check_limits', return_value=(True, "")):
                    with patch('core.browser._state.record_navigation'):
                        with patch('core.browser.PLAYWRIGHT_AVAILABLE', False):
                            # When Playwright not available, should return failure
                            result = browser_open("http://example.com")
                            assert isinstance(result, dict)
                            assert "ok" in result
                            assert "error" in result or "data" in result
        except ImportError:
            pytest.skip("Browser module not available")


# ══════════════════════════════════════════════════════════════
# SECTION 5: Safety & Limits Tests
# ══════════════════════════════════════════════════════════════

class TestBrowserSafety:
    """Test browser safety features."""

    def test_blocked_actions_prevented(self):
        """Blocked actions should be prevented."""
        try:
            from core.browser import BLOCKED_ACTIONS
            
            # These actions should be blocked
            blocked = {"purchase", "checkout", "payment", "buy", "order"}
            assert blocked.issubset(BLOCKED_ACTIONS)
        except ImportError:
            pytest.skip("Browser module not available")

    def test_safety_limits_configured(self):
        """Safety limits should be properly configured."""
        try:
            from core.browser import (
                MAX_NAVIGATION_DEPTH,
                MAX_ACTIONS_PER_PAGE,
                MAX_TOTAL_ACTIONS,
            )
            
            # Limits should be reasonable
            assert 5 <= MAX_NAVIGATION_DEPTH <= 50
            assert 10 <= MAX_ACTIONS_PER_PAGE <= 100
            assert 20 <= MAX_TOTAL_ACTIONS <= 200
        except ImportError:
            pytest.skip("Browser module not available")

    def test_state_manager_detects_limit_exceeded(self):
        """StateManager should detect when limits are exceeded."""
        try:
            from core.browser import BrowserStateManager, MAX_NAVIGATION_DEPTH
            
            state = BrowserStateManager()
            
            # Simulate exceeding navigation depth
            state.navigation_depth = MAX_NAVIGATION_DEPTH + 1
            ok, reason = state.check_limits()
            
            assert ok is False
            assert "navigation" in reason.lower()
        except ImportError:
            pytest.skip("Browser module not available")


# ══════════════════════════════════════════════════════════════
# SECTION 6: Observation Utilities Tests
# ══════════════════════════════════════════════════════════════

class TestObservationUtilities:
    """Test observation utility functions."""

    def test_extract_key_info(self):
        """Should extract key info from observation."""
        try:
            from core.observation import Observation, extract_key_info
            
            obs = Observation(
                url="http://example.com",
                title="Example Page",
                visible_text="Hello World",
                elements={
                    "buttons": [{"selector": "#btn1"}, {"selector": "#btn2"}],
                    "inputs": [{"selector": "#input1"}],
                    "links": [],
                },
                errors=["Error 1"],
                timestamp=1234567890.0,
            )
            
            info = extract_key_info(obs)
            
            assert info["url"] == "http://example.com"
            assert info["title"] == "Example Page"
            assert info["num_buttons"] == 2
            assert info["num_inputs"] == 1
            assert info["num_links"] == 0
            assert info["has_errors"] is True
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_to_prompt_context(self):
        """Should convert observation to prompt context."""
        try:
            from core.observation import Observation, observation_to_prompt_context
            
            obs = Observation(
                url="http://example.com",
                title="Example Page",
                visible_text="Hello World content here",
                elements={
                    "buttons": [{"selector": "#btn1", "text": "Click"}],
                    "inputs": [{"selector": "#input1", "placeholder": "Search..."}],
                    "links": [{"selector": "#link1", "text": "More info"}],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            context = observation_to_prompt_context(obs)
            
            assert "http://example.com" in context
            assert "Example Page" in context
            assert "Buttons:" in context
            assert "Click" in context
            assert "input" in context.lower()  # "Input fields" becomes "input fields"
        except ImportError:
            pytest.skip("Observation module not available")

    def test_observation_summarize(self):
        """Should create human-readable summary."""
        try:
            from core.observation import Observation
            
            obs = Observation(
                url="http://example.com",
                title="Example",
                visible_text="Some content\nMore content",
                elements={
                    "buttons": [{"selector": "#btn1"}],
                    "inputs": [],
                    "links": [{"selector": "#link1"}],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            summary = obs.summarize()
            
            assert "URL:" in summary
            assert "http://example.com" in summary
            assert "Buttons:" in summary
            assert "Links:" in summary
        except ImportError:
            pytest.skip("Observation module not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])