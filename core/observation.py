"""Observation system for AURUM Phase 2.

Defines the unified observation schema and provides utilities for
processing, comparing, and storing observations from the environment.

The observation system is the agent's "perception layer" - it transforms
raw browser state into structured, actionable information.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ══════════════════════════════════════════════════════════════
# Observation Schema
# ══════════════════════════════════════════════════════════════

@dataclass
class ElementInfo:
    """Information about an interactive element."""
    selector: str
    text: str = ""
    visible: bool = True
    element_type: str = ""  # button, input, link, etc.
    name: str = ""
    placeholder: str = ""
    href: str = ""  # For links


@dataclass
class Observation:
    """Unified observation schema for the agent's perception layer.
    
    This represents what the agent "sees" at a point in time.
    """
    url: str
    title: str
    visible_text: str
    elements: dict  # {buttons: [], inputs: [], links: []}
    errors: list
    timestamp: float
    screenshot: Optional[str] = None  # Base64 screenshot (optional)
    
    # Derived/computed fields
    observation_id: str = field(default_factory=lambda: "")
    previous_url: Optional[str] = None
    navigation_type: str = "direct"  # direct, click, back, forward, etc.
    
    def __post_init__(self):
        if not self.observation_id:
            self.observation_id = f"obs_{int(self.timestamp * 1000)}"
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "url": self.url,
            "title": self.title,
            "visible_text": self.visible_text,
            "elements": self.elements,
            "errors": self.errors,
            "timestamp": self.timestamp,
            "screenshot": self.screenshot,
            "observation_id": self.observation_id,
            "previous_url": self.previous_url,
            "navigation_type": self.navigation_type,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "Observation":
        """Create from dictionary."""
        return cls(
            url=data.get("url", ""),
            title=data.get("title", ""),
            visible_text=data.get("visible_text", ""),
            elements=data.get("elements", {}),
            errors=data.get("errors", []),
            timestamp=data.get("timestamp", time.time()),
            screenshot=data.get("screenshot"),
            observation_id=data.get("observation_id", ""),
            previous_url=data.get("previous_url"),
            navigation_type=data.get("navigation_type", "direct"),
        )
    
    def get_buttons(self) -> list:
        """Get list of button elements."""
        return self.elements.get("buttons", [])
    
    def get_inputs(self) -> list:
        """Get list of input elements."""
        return self.elements.get("inputs", [])
    
    def get_links(self) -> list:
        """Get list of link elements."""
        return self.elements.get("links", [])
    
    def find_element_by_text(self, text: str, element_type: str = None) -> Optional[dict]:
        """Find an element by its text content."""
        text_lower = text.lower().strip()
        
        if element_type is None or element_type == "button":
            for btn in self.get_buttons():
                if text_lower in btn.get("text", "").lower():
                    return btn
        
        if element_type is None or element_type == "input":
            for inp in self.get_inputs():
                if text_lower in inp.get("placeholder", "").lower():
                    return inp
                if text_lower in inp.get("name", "").lower():
                    return inp
        
        if element_type is None or element_type == "link":
            for link in self.get_links():
                if text_lower in link.get("text", "").lower():
                    return link
        
        return None
    
    def find_element_by_selector(self, selector: str) -> Optional[dict]:
        """Find an element by its selector."""
        selector_lower = selector.lower().strip()
        
        for btn in self.get_buttons():
            if selector_lower in btn.get("selector", "").lower():
                return btn
        
        for inp in self.get_inputs():
            if selector_lower in inp.get("selector", "").lower():
                return inp
        
        for link in self.get_links():
            if selector_lower in link.get("selector", "").lower():
                return link
        
        return None
    
    def has_element(self, selector_or_text: str) -> bool:
        """Check if an element exists (by selector or text)."""
        return self.find_element_by_text(selector_or_text) is not None or \
               self.find_element_by_selector(selector_or_text) is not None
    
    def get_visible_text_snippet(self, max_length: int = 500) -> str:
        """Get a snippet of visible text."""
        text = self.visible_text.strip()
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."
    
    def summarize(self) -> str:
        """Create a human-readable summary of the observation."""
        lines = [
            f"URL: {self.url}",
            f"Title: {self.title}",
            f"Buttons: {len(self.get_buttons())}",
            f"Inputs: {len(self.get_inputs())}",
            f"Links: {len(self.get_links())}",
        ]
        
        if self.errors:
            lines.append(f"Errors: {len(self.errors)}")
        
        # Add first few visible text lines
        text_lines = self.visible_text.strip().split('\n')[:5]
        if text_lines:
            lines.append("Content preview:")
            for line in text_lines:
                line = line.strip()
                if line:
                    lines.append(f"  {line}")
        
        return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# Observation History
# ══════════════════════════════════════════════════════════════

class ObservationHistory:
    """Stores and manages observation history for the current task."""
    
    def __init__(self, max_size: int = 100):
        self.observations: list[Observation] = []
        self.max_size = max_size
    
    def add(self, observation: Observation):
        """Add an observation to history."""
        if self.observations:
            observation.previous_url = self.observations[-1].url
        
        self.observations.append(observation)
        
        # Trim if needed
        if len(self.observations) > self.max_size:
            self.observations = self.observations[-self.max_size:]
    
    def get_latest(self) -> Optional[Observation]:
        """Get the most recent observation."""
        if self.observations:
            return self.observations[-1]
        return None
    
    def get_previous(self) -> Optional[Observation]:
        """Get the second-most recent observation."""
        if len(self.observations) >= 2:
            return self.observations[-2]
        return None
    
    def get_all(self) -> list[Observation]:
        """Get all observations."""
        return list(self.observations)
    
    def get_urls_visited(self) -> list[str]:
        """Get list of all URLs visited."""
        return [obs.url for obs in self.observations]
    
    def has_visited_url(self, url: str) -> bool:
        """Check if we've visited a URL before."""
        return any(obs.url == url for obs in self.observations)
    
    def count_visits_to_domain(self, domain: str) -> int:
        """Count how many times we've visited a domain."""
        count = 0
        for obs in self.observations:
            if domain in obs.url:
                count += 1
        return count
    
    def detect_loop(self, window_size: int = 3) -> bool:
        """Detect if we're stuck in a navigation loop."""
        if len(self.observations) < window_size * 2:
            return False
        
        recent_urls = [obs.url for obs in self.observations[-window_size:]]
        previous_urls = [obs.url for obs in self.observations[-window_size*2:-window_size]]
        
        # Check if recent URLs repeat previous URLs
        return recent_urls == previous_urls
    
    def clear(self):
        """Clear all observations."""
        self.observations = []
    
    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "observations": [obs.to_dict() for obs in self.observations],
            "count": len(self.observations),
            "urls_visited": self.get_urls_visited(),
        }


