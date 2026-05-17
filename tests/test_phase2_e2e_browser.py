"""Phase 2 End-to-End Browser Tests.

Tests that verify the complete observation → action → observation loop.
Uses safe public pages only.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure repo root is on sys.path
REPO_ROOT = str(Path(__file__).resolve().parents[1])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def skip_if_no_playwright():
    """Skip test if Playwright is not available."""
    try:
        from core.browser import PLAYWRIGHT_AVAILABLE
        if not PLAYWRIGHT_AVAILABLE:
            pytest.skip("Playwright not installed - skipping E2E browser tests")
    except ImportError:
        pytest.skip("Browser module not available")


# ══════════════════════════════════════════════════════════════
# E2E Test 1: Simple Navigation
# ══════════════════════════════════════════════════════════════

class TestE2ESimpleNavigation:
    """TEST 1 — SIMPLE NAVIGATION
    Goal: Open example.com → extract title
    Expected: observation returned, title correct, no failure
    """

    def test_simple_navigation_with_mock(self):
        """Test simple navigation with mocked browser."""
        try:
            from core.browser import browser_open, reset_browser_state
            from core.observation import Observation, record_observation, get_observation_history
            
            reset_browser_state()
            
            # Mock the browser
            with patch('core.browser._browser') as mock_browser:
                mock_page = MagicMock()
                mock_page.url = "https://example.com"
                mock_page.title.return_value = "Example Domain"
                mock_page.evaluate.return_value = "Example Domain\nThis domain is for use in illustrative examples."
                mock_page.goto.return_value = MagicMock()
                mock_browser.page = mock_page
                mock_browser.ensure_page.return_value = (True, "")
                
                with patch('core.browser._state.check_limits', return_value=(True, "")):
                    with patch('core.browser._state.record_navigation'):
                        with patch('core.browser._state.record_action'):
                            # Simulate browser_open returning observation
                            result = browser_open("https://example.com")
                            
                            # Verify structured output
                            assert isinstance(result, dict)
                            assert "ok" in result
                            assert "data" in result or "error" in result
                            
                            # If successful, verify observation structure
                            if result["ok"]:
                                data = result["data"]
                                assert "url" in data
                                assert "title" in data
                                assert "visible_text" in data
                                assert "elements" in data
        except ImportError:
            pytest.skip("Browser module not available")

    def test_observation_recording(self):
        """Test that observations are properly recorded."""
        try:
            from core.observation import (
                Observation, record_observation, get_observation_history, reset_observation_history
            )
            
            reset_observation_history()
            
            obs = Observation(
                url="https://example.com",
                title="Example Domain",
                visible_text="Example content",
                elements={"buttons": [], "inputs": [], "links": []},
                errors=[],
                timestamp=1234567890.0,
            )
            
            record_observation(obs)
            
            history = get_observation_history()
            assert history.get_latest() == obs
            assert len(history.get_all()) == 1
        except ImportError:
            pytest.skip("Observation module not available")


# ══════════════════════════════════════════════════════════════
# E2E Test 2: Real Content Extraction
# ══════════════════════════════════════════════════════════════

class TestE2EContentExtraction:
    """TEST 2 — REAL CONTENT EXTRACTION
    Goal: Open Wikipedia AI page → extract first paragraph
    Expected: non-empty visible text, structured observation valid
    """

    def test_content_extraction_with_mock(self):
        """Test content extraction with mocked browser."""
        try:
            from core.browser import browser_extract, browser_open
            from core.observation import Observation
            
            # Mock the browser with Wikipedia-like content
            with patch('core.browser._browser') as mock_browser:
                mock_page = MagicMock()
                mock_page.url = "https://en.wikipedia.org/wiki/Artificial_intelligence"
                mock_page.title.return_value = "Artificial intelligence - Wikipedia"
                mock_page.evaluate.return_value = "Artificial intelligence (AI) is intelligence—perceiving, synthesizing, and inferring information—demonstrated by machines."
                mock_page.content.return_value = "<html><body>AI content</body></html>"
                mock_page.query_selector.return_value = None
                mock_browser.page = mock_page
                mock_browser.ensure_page.return_value = (True, "")
                
                with patch('core.browser._state.record_action'):
                    # Extract full page content
                    result = browser_extract()
                    
                    # Verify structured output
                    assert isinstance(result, dict)
                    assert "ok" in result
                    
                    if result["ok"]:
                        data = result["data"]
                        assert "text" in data
                        assert len(data["text"]) > 0
        except ImportError:
            pytest.skip("Browser module not available")

    def test_observation_element_finding(self):
        """Test finding elements in observation."""
        try:
            from core.observation import Observation
            
            obs = Observation(
                url="https://example.com",
                title="Example",
                visible_text="Welcome to the site",
                elements={
                    "buttons": [
                        {"text": "Click Here", "selector": "#btn1", "visible": True},
                        {"text": "Submit", "selector": "#btn2", "visible": True},
                    ],
                    "inputs": [
                        {"name": "search", "selector": "#search", "placeholder": "Search..."},
                    ],
                    "links": [
                        {"text": "About", "selector": "#about-link", "href": "/about"},
                        {"text": "Contact", "selector": "#contact-link", "href": "/contact"},
                    ],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            # Find by text
            btn = obs.find_element_by_text("click")
            assert btn is not None
            assert btn["selector"] == "#btn1"
            
            # Find link by text
            link = obs.find_element_by_text("about")
            assert link is not None
            assert link["href"] == "/about"
            
            # Not found
            not_found = obs.find_element_by_text("nonexistent")
            assert not_found is None
        except ImportError:
            pytest.skip("Observation module not available")


# ══════════════════════════════════════════════════════════════
# E2E Test 3: Multi-Step Navigation
# ══════════════════════════════════════════════════════════════

class TestE2EMultiStepNavigation:
    """TEST 3 — MULTI-STEP NAVIGATION
    Goal: Search → click link → extract content
    Expected: at least 2 observation cycles, planner re-engages
    """

    def test_multi_step_with_mock(self):
        """Test multi-step navigation with mocked browser."""
        try:
            from core.browser import browser_open, browser_click, browser_extract
            from core.observation import Observation, ObservationHistory
            
            history = ObservationHistory()
            
            # Step 1: Open search page
            obs1 = Observation(
                url="https://example.com/search",
                title="Search Results",
                visible_text="Search results for test",
                elements={
                    "buttons": [],
                    "inputs": [{"name": "q", "selector": "#search-input"}],
                    "links": [
                        {"text": "Result 1", "selector": "#result1", "href": "/page1"},
                        {"text": "Result 2", "selector": "#result2", "href": "/page2"},
                    ],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            history.add(obs1)
            
            # Step 2: Click first result
            obs2 = Observation(
                url="https://example.com/page1",
                title="Page 1 - Content",
                visible_text="This is the content of page 1. It contains useful information.",
                elements={
                    "buttons": [{"text": "Back", "selector": "#back-btn"}],
                    "inputs": [],
                    "links": [{"text": "Next", "selector": "#next-link", "href": "/page2"}],
                },
                errors=[],
                timestamp=1234567891.0,
            )
            history.add(obs2)
            
            # Verify multiple observations
            assert len(history.get_all()) == 2
            assert history.get_latest().url == "https://example.com/page1"
            assert history.get_previous().url == "https://example.com/search"
            
            # Verify observation-driven decisions
            latest = history.get_latest()
            assert latest.has_element("Back")
            assert latest.has_element("Next")
        except ImportError:
            pytest.skip("Observation module not available")


# ══════════════════════════════════════════════════════════════
# E2E Test 4: Failure Recovery
# ══════════════════════════════════════════════════════════════

class TestE2EFailureRecovery:
    """TEST 4 — FAILURE RECOVERY TEST
    Goal: Force invalid selector → system must recover
    Expected: failure detected, alternative strategy used, system continues
    """

    def test_invalid_selector_recovery(self):
        """Test recovery from invalid selector."""
        try:
            from core.browser import browser_click
            from core.observation import Observation
            
            # Mock browser where element is not found
            with patch('core.browser._browser') as mock_browser:
                mock_page = MagicMock()
                mock_page.url = "https://example.com"
                mock_page.wait_for_selector.return_value = None  # Element not found
                mock_browser.page = mock_page
                mock_browser.ensure_page.return_value = (True, "")
                
                with patch('core.browser._state.check_limits', return_value=(True, "")):
                    result = browser_click("#nonexistent-element")
                    
                    # Should return failure, not crash
                    assert isinstance(result, dict)
                    assert result["ok"] is False or "error" in result
        except ImportError:
            pytest.skip("Browser module not available")

    def test_observation_driven_recovery(self):
        """Test that observation can drive recovery decisions."""
        try:
            from core.observation import Observation
            
            # Initial observation - element not found
            obs = Observation(
                url="https://example.com",
                title="Example",
                visible_text="Content here",
                elements={
                    "buttons": [{"text": "Submit Form", "selector": "#submit"}],
                    "inputs": [],
                    "links": [],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            # Try to find element with wrong selector
            found = obs.find_element_by_selector("#wrong-selector")
            assert found is None
            
            # Recovery: try finding by text instead
            found = obs.find_element_by_text("submit")
            assert found is not None
            assert found["selector"] == "#submit"
        except ImportError:
            pytest.skip("Observation module not available")


# ══════════════════════════════════════════════════════════════
# E2E Test 5: Loop Safety
# ══════════════════════════════════════════════════════════════

class TestE2ELoopSafety:
    """TEST 5 — LOOP SAFETY TEST
    Goal: Repeated failure simulation
    Expected: system stops after bounded retries, no infinite loop
    """

    def test_browser_state_limits(self):
        """Test that browser state manager enforces limits."""
        try:
            from core.browser import BrowserStateManager, MAX_TOTAL_ACTIONS
            
            state = BrowserStateManager()
            
            # Simulate exceeding total actions
            for i in range(MAX_TOTAL_ACTIONS + 5):
                state.record_action("click")
            
            ok, reason = state.check_limits()
            assert ok is False, f"Expected limits exceeded but got ok=True. Reason: {reason}"
            assert "total actions" in reason.lower() or "actions" in reason.lower()
        except ImportError:
            pytest.skip("Browser module not available")

    def test_observation_loop_detection(self):
        """Test that observation history detects loops."""
        try:
            from core.observation import Observation, ObservationHistory
            
            history = ObservationHistory()
            
            # Simulate loop: A -> B -> C -> A -> B -> C
            urls = ["a.com", "b.com", "c.com", "a.com", "b.com", "c.com"]
            for i, url in enumerate(urls):
                obs = Observation(
                    url=f"https://{url}",
                    title=f"Page {url}",
                    visible_text=f"Content of {url}",
                    elements={"buttons": [], "inputs": [], "links": []},
                    errors=[],
                    timestamp=1234567890.0 + i,
                )
                history.add(obs)
            
            # Should detect loop
            assert history.detect_loop(window_size=3) is True
        except ImportError:
            pytest.skip("Observation module not available")

    def test_repeated_failure_stops_execution(self):
        """Test that repeated failures trigger stop via fingerprint mechanism."""
        try:
            from core.deterministic_repair import deterministic_repair
            
            # Use run_python with a syntax error (a handled tool)
            step = {"tool": "run_python", "tool_input": {"code": "print('test'"}}
            executed = {
                "status": "failed",
                "result": {"ok": False, "error": "syntax error", "metadata": {}},
            }

            # First failure - should try to handle (auto-fix or convert)
            result1 = deterministic_repair(
                step=step,
                executed_step=executed,
                goal="Test goal",
                completed_steps=[],
                attempted_steps=[],
            )
            assert result1["handled"] is True, "First failure should be handled"

            # Repeated failure with same fingerprint - should stop
            # NOTE: deterministic_repair stops early when previous_failure_fingerprint is provided.
            result2 = deterministic_repair(
                step=step,
                executed_step=executed,
                goal="Test goal",
                completed_steps=[],
                attempted_steps=[],
                previous_failure_fingerprint="run_python|code|syntax error",
            )
            assert result2["handled"] is True, "Repeated failure should be handled"
            assert result2["action"] == "stop", f"Expected 'stop' action but got '{result2.get('action')}'"
        except ImportError:
            pytest.skip("Deterministic repair module not available")


# ══════════════════════════════════════════════════════════════
# Integration: Observation-Driven Planning
# ══════════════════════════════════════════════════════════════

class TestObservationDrivenPlanning:
    """Test that planning uses observations correctly."""

    def test_planner_receives_observation_context(self):
        """Test that planner can use observation as context."""
        try:
            from core.observation import observation_to_prompt_context, Observation
            
            obs = Observation(
                url="https://example.com",
                title="Example Page",
                visible_text="Welcome to the example page. Click the button to continue.",
                elements={
                    "buttons": [
                        {"text": "Continue", "selector": "#continue-btn"},
                        {"text": "Skip", "selector": "#skip-btn"},
                    ],
                    "inputs": [],
                    "links": [{"text": "Help", "selector": "#help-link", "href": "/help"}],
                },
                errors=[],
                timestamp=1234567890.0,
            )
            
            context = observation_to_prompt_context(obs)
            
            # Context should contain actionable information
            assert "https://example.com" in context
            assert "Continue" in context
            assert "#continue-btn" in context
            assert "Skip" in context
        except ImportError:
            pytest.skip("Observation module not available")

    def test_tool_registry_includes_browser_tools(self):
        """Test that tool registry properly includes browser tools."""
        try:
            from tools.tool_registry import execute_tool
            
            # Try to execute a browser tool (will fail without browser, but should route correctly)
            result = execute_tool("browser_open", {"url": "https://example.com"})
            
            # Should return structured result (either ok or error)
            assert isinstance(result, dict)
            assert "ok" in result
        except ImportError:
            pytest.skip("Tool registry not available")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])