from crewai import Agent, Crew, Process, Task
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
    """MstCabs crew"""

    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'

    # Define the Groq LLM
    llm = "groq/llama-3.3-70b-versatile"

    @agent
    def email_parser_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['email_parser_agent'],
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

    @agent
    def email_parser_agent_2(self) -> Agent:
        return Agent(
            config=self.agents_config['email_parser_agent_2'],
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

    @task
    def extract_booking_task_a(self) -> Task:
        # Both tasks reuse the SAME 'extract_booking_task' prompt from tasks.yaml —
        # no need to duplicate that long prompt. Only the agent differs.
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
        Runs both extraction tasks sequentially (task B does NOT see task A's output —
        no context is shared between them, so this is a genuine independent cross-check,
        just not running in parallel). main.py reads BOTH task outputs from
        result.tasks_output and compares them before deciding whether to push to Sheets.
        """
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
            cache=False
        )