from crewai import Agent, Crew, Process, Task
from crewai.project import CrewBase, agent, crew, task
from crewai.agents.agent_builder.base_agent import BaseAgent
from typing import List
from dotenv import load_dotenv


load_dotenv()

@CrewBase
class SageCrew():
    """Sage crew"""

    agents: List[BaseAgent]
    tasks: List[Task]

    @agent
    def log_analyzer(self) -> Agent:
        return Agent(
            config=self.agents_config['log_analyzer'], # type: ignore[index]
        )

    @agent
    def fix_suggester(self) -> Agent:
        return Agent(
            config=self.agents_config['fix_suggester'], # type: ignore[index]
        )

    @agent
    def pr_creator(self) -> Agent:
        return Agent(
            config=self.agents_config['pr_creator'], # type: ignore[index]
        )

    # To learn more about structured task outputs,
    # task dependencies, and task callbacks, check out the documentation:
    # https://docs.crewai.com/concepts/tasks#overview-of-a-task
    @task
    def analyze_logs_task(self) -> Task:
        return Task(
            config=self.tasks_config['analyze_logs_task'], # type: ignore[index]
        )

    @task
    def suggest_fix_task(self) -> Task:
        return Task(
            config=self.tasks_config['suggest_fix_task'], # type: ignore[index]
        )

    @task
    def create_pr_task(self) -> Task:
        return Task(
            config=self.tasks_config['create_pr_task'], # type: ignore[index]
        )

    @crew
    def crew(self) -> Crew:
        return Crew(
            agents=self.agents,
            tasks=self.tasks, 
            process=Process.sequential,
            verbose=True,
        )

    def run_ci_crew(self, logs: str) -> str:
        """
        Runs the crew to analyze CI logs and suggest a fix
        """
        inputs = {
            'log_output': logs,
            'fix_context': 'Based on the analysis and fix suggestions provided.',
        }
        result = self.crew().kickoff(inputs=inputs)
        return str(result)
