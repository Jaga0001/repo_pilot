#!/usr/bin/env python
import sys
import warnings

from datetime import datetime

from crewai import Crew

from .crew import SageCrew

warnings.filterwarnings("ignore", category=SyntaxWarning, module="pysbd")

# This main file is intended to be a way for you to run your
# crew locally, so refrain from adding unnecessary logic into this file.
# Replace with inputs you want to test with, it will automatically
# interpolate any tasks and agents information

def run():
    """
    Run the crew.
    """
    # Sample CI/CD log for testing
    sample_log = """
    [ERROR] Build failed: Cannot find module 'react'
    npm ERR! code ERESOLVE
    npm ERR! ERESOLVE unable to resolve dependency tree
    npm ERR! npm ERR! While resolving: app@1.0.0
    npm ERR! Found: react@17.0.2
    npm ERR! node_modules/react
    npm ERR!   react@"^18.0.0" from the root project
    npm ERR! Fix the upstream dependency conflict.
    """
    
    inputs = {
        'log_output': sample_log,
    }

    try:
        SageCrew().crew().kickoff(inputs=inputs)
    except Exception as e:
        raise Exception(f"An error occurred while running the crew: {e}")



if __name__ == "__main__":
    run()
