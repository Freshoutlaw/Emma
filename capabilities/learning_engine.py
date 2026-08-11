"""Learning Engine — Comprehensive knowledge acquisition and synthesis.

Enables Emma to learn about topics from their historical origins to present day,
with resource estimation (time, data, storage) and systematic content aggregation.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

from flags.network_gate import NetworkGate
from security.guardian import Guardian


class LearningEstimate:
    """Resource estimates for a learning task."""
    
    def __init__(
        self,
        topic: str,
        estimated_time_hours: float,
        estimated_data_mb: float,
        estimated_storage_mb: float,
        confidence: str,
        rationale: str
    ):
        self.topic = topic
        self.estimated_time_hours = estimated_time_hours
        self.estimated_data_mb = estimated_data_mb
        self.estimated_storage_mb = estimated_storage_mb
        self.confidence = confidence  # "high", "medium", "low"
        self.rationale = rationale
    
    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "estimated_time_hours": self.estimated_time_hours,
            "estimated_data_mb": self.estimated_data_mb,
            "estimated_storage_mb": self.estimated_storage_mb,
            "confidence": self.confidence,
            "rationale": self.rationale
        }


class LearningEngine:
    """Comprehensive learning system for Emma."""
    
    def __init__(
        self,
        guardian: Guardian,
        gate: Optional[NetworkGate] = None,
        timeout: float = 15.0
    ) -> None:
        self.guardian = guardian
        self.gate = gate
        self.timeout = timeout
        
        # Topic complexity mappings for estimation
        self._complexity_factors = {
            "high": ["programming", "development", "engineering", "science", "medicine", "law"],
            "medium": ["marketing", "business", "management", "design", "economics"],
            "low": ["basic", "introduction", "overview", "summary"]
        }
        
        # Historical depth configurations
        self._time_periods = {
            "ancient": (0, 1500),
            "early_modern": (1500, 1800),
            "modern": (1800, 1950),
            "contemporary": (1950, 2000),
            "digital": (2000, 2010),
            "current": (2010, 2026)
        }
    
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }
    
    def _check_gate(self) -> None:
        if self.gate is not None and not self.gate.is_open:
            raise RuntimeError("network egress is closed by the network gate")
    
    def _assess_topic_complexity(self, topic: str) -> str:
        """Assess the complexity level of a topic."""
        topic_lower = topic.lower()
        
        for complexity, keywords in self._complexity_factors.items():
            if any(keyword in topic_lower for keyword in keywords):
                return complexity
        
        return "medium"
    
    def _estimate_learning_resources(self, topic: str) -> LearningEstimate:
        """Estimate time, data, and storage requirements for learning a topic."""
        complexity = self._assess_topic_complexity(topic)
        
        # Base estimates (in hours, MB, MB)
        base_estimates = {
            "high": {"time": 48, "data": 500, "storage": 200},
            "medium": {"time": 24, "data": 250, "storage": 100},
            "low": {"time": 8, "data": 100, "storage": 50}
        }
        
        base = base_estimates[complexity]
        
        # Adjust for multi-word topics (indicates broader scope)
        word_count = len(topic.split())
        multiplier = 1.0 + (word_count - 1) * 0.2
        
        # Adjust for specific keywords indicating comprehensive scope
        comprehensive_keywords = ["everything", "comprehensive", "complete", "entire", "full", "deep"]
        if any(keyword in topic.lower() for keyword in comprehensive_keywords):
            multiplier *= 1.5
        
        estimated_time = base["time"] * multiplier
        estimated_data = base["data"] * multiplier
        estimated_storage = base["storage"] * multiplier
        
        # Determine confidence based on topic specificity
        confidence = "medium"
        if word_count <= 2:
            confidence = "high"
        elif word_count >= 5:
            confidence = "low"
        
        rationale = (
            f"Topic complexity: {complexity}. "
            f"Base estimates adjusted by {multiplier:.1f}x multiplier for scope. "
            f"Word count: {word_count} indicates {'broad' if word_count > 3 else 'focused'} scope."
        )
        
        return LearningEstimate(
            topic=topic,
            estimated_time_hours=round(estimated_time, 1),
            estimated_data_mb=round(estimated_data, 1),
            estimated_storage_mb=round(estimated_storage, 1),
            confidence=confidence,
            rationale=rationale
        )
    
    async def evaluate_learning_task(self, topic: str) -> dict:
        """Evaluate a learning task and provide resource estimates."""
        self.guardian.guard("learning_evaluation", {"topic": topic})
        
        estimate = self._estimate_learning_resources(topic)
        
        # Get quick search results to validate topic existence and scope
        try:
            self._check_gate()
            search_results = await self._quick_topic_search(topic)
            estimate.rationale += f" Found {len(search_results)} initial search results to validate scope."
        except Exception as e:
            estimate.rationale += f" Search validation skipped: {str(e)}"
        
        return estimate.to_dict()
    
    async def _quick_topic_search(self, topic: str, max_results: int = 3) -> list[dict]:
        """Quick search to validate topic and get initial scope."""
        url = f"https://html.duckduckgo.com/html/?q={quote(topic)}"
        async with httpx.AsyncClient(timeout=self.timeout, headers=self._HEADERS, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        results = []
        for result in soup.select("div.result")[:max_results]:
            anchor = result.select_one("a.result__a")
            if not anchor:
                continue
            snippet_el = result.select_one("a.result__snippet")
            results.append({
                "title": anchor.get_text(strip=True),
                "url": anchor.get("href", ""),
                "snippet": snippet_el.get_text(strip=True) if snippet_el else ""
            })
        return results
    
    async def learn_topic(
        self,
        topic: str,
        progress_callback: Optional[callable] = None
    ) -> dict:
        """Comprehensively learn about a topic from its origins to present."""
        self.guardian.guard("learning_task", {"topic": topic})
        self._check_gate()
        
        # Get initial estimate
        estimate = self._estimate_learning_resources(topic)
        
        learning_result = {
            "topic": topic,
            "estimate": estimate.to_dict(),
            "stages_completed": [],
            "knowledge_base": [],
            "summary": "",
            "start_time": datetime.now().isoformat(),
            "end_time": None
        }
        
        try:
            # Stage 1: Historical origins and foundations
            if progress_callback:
                progress_callback("Stage 1: Researching historical origins...", 10)
            
            historical_content = await self._learn_historical_context(topic)
            learning_result["stages_completed"].append("historical_origins")
            learning_result["knowledge_base"].extend(historical_content)
            
            # Stage 2: Evolution and development
            if progress_callback:
                progress_callback("Stage 2: Tracking evolution and development...", 30)
            
            evolution_content = await self._learn_evolution(topic)
            learning_result["stages_completed"].append("evolution_development")
            learning_result["knowledge_base"].extend(evolution_content)
            
            # Stage 3: Modern developments and current state
            if progress_callback:
                progress_callback("Stage 3: Analyzing modern developments...", 60)
            
            modern_content = await self._learn_modern_context(topic)
            learning_result["stages_completed"].append("modern_context")
            learning_result["knowledge_base"].extend(modern_content)
            
            # Stage 4: Synthesis and summary
            if progress_callback:
                progress_callback("Stage 4: Synthesizing knowledge...", 80)
            
            summary = await self._synthesize_knowledge(topic, learning_result["knowledge_base"])
            learning_result["summary"] = summary
            learning_result["stages_completed"].append("synthesis")
            
            if progress_callback:
                progress_callback("Learning complete!", 100)
            
        except Exception as e:
            learning_result["error"] = str(e)
            if progress_callback:
                progress_callback(f"Error during learning: {str(e)}", -1)
        
        learning_result["end_time"] = datetime.now().isoformat()
        return learning_result
    
    async def _learn_historical_context(self, topic: str) -> list[dict]:
        """Research the historical origins and foundations of a topic."""
        queries = [
            f"{topic} history origins",
            f"{topic} foundation beginning",
            f"when was {topic} invented created",
            f"{topic} early development pioneers"
        ]
        
        content = []
        for query in queries:
            try:
                results = await self._quick_topic_search(query, max_results=5)
                for result in results:
                    content.append({
                        "type": "historical",
                        "query": query,
                        "title": result["title"],
                        "url": result["url"],
                        "snippet": result["snippet"]
                    })
            except Exception:
                continue
        
        return content
    
    async def _learn_evolution(self, topic: str) -> list[dict]:
        """Research the evolution and development of a topic over time."""
        queries = [
            f"{topic} evolution timeline",
            f"{topic} development milestones",
            f"{topic} major changes over time",
            f"{topic} progress advancement"
        ]
        
        content = []
        for query in queries:
            try:
                results = await self._quick_topic_search(query, max_results=5)
                for result in results:
                    content.append({
                        "type": "evolution",
                        "query": query,
                        "title": result["title"],
                        "url": result["url"],
                        "snippet": result["snippet"]
                    })
            except Exception:
                continue
        
        return content
    
    async def _learn_modern_context(self, topic: str) -> list[dict]:
        """Research modern developments and current state of a topic."""
        queries = [
            f"{topic} 2024 2025 2026 current state",
            f"{topic} modern developments",
            f"{topic} latest trends",
            f"{topic} future outlook"
        ]
        
        content = []
        for query in queries:
            try:
                results = await self._quick_topic_search(query, max_results=5)
                for result in results:
                    content.append({
                        "type": "modern",
                        "query": query,
                        "title": result["title"],
                        "url": result["url"],
                        "snippet": result["snippet"]
                    })
            except Exception:
                continue
        
        return content
    
    async def _synthesize_knowledge(self, topic: str, knowledge_base: list[dict]) -> str:
        """Synthesize the collected knowledge into a coherent summary."""
        # Group by type
        by_type = {}
        for item in knowledge_base:
            item_type = item.get("type", "general")
            if item_type not in by_type:
                by_type[item_type] = []
            by_type[item_type].append(item)
        
        summary_parts = []
        
        # Historical summary
        if "historical" in by_type:
            historical_items = by_type["historical"][:5]
            summary_parts.append(f"Historical Origins: Found {len(historical_items)} sources about {topic}'s beginnings and foundations.")
        
        # Evolution summary
        if "evolution" in by_type:
            evolution_items = by_type["evolution"][:5]
            summary_parts.append(f"Evolution: Identified {len(evolution_items)} key developments and milestones in {topic}'s progress.")
        
        # Modern summary
        if "modern" in by_type:
            modern_items = by_type["modern"][:5]
            summary_parts.append(f"Current State: Analyzed {len(modern_items)} sources on {topic}'s modern developments and future outlook.")
        
        # Overall summary
        summary_parts.append(f"Total Knowledge Base: {len(knowledge_base)} information sources aggregated from historical origins to present day (2026).")
        
        return " ".join(summary_parts)