# ══════════════════════════════════════════════════════════════
# Observation Comparison
# ══════════════════════════════════════════════════════════════

def compare_observations(obs1: Observation, obs2: Observation) -> dict:
    """Compare two observations and return differences.
    
    Returns:
        dict with keys:
            - same_url: bool
            - same_title: bool
            - elements_changed: bool
            - text_changed: bool
            - new_elements: list
            - removed_elements: list
            - similarity_score: float (0-1)
    """
    result = {
        "same_url": obs1.url == obs2.url,
        "same_title": obs1.title == obs2.title,
        "elements_changed": False,
        "text_changed": False,
        "new_elements": [],
        "removed_elements": [],
        "similarity_score": 0.0,
    }
    
    # Compare elements
    obs1_selectors = set()
    obs2_selectors = set()
    
    for btn in obs1.get_buttons():
        obs1_selectors.add(btn.get("selector", ""))
    for inp in obs1.get_inputs():
        obs1_selectors.add(inp.get("selector", ""))
    for link in obs1.get_links():
        obs1_selectors.add(link.get("selector", ""))
    
    for btn in obs2.get_buttons():
        obs2_selectors.add(btn.get("selector", ""))
    for inp in obs2.get_inputs():
        obs2_selectors.add(inp.get("selector", ""))
    for link in obs2.get_links():
        obs2_selectors.add(link.get("selector", ""))
    
    new_elements = obs2_selectors - obs1_selectors
    removed_elements = obs1_selectors - obs2_selectors
    
    if new_elements or removed_elements:
        result["elements_changed"] = True
        result["new_elements"] = list(new_elements)
        result["removed_elements"] = list(removed_elements)
    
    # Compare text (simple similarity)
    text1 = obs1.get_visible_text_snippet(1000)
    text2 = obs2.get_visible_text_snippet(1000)
    
    if text1 != text2:
        result["text_changed"] = True
    
    # Calculate similarity score
    if obs1_selectors or obs2_selectors:
        intersection = obs1_selectors & obs2_selectors
        union = obs1_selectors | obs2_selectors
        jaccard = len(intersection) / len(union) if union else 1.0
    else:
        jaccard = 1.0
    
    result["similarity_score"] = jaccard
    
    return result


