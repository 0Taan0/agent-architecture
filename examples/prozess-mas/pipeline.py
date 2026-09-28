from agents.analyzer import ProcessAnalyzerAgent
from agents.critic import CriticAgent
from agents.proposer import ImprovementProposerAgent

PIPELINE = [ProcessAnalyzerAgent, ImprovementProposerAgent, CriticAgent]
