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

    # Tell CrewAI where to find the YAML configurations
    agents_config = 'config/agents.yaml'
    tasks_config = 'config/tasks.yaml'
    
    # Define the Groq LLM
    llm = "groq/llama-3.1-8b-instant"

    @agent
    def email_parser_agent(self) -> Agent:
        return Agent(
            config=self.agents_config['email_parser_agent'],
            llm=self.llm,
            verbose=True,
            allow_delegation=False
        )

    @task
    def extract_booking_task(self) -> Task:
        return Task(
            config=self.tasks_config['extract_booking_task'],
            agent=self.email_parser_agent()
        )

    @crew
    def crew(self) -> Crew:
        """Creates the MstCabs crew"""
        return Crew(
            agents=self.agents, # Automatically grabs all @agent decorated methods
            tasks=self.tasks,   # Automatically grabs all @task decorated methods
            process=Process.sequential,
            verbose=True,
            cache=False
        )