# ══════════════════════════════════════════════════════════════
# Observation Utilities
# ══════════════════════════════════════════════════════════════

def extract_key_info(observation: Observation) -> dict:
    """Extract key information from an observation for decision making.
    
    Returns a condensed dict with the most important info.
    """
    return {
        "url": observation.url,
        "title": observation.title,
        "num_buttons": len(observation.get_buttons()),
        "num_inputs": len(observation.get_inputs()),
        "num_links": len(observation.get_links()),
        "has_errors": len(observation.errors) > 0,
        "text_preview": observation.get_visible_text_snippet(200),
    }


def observation_to_prompt_context(observation: Observation) -> str:
    """Convert an observation to a text context for LLM prompts."""
    lines = [
        f"Current URL: {observation.url}",
        f"Page Title: {observation.title}",
        "",
        "=== Interactive Elements ===",
    ]
    
    buttons = observation.get_buttons()
    if buttons:
        lines.append("Buttons:")
        for i, btn in enumerate(buttons[:10], 1):
            text = btn.get("text", "")[:50]
            lines.append(f"  {i}. [{btn.get('selector', '')}] {text}")
        if len(buttons) > 10:
            lines.append(f"  ... and {len(buttons) - 10} more buttons")
    
    inputs = observation.get_inputs()
    if inputs:
        lines.append("Input fields:")
        for i, inp in enumerate(inputs[:10], 1):
            placeholder = inp.get("placeholder", "")[:50]
            lines.append(f"  {i}. [{inp.get('selector', '')}] {placeholder or inp.get('name', '')}")
        if len(inputs) > 10:
            lines.append(f"  ... and {len(inputs) - 10} more inputs")
    
    links = observation.get_links()
    if links:
        lines.append("Links:")
        for i, link in enumerate(links[:15], 1):
            text = link.get("text", "")[:50]
            lines.append(f"  {i}. [{link.get('selector', '')}] {text}")
        if len(links) > 15:
            lines.append(f"  ... and {len(links) - 15} more links")
    
    if observation.errors:
        lines.append("")
        lines.append("=== Errors ===")
        for error in observation.errors[:5]:
            lines.append(f"  - {error}")
    
    lines.append("")
    lines.append("=== Page Content (first 500 chars) ===")
    lines.append(observation.get_visible_text_snippet(500))
    
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
# Global Observation History
# ══════════════════════════════════════════════════════════════

# Global observation history for the current task
_history = ObservationHistory()


def get_observation_history() -> ObservationHistory:
    """Get the global observation history."""
    return _history


def reset_observation_history():
    """Reset the global observation history."""
    _history.clear()


def record_observation(observation: Observation):
    """Record an observation to the global history."""
    _history.add(observation)


def get_current_observation() -> Optional[Observation]:
    """Get the current (most recent) observation."""
    return _history.get_latest()