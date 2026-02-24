from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List
from dotenv import load_dotenv


load_dotenv()

@CrewBase
class RepoPilotCrew():
    """RepoPilot crew — analyses CI/CD failures and produces code fixes."""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def error_analyzer(self) -> Agent:
        return Agent(
            config=self.agents_config['error_analyzer'],  # type: ignore[index]
        )

    @agent
    def code_fixer(self) -> Agent:
        return Agent(
            config=self.agents_config['code_fixer'],  # type: ignore[index]
        )

    @task
    def analyze_error_task(self) -> Task:
        return Task(
            config=self.tasks_config['analyze_error_task'],  # type: ignore[index]
        )

    @task
    def fix_code_task(self) -> Task:
        return Task(
            config=self.tasks_config['fix_code_task'],  # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks,
            process=Process.sequential,
            verbose=True,
        )
