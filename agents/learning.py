"""Learning Agent — Handles comprehensive knowledge acquisition tasks."""

from __future__ import annotations

from typing import Any, Optional

from agents.base import BaseAgent, AgentResult
from capabilities.learning_engine import LearningEngine


class LearningAgent(BaseAgent):
    """Agent for learning topics comprehensively from origins to present."""
    
    name = "learning"
    description = "Comprehensive knowledge acquisition from historical origins to present day"
    
    def __init__(self, pipeline: Any) -> None:
        super().__init__(pipeline)
        self.learning_engine = LearningEngine(
            guardian=pipeline.guardian,
            gate=pipeline.network_gate,
            timeout=15.0
        )
    
    async def run(self, request: str) -> AgentResult:
        """Process learning requests."""
        # Check if this is an evaluation request or actual learning
        message_lower = request.lower()
        
        if any(keyword in message_lower for keyword in ["estimate", "evaluate", "how long", "how much", "cost"]):
            # Evaluation request
            try:
                # Extract topic from message
                topic = self._extract_topic(request)
                if not topic:
                    return AgentResult(
                        ok=False,
                        output="I couldn't identify a specific topic to evaluate. Please specify what you'd like me to learn about.",
                        intent="learning",
                        error="no_topic_found"
                    )
                
                estimate = await self.learning_engine.evaluate_learning_task(topic)
                response = self._format_estimate(estimate)
                
                return AgentResult(
                    ok=True,
                    output=response,
                    intent="learning",
                    actions=[{"action": "learning_evaluation", "data": estimate}]
                )
                
            except Exception as e:
                return AgentResult(
                    ok=False,
                    output=f"Error evaluating learning task: {str(e)}",
                    intent="learning",
                    error=str(e)
                )
        else:
            # Actual learning request
            try:
                topic = self._extract_topic(request)
                if not topic:
                    return AgentResult(
                        ok=False,
                        output="I couldn't identify a specific topic to learn. Please specify what you'd like me to learn about.",
                        intent="learning",
                        error="no_topic_found"
                    )
                
                # First provide estimate
                estimate = await self.learning_engine.evaluate_learning_task(topic)
                estimate_response = self._format_estimate(estimate)
                
                # For now, we'll just provide the estimate and a summary
                # In a full implementation, we'd ask for confirmation and proceed
                summary_response = f"{estimate_response}\n\nNote: Full learning process requires user confirmation. Say 'confirm' to proceed with learning about '{topic}'."
                
                return AgentResult(
                    ok=True,
                    output=summary_response,
                    intent="learning",
                    actions=[{"action": "learning_estimate", "data": estimate}]
                )
                
            except Exception as e:
                return AgentResult(
                    ok=False,
                    output=f"Error during learning process: {str(e)}",
                    intent="learning",
                    error=str(e)
                )
    
    def _extract_topic(self, message: str) -> Optional[str]:
        """Extract the topic to learn from the user's message."""
        import re
        
        # Common patterns for learning requests
        patterns = [
            r"learn(?:\s+about)?\s+(.+?)(?:\.|$)",
            r"teach\s+me\s+(?:about\s+)?(.+?)(?:\.|$)",
            r"study\s+(.+?)(?:\.|$)",
            r"research\s+(.+?)(?:\.|$)",
            r"go\s+online\s+and\s+learn\s+(.+?)(?:\.|$)",
            r"everything\s+about\s+(.+?)(?:\.|$)",
        ]
        
        for pattern in patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                topic = match.group(1).strip()
                # Remove trailing words that aren't part of the topic
                topic = re.sub(r"\s+(?:from|up to|until|starting).*$", "", topic, flags=re.IGNORECASE)
                return topic
        
        # If no pattern matches, try to extract after key phrases
        if "learn" in message.lower():
            parts = message.lower().split("learn")
            if len(parts) > 1:
                topic = parts[1].strip()
                topic = re.sub(r"^(?:about|everything about)?\s+", "", topic)
                topic = re.sub(r"\s+(?:from|up to|until|starting).*$", "", topic)
                return topic.strip()
        
        return None
    
    def _format_estimate(self, estimate: dict) -> str:
        """Format the learning estimate for user display."""
        return (
            f"Learning Task Evaluation for '{estimate['topic']}':\n"
            f"• Estimated Time: {estimate['estimated_time_hours']} hours\n"
            f"• Estimated Data Usage: {estimate['estimated_data_mb']} MB\n"
            f"• Estimated Storage: {estimate['estimated_storage_mb']} MB\n"
            f"• Confidence: {estimate['confidence'].upper()}\n"
            f"• Rationale: {estimate['rationale']}"
        )
