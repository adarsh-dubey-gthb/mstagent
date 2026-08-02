import os
from crewai import Agent, Crew, LLM, Process, Task
from crewai.project import CrewBase, agent, crew, task
import litellm
import functools

# --- MONKEY PATCH TO FIX GROQ BUG ---
original_completion = litellm.completion
@functools.wraps(original_completion)
def patched_completion(*args, **kwargs):
    if 'messages' in kwargs:
        for msg in kwargs['messages']:
            msg.pop('cache_breakpoint', None)
    return original_completion(*args, **kwargs)
litellm.completion = patched_completion
# ------------------------------------

@CrewBase
class MstCabsCrew():
    """
    MstCabs crew.

    Agent A and Agent B each use their OWN fixed Groq API key (read directly
    from environment variables at agent-construction time), instead of sharing
    one rotating key. This gives them two separate rate-limit budgets, roughly
    doubling total throughput before either one hits a 429.

    Deliberately does NOT override __init__ — @CrewBase-decorated classes can
    have framework-specific initialization behavior, and reading keys directly
    from os.getenv() inside the @agent methods (the same pattern the original
    code used successfully) avoids any risk of that interaction breaking.

    For the "backup key" scenario (see main.py), set GROQ_API_KEY and
    GROQ_API_KEY_2 to the same backup value in the environment BEFORE
    constructing a fresh MstCabsCrew() instance, then restore them afterward.
    """

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    @agent
    def email_parser_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['email_parser_agent'],
            llm=LLM(model="groq/openai/gpt-oss-120b", api_key=os.getenv("GROQ_API_KEY")),
            verbose=True,
            allow_delegation=False
        )

    @agent
    def email_parser_agent_2(self) -> Agent:
        return Agent(
            config=self.agents_config['email_parser_agent_2'],
            llm=LLM(model="groq/openai/gpt-oss-20b", api_key=os.getenv("GROQ_API_KEY_2")),
            verbose=True,
            allow_delegation=False
        )

    @task
    def extract_booking_task_a(self) -> Task:
        # Both tasks reuse the SAME 'extract_booking_task' prompt from tasks.yaml —
        # no need to duplicate that long prompt. Only the agent (and therefore the
        # model + API key) differs.
        return Task(
            config=self.tasks_config['extract_booking_task'],
            agent=self.email_parser_agent()
        )

    @task
    def extract_booking_task_b(self) -> Task:
        return Task(
            config=self.tasks_config['extract_booking_task'],
            agent=self.email_parser_agent_2()
        )

    @crew
    def crew(self) -> Crew:
        """
        Runs both extraction tasks sequentially. Task B does NOT see task A's
        output — no context is shared between them, so this is a genuine
        independent cross-check. main.py reads BOTH task outputs from
        result.tasks_output and compares them before deciding whether to push
        to Sheets.
        """
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            cache=False
        )