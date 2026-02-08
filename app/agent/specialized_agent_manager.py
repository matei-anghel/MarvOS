from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.config import AppConfig
from app.llm.mistral_gateway import MistralGateway
from app.storage.db import Storage


@dataclass
class ClarifyingQuestion:
    """A question to ask the user to better understand their request."""
    question: str
    context: str = ""
    suggested_answers: list[str] = field(default_factory=list)


@dataclass
class ResearchPlan:
    """A structured plan for completing a research-oriented task."""
    task_type: str
    summary: str
    steps: list[dict[str, Any]]
    requirements: list[str]
    estimated_time: str
    potential_issues: list[str]
    agent_id: str | None = None
    conversation_id: str | None = None  # Track conversation for session isolation


@dataclass
class AgentMatch:
    """Information about a matching existing agent."""
    agent_id: str
    name: str
    description: str
    similarity_score: float


class SpecializedAgentManager:
    """Manages specialized Mistral agents for research and task planning.
    
    This orchestrates the workflow:
    1. Check for existing agents
    2. Create specialized agent if needed
    3. Ask clarifying questions
    4. Generate research plan
    5. Execute plan with user approval
    """

    # Task type configurations
    TASK_TEMPLATES = {
        "trip_planning": {
            "name": "Trip Planner",
            "description": "A specialized agent for planning trips, finding flights, hotels, and creating itineraries",
            "instructions": """You are a Trip Planning Specialist. Your job is to help users plan their trips comprehensively.

CAPABILITIES:
- Search for current flight information, hotel availability, and prices
- Analyze images of destinations, hotels, and attractions
- Calculate budgets and compare options
- Create detailed day-by-day itineraries

WORKFLOW:
1. Gather essential trip details (dates, destination, budget, preferences)
2. Research flights, accommodations, and activities
3. Present a structured plan with options
4. Wait for user approval before proceeding to booking

IMPORTANT:
- Always provide multiple options when possible
- Include price ranges and booking deadlines
- Note any visa or travel requirements
- NEVER make actual purchases - only prepare and present options
- Be thorough in your research - check reviews, ratings, and current conditions

When researching:
- Use web search for current prices and availability
- Analyze any images provided by the user
- Consider seasonal factors, local events, and weather""",
        },
        "product_research": {
            "name": "Product Researcher",
            "description": "A specialized agent for researching products, comparing options, and finding best deals",
            "instructions": """You are a Product Research Specialist. Your job is to help users make informed purchasing decisions.

CAPABILITIES:
- Search for product reviews, prices, and availability across retailers
- Analyze product images and specifications
- Compare features, prices, and user ratings
- Find deals, discounts, and alternatives

WORKFLOW:
1. Understand user's needs, budget, and preferences
2. Research products across multiple sources
3. Present comparison with pros/cons
4. Wait for user approval before any purchase-related actions

IMPORTANT:
- Always check recent reviews and ratings
- Compare prices across multiple retailers
- Note warranty, return policies, and shipping costs
- NEVER make actual purchases - only research and recommend
- Be objective - present both positive and negative aspects""",
        },
        "event_planning": {
            "name": "Event Planner",
            "description": "A specialized agent for planning events, finding venues, and coordinating logistics",
            "instructions": """You are an Event Planning Specialist. Your job is to help users plan events of all sizes.

CAPABILITIES:
- Search for venues, vendors, and services
- Calculate budgets and coordinate timelines
- Find local regulations and requirements
- Research entertainment and catering options

WORKFLOW:
1. Gather event details (type, date, guest count, budget, location)
2. Research venues, vendors, and services
3. Present options with availability and pricing
4. Create timeline and checklist
5. Wait for user approval before booking anything

IMPORTANT:
- Always check venue availability for the specific date
- Note deposit requirements and cancellation policies
- Consider logistics (parking, accessibility, catering)
- NEVER make actual bookings - only research and present options
- Include backup options for critical elements""",
        },
        "general_research": {
            "name": "Research Assistant",
            "description": "A general-purpose research agent for gathering information on any topic",
            "instructions": """You are a Research Specialist. Your job is to help users gather comprehensive information on any topic.

CAPABILITIES:
- Search the web for current, accurate information
- Analyze images, documents, and data
- Synthesize information from multiple sources
- Provide summaries, comparisons, and recommendations

WORKFLOW:
1. Understand the research question or topic
2. Ask clarifying questions to focus the research
3. Conduct thorough research using available tools
4. Present findings in a structured, easy-to-understand format
5. Cite sources and note confidence levels

IMPORTANT:
- Always verify information from multiple sources when possible
- Distinguish between facts and opinions
- Note the date of information (especially for rapidly changing topics)
- Be transparent about limitations or uncertainties
- Provide actionable next steps based on findings""",
        },
    }

    def __init__(
        self,
        *,
        config: AppConfig,
        mistral: MistralGateway,
        storage: Storage,
        session_id: str | None = None,
    ) -> None:
        self._config = config
        self._mistral = mistral
        self._storage = storage
        self._session_id = session_id

    def _get_agent_storage_key(self, task_type: str) -> str:
        """Get the storage key for an agent.

        Agents are reusable across sessions; conversation IDs remain session-scoped.
        This prevents creating duplicate cloud agents every time the UI/session restarts.
        """
        return f"agent_{task_type}_id"

    def _get_conversation_storage_key(self, task_type: str) -> str:
        """Get the storage key for a conversation, scoped to session if available."""
        if self._session_id:
            return f"session_{self._session_id}_conversation_{task_type}_id"
        return f"conversation_{task_type}_id"

    async def find_or_create_agent(
        self,
        task_type: str,
    ) -> tuple[str, bool]:
        """Find an existing agent or create a new one for the task type.
        
        Returns:
            Tuple of (agent_id, was_created)
        """
        import logging
        log = logging.getLogger(__name__)
        
        # Check if we have a stored agent for this task type (global)
        storage_key = self._get_agent_storage_key(task_type)
        stored_agent_id = self._storage.get_kv(storage_key)
        if stored_agent_id:
            # Best-effort verify the agent still exists.
            # If verification fails (transient API issues), keep using the stored agent.
            try:
                agents = await self._mistral.list_cloud_agents()
                if any(a["id"] == stored_agent_id for a in agents):
                    log.info(f"[Agent Manager] Found existing agent {stored_agent_id} for {task_type}")
                    return stored_agent_id, False
                # Agent was deleted from Mistral dashboard - clear stale IDs
                log.warning(f"[Agent Manager] Stored agent {stored_agent_id} no longer exists for {task_type}, clearing stale IDs")
                self._storage.delete_kv(storage_key)
                # Also clear conversation ID since it's tied to the deleted agent
                conversation_key = self._get_conversation_storage_key(task_type)
                self._storage.delete_kv(conversation_key)
            except Exception as e:
                log.warning(f"[Agent Manager] Failed to verify agent {stored_agent_id}: {e}, using stored ID")
                return stored_agent_id, False
        # Get template for this task type
        template = self.TASK_TEMPLATES.get(task_type, self.TASK_TEMPLATES["general_research"])

        # Prefer reusing an existing cloud agent with the same name/description.
        try:
            existing_id = await self._find_existing_agent_id(template_name=template["name"], template_desc=template["description"])
            if existing_id:
                log.info(f"[Agent Manager] Found existing agent by name '{template['name']}': {existing_id}")
                self._storage.put_kv(storage_key, existing_id)
                return existing_id, False
            log.info(f"[Agent Manager] No existing agent found with name '{template['name']}', will create new one")
        except Exception as e:
            # If listing agents fails, we can't reuse by search; continue to creation.
            log.warning(f"[Agent Manager] Failed to search for existing agent: {e}")
        
        # Create new agent with capabilities
        log.info(f"[Agent Manager] Creating new {task_type} agent with model {self._config.specialized_agent_model}")
        agent_id = await self._mistral.create_cloud_agent_with_capabilities(
            name=template["name"],
            description=template["description"],
            instructions=template["instructions"],
            model=self._config.specialized_agent_model,
            max_tokens=self._config.specialized_agent_max_tokens,
            capabilities=self._config.specialized_agent_capabilities,
        )
        
        # Store the agent ID (globally, not session-scoped)
        log.info(f"[Agent Manager] Created new agent {agent_id} for {task_type}")
        self._storage.put_kv(storage_key, agent_id)
        
        return agent_id, True

    async def _find_existing_agent_id(self, *, template_name: str, template_desc: str) -> str | None:
        """Find the oldest existing cloud agent matching the template.

        Matching is primarily by name; description is used as a tie-breaker.
        """
        agents = await self._mistral.list_cloud_agents()

        name_norm = (template_name or "").strip().lower()
        desc_norm = (template_desc or "").strip().lower()

        def parse_created_at(v: str) -> datetime:
            s = (v or "").strip()
            if not s:
                return datetime.max
            # Handle ISO with Z
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            try:
                return datetime.fromisoformat(s)
            except Exception:
                return datetime.max

        def matches(a: dict[str, Any]) -> bool:
            an = str(a.get("name") or "").strip().lower()
            if not an:
                return False
            # Exact-name match is best; also allow minor variations.
            if an == name_norm:
                return True
            if name_norm and an.replace("-", " ") == name_norm.replace("-", " "):
                return True
            return False

        candidates = [a for a in agents if matches(a)]
        if not candidates:
            return None

        # Prefer those whose description matches, then oldest by created_at.
        def score(a: dict[str, Any]) -> tuple[int, datetime]:
            ad = str(a.get("description") or "").strip().lower()
            desc_match = 0
            if desc_norm and ad == desc_norm:
                desc_match = 0
            elif desc_norm and desc_norm and desc_norm in ad:
                desc_match = 1
            else:
                desc_match = 2
            return (desc_match, parse_created_at(str(a.get("created_at") or "")))

        best = sorted(candidates, key=score)[0]
        best_id = str(best.get("id") or "").strip()
        return best_id or None

    async def ask_clarifying_questions(
        self,
        task_type: str,
        user_input: str,
        agent_id: str,
    ) -> tuple[list[ClarifyingQuestion], str]:
        """Use the specialized agent to generate clarifying questions.
        
        Returns:
            Tuple of (questions, conversation_id)
        """
        
        prompt = f"""The user wants help with the following request:

"{user_input}"

Before I can create a comprehensive plan, I need to ask 2-4 clarifying questions to better understand their needs. 

Please generate clarifying questions that will help me:
1. Understand the specific requirements and constraints
2. Identify any missing critical information
3. Clarify preferences and priorities
4. Establish timeline and budget if relevant

Return 2-4 clarifying questions.

Preferred output (EASIEST TO PARSE): JSON ONLY, no markdown, no prose:
{{
    "questions": [
        {{"question": "...", "context": "...", "suggested_answers": ["...", "..."]}}
    ]
}}

If you cannot output JSON, then output a simple numbered list of questions (each line must end with a '?').
Be concise but thorough. Focus on the most important unknowns."""

        # Use session-scoped conversation tracking
        conversation_key = self._get_conversation_storage_key(task_type)
        conversation_id = self._storage.get_kv(conversation_key)
        
        result = await self._mistral.chat_with_cloud_agent(
            agent_id=agent_id,
            conversation_id=conversation_id,
            message=prompt,
        )
        
        # Store the conversation ID for this session
        self._storage.put_kv(conversation_key, result.conversation_id)
        
        # Parse the response to extract questions (robustly).
        questions = self._parse_questions_from_response(result.reply_text)

        # If the specialized agent response is unparseable or unhelpful, fall back to deterministic questions.
        # This prevents the main agent from looping with “no questions returned”.
        if len(questions) < 2:
            questions = self._fallback_questions(task_type=task_type, user_input=user_input)
        return questions, result.conversation_id

    def _parse_questions_from_response(self, response: str) -> list[ClarifyingQuestion]:
        """Parse clarifying questions from agent response."""
        text = (response or "").strip()
        if not text:
            return []

        # 1) Try JSON first (preferred).
        json_questions = self._try_parse_questions_json(text)
        if json_questions:
            return json_questions

        # 2) Try structured Q1/Context/Suggested answers format.
        questions: list[ClarifyingQuestion] = []
        lines = text.split("\n")

        current_question: str | None = None
        current_context = ""
        current_suggestions: list[str] = []

        q_header_re = re.compile(r"^(?:q\s*\d+|question\s*\d+)\s*[:.)-]\s*(.+)$", re.IGNORECASE)
        numbered_re = re.compile(r"^\d+\s*[.)-]\s*(.+)$")
        bullet_re = re.compile(r"^(?:[-*•])\s+(.+)$")

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            m = q_header_re.match(line)
            if m:
                if current_question:
                    questions.append(
                        ClarifyingQuestion(
                            question=current_question,
                            context=current_context,
                            suggested_answers=current_suggestions,
                        )
                    )
                current_question = m.group(1).strip()
                current_context = ""
                current_suggestions = []
                continue

            if line.lower().startswith("context:"):
                current_context = line.split(":", 1)[1].strip() if ":" in line else ""
                continue
            if line.lower().startswith("suggested answers:") or line.lower().startswith("suggested options:"):
                suggestions_text = line.split(":", 1)[1].strip() if ":" in line else ""
                current_suggestions = [s.strip() for s in suggestions_text.split(",") if s.strip()]
                continue

            # If we didn't get a Q header, accept common question list formats.
            # Only treat as question if it looks like one.
            def looks_like_question(s: str) -> bool:
                s2 = s.strip()
                if not s2:
                    return False
                if s2.endswith("?"):
                    return True
                return bool(re.match(r"^(what|which|when|where|why|how|do|does|did|are|is|can|could|would|should|will)\b", s2, re.IGNORECASE))

            content: str | None = None
            m2 = numbered_re.match(line)
            if m2:
                content = m2.group(1).strip()
            else:
                m3 = bullet_re.match(line)
                if m3:
                    content = m3.group(1).strip()

            if content and looks_like_question(content):
                questions.append(ClarifyingQuestion(question=content))

        if current_question:
            questions.append(
                ClarifyingQuestion(
                    question=current_question,
                    context=current_context,
                    suggested_answers=current_suggestions,
                )
            )

        # Deduplicate while preserving order; cap at 4.
        seen: set[str] = set()
        out: list[ClarifyingQuestion] = []
        for q in questions:
            q_text = (q.question or "").strip()
            if not q_text:
                continue
            key = q_text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
            if len(out) >= 4:
                break
        return out

    def _try_parse_questions_json(self, text: str) -> list[ClarifyingQuestion]:
        """Parse questions from a JSON object/array embedded in the response."""
        # Attempt direct JSON parse.
        for candidate in (text, self._extract_first_json_object(text)):
            if not candidate:
                continue
            try:
                payload = json.loads(candidate)
            except Exception:
                continue

            # Accept {"questions": [...]} or direct list.
            items = None
            if isinstance(payload, dict) and isinstance(payload.get("questions"), list):
                items = payload.get("questions")
            elif isinstance(payload, list):
                items = payload

            if not isinstance(items, list):
                continue

            out: list[ClarifyingQuestion] = []
            for it in items:
                if isinstance(it, str):
                    q = it.strip()
                    if q:
                        out.append(ClarifyingQuestion(question=q))
                elif isinstance(it, dict):
                    q = str(it.get("question") or "").strip()
                    if not q:
                        continue
                    ctx = str(it.get("context") or "").strip()
                    sug = it.get("suggested_answers")
                    if isinstance(sug, list):
                        suggestions = [str(s).strip() for s in sug if str(s).strip()]
                    else:
                        suggestions = []
                    out.append(ClarifyingQuestion(question=q, context=ctx, suggested_answers=suggestions))
                if len(out) >= 4:
                    break

            if len(out) >= 2:
                return out
        return []

    def _extract_first_json_object(self, text: str) -> str | None:
        """Best-effort extraction of the first top-level JSON object/array from free-form text."""
        s = text.strip()
        if not s:
            return None
        # Find first '{' or '[' and try to bracket-match.
        start = None
        for i, ch in enumerate(s):
            if ch in "[{":
                start = i
                break
        if start is None:
            return None

        opening = s[start]
        closing = "}" if opening == "{" else "]"
        depth = 0
        in_str = False
        esc = False
        for j in range(start, len(s)):
            ch = s[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
                continue
            if ch == opening:
                depth += 1
            elif ch == closing:
                depth -= 1
                if depth == 0:
                    return s[start : j + 1]
        return None

    def _fallback_questions(self, *, task_type: str, user_input: str) -> list[ClarifyingQuestion]:
        """Deterministic fallback questions when agent output is empty/unparseable."""
        task_type = task_type or "general_research"
        if task_type == "trip_planning":
            return [
                ClarifyingQuestion(
                    question="What are your exact travel dates (departure and return) for next week?",
                    context="Dates drive flight/hotel availability and pricing.",
                    suggested_answers=["Mon–Fri", "Tue–Sun", "Flexible"],
                ),
                ClarifyingQuestion(
                    question="What’s your approximate budget (all-in or per day) and preferred comfort level?",
                    context="Budget determines hotel area/class and activity choices.",
                    suggested_answers=["Budget", "Mid-range", "Comfort/Luxury"],
                ),
                ClarifyingQuestion(
                    question="Where are you departing from, and do you prefer direct flights over cheaper connections?",
                    context="Departure airport and flight constraints are required to compare options.",
                    suggested_answers=["Direct only", "1 stop ok", "Cheapest"],
                ),
                ClarifyingQuestion(
                    question="What kind of trip do you want (museums, food, nightlife, romantic, family) and any must-sees?",
                    context="Preferences shape the itinerary and neighborhood choice.",
                    suggested_answers=["Museums", "Food", "Romantic", "Nightlife"],
                ),
            ]
        if task_type == "product_research":
            return [
                ClarifyingQuestion(question="What’s your target budget range and must-have features?"),
                ClarifyingQuestion(question="What brands/models are you considering (if any), and what will you use it for?"),
                ClarifyingQuestion(question="Any constraints like size, compatibility, or delivery deadline?"),
            ]
        if task_type == "event_planning":
            return [
                ClarifyingQuestion(question="What’s the event date/time window, city, and approximate guest count?"),
                ClarifyingQuestion(question="What’s your budget and venue style (casual, formal, outdoors, etc.)?"),
                ClarifyingQuestion(question="Do you need catering/AV/entertainment, and any dietary/accessibility needs?"),
            ]
        return [
            ClarifyingQuestion(question="What’s the end goal or deliverable you want (summary, comparison, step-by-step plan)?"),
            ClarifyingQuestion(question="What constraints matter most (budget, time, quality, sources/region)?"),
            ClarifyingQuestion(question="Any preferences or examples of what ‘good’ looks like for this?"),
        ]

    async def generate_research_plan(
        self,
        task_type: str,
        user_input: str,
        answers: dict[str, str],
        agent_id: str,
    ) -> ResearchPlan:
        """Generate a comprehensive research plan using the specialized agent."""
        
        # Build context from answers
        context = "\n".join([f"Q: {q}\nA: {a}" for q, a in answers.items()])
        
        prompt = f"""Based on the user's request and the clarifying questions answered, create a comprehensive research and action plan.

USER REQUEST:
{user_input}

ADDITIONAL CONTEXT:
{context}

Please provide a structured plan including:

1. SUMMARY: A brief overview of what needs to be done

2. STEPS: A numbered list of specific actions to take (be detailed)
   - Research steps (what to search for, where to look)
   - Decision points (what criteria to use)
   - Actions the assistant should prepare for

3. REQUIREMENTS: What information, tools, or resources are needed

4. ESTIMATED TIME: Rough estimate of how long this will take

5. POTENTIAL ISSUES: What could go wrong or what to watch out for

Format your response clearly so it can be parsed into a structured plan."""

        # Use session-scoped conversation tracking
        conversation_key = self._get_conversation_storage_key(task_type)
        conversation_id = self._storage.get_kv(conversation_key)
        
        result = await self._mistral.chat_with_cloud_agent(
            agent_id=agent_id,
            conversation_id=conversation_id,
            message=prompt,
        )
        
        # Store the conversation ID for this session
        self._storage.put_kv(conversation_key, result.conversation_id)
        
        # Parse the plan from the response
        plan = self._parse_plan_from_response(task_type, result.reply_text)
        plan.agent_id = agent_id
        plan.conversation_id = result.conversation_id
        return plan

    def _parse_plan_from_response(self, task_type: str, response: str) -> ResearchPlan:
        """Parse a research plan from agent response."""
        lines = response.strip().split('\n')
        
        summary = ""
        steps = []
        requirements = []
        estimated_time = "Unknown"
        potential_issues = []
        
        current_section = None
        current_step = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Detect section headers
            lower = line.lower()
            if 'summary' in lower and ':' in line:
                current_section = 'summary'
                summary = line.split(':', 1)[1].strip()
                continue
            elif 'steps' in lower or 'action' in lower:
                current_section = 'steps'
                continue
            elif 'requirements' in lower or 'needed' in lower:
                current_section = 'requirements'
                continue
            elif 'time' in lower or 'duration' in lower:
                current_section = 'time'
                estimated_time = line.split(':', 1)[1].strip() if ':' in line else line
                continue
            elif 'issues' in lower or 'risks' in lower or 'watch' in lower:
                current_section = 'issues'
                continue
            
            # Process content based on current section
            if current_section == 'summary' and line:
                summary += " " + line if summary else line
            elif current_section == 'steps':
                # Check if it's a numbered step
                if line[0].isdigit() and '.' in line[:3]:
                    if current_step:
                        steps.append(current_step)
                    step_text = line.split('.', 1)[1].strip() if '.' in line else line
                    current_step = {"number": len(steps) + 1, "description": step_text, "details": []}
                elif current_step and line.startswith('-'):
                    current_step["details"].append(line[1:].strip())
                elif current_step:
                    current_step["description"] += " " + line
            elif current_section == 'requirements' and line.startswith('-'):
                requirements.append(line[1:].strip())
            elif current_section == 'issues' and line.startswith('-'):
                potential_issues.append(line[1:].strip())
        
        # Don't forget the last step
        if current_step:
            steps.append(current_step)
        
        return ResearchPlan(
            task_type=task_type,
            summary=summary or "Research plan",
            steps=steps,
            requirements=requirements,
            estimated_time=estimated_time,
            potential_issues=potential_issues,
        )

    async def execute_research_step(
        self,
        agent_id: str,
        step_description: str,
        task_type: str,
    ) -> dict[str, Any]:
        """Execute a single research step using the specialized agent."""
        
        prompt = f"""Execute the following research step:

{step_description}

Use your capabilities (web search, image analysis, code) to complete this step thoroughly. 
Provide detailed findings including:
- What you discovered
- Specific details (names, prices, dates, etc.)
- Sources or URLs where relevant
- Any decisions made and why

Be comprehensive but concise."""

        # Use session-scoped conversation tracking
        conversation_key = self._get_conversation_storage_key(task_type)
        conversation_id = self._storage.get_kv(conversation_key)
        
        result = await self._mistral.chat_with_cloud_agent(
            agent_id=agent_id,
            conversation_id=conversation_id,
            message=prompt,
        )
        
        # Update the conversation ID for this session
        self._storage.put_kv(conversation_key, result.conversation_id)
        
        return {
            "success": True,
            "findings": result.reply_text,
            "conversation_id": result.conversation_id,
        }

    def detect_task_type(self, user_input: str) -> str:
        """Detect the type of task based on user input."""
        input_lower = user_input.lower()
        
        # Trip planning keywords
        trip_keywords = ['trip', 'travel', 'flight', 'hotel', 'vacation', 'booking', 
                        'itinerary', 'destination', 'tour', 'visit', 'stay']
        if any(kw in input_lower for kw in trip_keywords):
            return "trip_planning"
        
        # Product research keywords
        product_keywords = ['buy', 'purchase', 'product', 'review', 'compare', 'price',
                           'shopping', 'recommend', 'best', 'deal', 'discount']
        if any(kw in input_lower for kw in product_keywords):
            return "product_research"
        
        # Event planning keywords
        event_keywords = ['event', 'party', 'wedding', 'venue', 'catering', 'celebration',
                         'conference', 'meeting', 'gathering']
        if any(kw in input_lower for kw in event_keywords):
            return "event_planning"
        
        # Default to general research
        return "general_research"
